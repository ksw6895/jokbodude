# tasks.py
import os
import tempfile
from pathlib import Path
from celery import Celery, current_task
from celery.signals import worker_ready
from celery.exceptions import Ignore, SoftTimeLimitExceeded
from typing import Optional
import threading
import time
from config import create_model, configure_api, API_KEYS
import logging
from pdf_processor.core.processor import PDFProcessor
from pdf_creator import PDFCreator
from storage_manager import StorageManager
from pdf_processor.pdf.operations import PDFOperations
from celery import group, chord
from pdf_processor.utils.exceptions import CancelledError
from dataclasses import dataclass

@dataclass
class ModeStrategy:
    mode: str                 # "jokbo-centric" | "lesson-centric"
    primary_kind: str         # "jokbo" | "lesson"
    secondary_kind: str       # "lesson" | "jokbo"
    analyze_name: str         # method on PDFProcessor for single-key
    analyze_multi_name: str   # method on PDFProcessor for multi-key
    create_pdf_name: str      # method on PDFCreator
    output_template: str      # e.g. "jokbo_centric_{stem}_all_lessons.pdf"


def _compute_total_chunks(primary_paths: list[str], lesson_paths: list[str]) -> int:
    """Compute total chunks as: number of primaries × sum(chunks across lessons).

    Matches existing behavior in both jokbo- and lesson-centric modes where
    chunking is based on lesson PDFs.
    """
    try:
        prim_count = len(primary_paths)
        lesson_chunks = 0
        for lp in lesson_paths:
            lesson_chunks += len(PDFOperations.split_pdf_for_chunks(lp))
        return max(1, prim_count * max(1, lesson_chunks))
    except Exception:
        return 1


def run_analysis_task(job_id: str, model_type: Optional[str], multi_api: Optional[bool], strategy: ModeStrategy):
    """Generic analysis routine for jokbo/lesson modes using a strategy configuration."""
    storage_manager = StorageManager()
    try:
        # Early cooperative cancel
        try:
            if storage_manager.is_cancelled(job_id):
                storage_manager.update_progress(job_id, 0, "사용자 취소됨")
                current_task.update_state(state='REVOKED', meta={"job_id": job_id, "status": "cancelled"})
                raise Ignore()
        except Exception:
            pass

        metadata = storage_manager.get_job_metadata(job_id)
        if not metadata:
            raise Exception(f"Job metadata not found for {job_id}")
        min_relevance = None
        try:
            if isinstance(metadata, dict):
                mr = metadata.get("min_relevance")
                if mr is not None:
                    min_relevance = int(mr)
        except Exception:
            min_relevance = None

        jokbo_keys = metadata["jokbo_keys"]
        lesson_keys = metadata["lesson_keys"]
        primary_keys = jokbo_keys if strategy.primary_kind == "jokbo" else lesson_keys
        secondary_keys = lesson_keys if strategy.secondary_kind == "lesson" else jokbo_keys

        # Determine multi-API usage
        meta_multi = None
        if isinstance(metadata, dict):
            meta_multi = metadata.get("multi_api")
        use_multi = meta_multi if meta_multi is not None else (multi_api if multi_api is not None else USE_MULTI_API)
        try:
            logger.info(f"{strategy.mode}: use_multi={use_multi}, API_KEYS_count={len(API_KEYS) if isinstance(API_KEYS, list) else 0}")
            if use_multi and (not isinstance(API_KEYS, list) or len(API_KEYS) < 2):
                logger.warning(f"{strategy.mode}: requested multi_api but only 1 key available; falling back to single-key")
        except Exception:
            pass

        # Refresh all TTLs once upfront
        try:
            storage_manager.refresh_ttls(list(jokbo_keys) + list(lesson_keys))
        except Exception:
            pass

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            jokbo_dir = temp_path / "jokbo"
            lesson_dir = temp_path / "lesson"
            output_dir = temp_path / "output"
            jokbo_dir.mkdir(parents=True, exist_ok=True)
            lesson_dir.mkdir(parents=True, exist_ok=True)
            output_dir.mkdir(parents=True, exist_ok=True)

            # Download to local
            jokbo_paths: list[str] = []
            for key in jokbo_keys:
                filename = key.split(":")[-2]
                local_path = jokbo_dir / filename
                try:
                    storage_manager.refresh_ttl(key)
                except Exception:
                    pass
                storage_manager.save_file_locally(key, local_path)
                jokbo_paths.append(str(local_path))

            lesson_paths: list[str] = []
            for key in lesson_keys:
                filename = key.split(":")[-2]
                local_path = lesson_dir / filename
                try:
                    storage_manager.refresh_ttl(key)
                except Exception:
                    pass
                storage_manager.save_file_locally(key, local_path)
                lesson_paths.append(str(local_path))

            primary_paths = jokbo_paths if strategy.primary_kind == "jokbo" else lesson_paths

            # Init chunk-based progress
            try:
                # chunking based on lessons regardless of mode
                total_chunks = _compute_total_chunks(primary_paths, lesson_paths)
                # Add a more descriptive preflight log for debugging
                try:
                    prim_count = len(primary_paths)
                    lesson_chunks = 0
                    for lp in lesson_paths:
                        lesson_chunks += len(PDFOperations.split_pdf_for_chunks(lp))
                    logging.getLogger(__name__).info(
                        f"preflight: primaries={prim_count} × lesson_chunks={lesson_chunks} => total_chunks={total_chunks}"
                    )
                except Exception:
                    pass
                storage_manager.init_progress(job_id, total_chunks, f"총 청크: {total_chunks}")
            except Exception:
                storage_manager.init_progress(job_id, 1, "진행률 초기화")

            # Configure API/model
            configure_api()
            selected_model = model_type or MODEL_TYPE
            model = create_model(selected_model)
            processor = PDFProcessor(model, session_id=job_id)
            if min_relevance is not None:
                try:
                    processor.set_relevance_threshold(min_relevance)
                except Exception:
                    pass
            creator = PDFCreator()

            aggregated_warnings = {"failed_files": [], "failed_chunks": 0}
            for prim_path_str in primary_paths:
                # Cancellation check between items
                try:
                    if storage_manager.is_cancelled(job_id):
                        storage_manager.update_progress(job_id, int((storage_manager.get_progress(job_id) or {}).get('progress', 0) or 0), "사용자 취소됨")
                        current_task.update_state(state='REVOKED', meta={"job_id": job_id, "status": "cancelled"})
                        raise Ignore()
                except Ignore:
                    raise
                except Exception:
                    pass

                prim_path = Path(prim_path_str)
                # Update status message (driven by chunk ticks)
                try:
                    cur = storage_manager.get_progress(job_id) or {}
                    storage_manager.update_progress(job_id, int(cur.get('progress', 0) or 0), f"분석 중: {prim_path.name}")
                except Exception:
                    pass

                # Analyze
                if use_multi and len(API_KEYS) > 1:
                    analysis_result = getattr(processor, strategy.analyze_multi_name)(
                        (lesson_paths if strategy.secondary_kind == "lesson" else jokbo_paths), prim_path_str, api_keys=API_KEYS
                    )
                else:
                    analysis_result = getattr(processor, strategy.analyze_name)(
                        (lesson_paths if strategy.secondary_kind == "lesson" else jokbo_paths), prim_path_str
                    )
                if "error" in analysis_result:
                    raise Exception(f"Analysis error for {prim_path.name}: {analysis_result['error']}")
                try:
                    w = analysis_result.get("warnings") or {}
                    if isinstance(w.get("failed_files"), list):
                        aggregated_warnings["failed_files"].extend([str(x) for x in w.get("failed_files")])
                    if isinstance(w.get("failed_chunks"), int):
                        aggregated_warnings["failed_chunks"] += int(w.get("failed_chunks"))
                except Exception:
                    pass

                # PDF generation message
                try:
                    cur = storage_manager.get_progress(job_id) or {}
                    storage_manager.update_progress(job_id, int(cur.get('progress', 0) or 0), f"PDF 생성 중: {prim_path.name}")
                except Exception:
                    pass

                # Generate output
                output_filename = strategy.output_template.format(stem=prim_path.stem)
                output_path = output_dir / output_filename
                getattr(creator, strategy.create_pdf_name)(
                    str(prim_path), analysis_result, str(output_path), str(lesson_dir if strategy.secondary_kind == "lesson" else jokbo_dir)
                )
                storage_manager.store_result(job_id, output_path)

            try:
                processor.cleanup_session()
            except Exception:
                pass

            try:
                storage_manager.finalize_progress(job_id, "완료")
            except Exception:
                pass

            result_payload = {
                "status": "Complete",
                "job_id": job_id,
                "files_generated": len(list(output_dir.glob("*.pdf")))
            }
            try:
                if aggregated_warnings["failed_files"] or aggregated_warnings["failed_chunks"]:
                    uniq = []
                    seen = set()
                    for f in aggregated_warnings["failed_files"]:
                        name = Path(f).name
                        if name not in seen:
                            seen.add(name)
                            uniq.append(name)
                    result_payload["warnings"] = {
                        "partial": True,
                        "failed_files": uniq,
                        "failed_chunks": int(aggregated_warnings["failed_chunks"]),
                    }
            except Exception:
                pass
            return result_payload

    except CancelledError:
        try:
            storage_manager.update_progress(job_id, int((storage_manager.get_progress(job_id) or {}).get('progress', 0) or 0), "사용자 취소됨")
            storage_manager.finalize_progress(job_id, "취소됨")
        except Exception:
            pass
        current_task.update_state(state='REVOKED', meta={"job_id": job_id, "status": "cancelled"})
        raise Ignore()
    except SoftTimeLimitExceeded:
        try:
            storage_manager.update_progress(job_id, int((storage_manager.get_progress(job_id) or {}).get('progress', 0) or 0), "시간 제한으로 취소됨")
            storage_manager.finalize_progress(job_id, "취소됨")
        except Exception:
            pass
        current_task.update_state(state='REVOKED', meta={"job_id": job_id, "status": "timeout"})
        raise Ignore()
    except Exception as e:
        raise e

# --- Configuration ---
# Ensure temporary files use a persistent or project path instead of /tmp
# Prefer RENDER_STORAGE_PATH when provided (e.g., on Render disks), else project output
try:
    _TMP_BASE = Path(os.getenv("RENDER_STORAGE_PATH", str(Path("output") / "temp" / "tmp")))
    os.environ.setdefault("TMPDIR", str(_TMP_BASE))

    # Use a storage path colocated with TMPDIR for any ad-hoc persistence needs
    STORAGE_PATH = Path(os.getenv("RENDER_STORAGE_PATH", str(Path("output") / "storage")))
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # Ensure storage paths exist
    STORAGE_PATH.mkdir(parents=True, exist_ok=True)
    _TMP_BASE.mkdir(parents=True, exist_ok=True)
except Exception:
    # Fallback to project-local directories if absolute paths are not writable
    _TMP_BASE = Path("output") / "temp" / "tmp"
    os.environ["TMPDIR"] = str(_TMP_BASE)
    STORAGE_PATH = Path("output") / "storage"
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    STORAGE_PATH.mkdir(parents=True, exist_ok=True)
    _TMP_BASE.mkdir(parents=True, exist_ok=True)

# Check if multi-API mode is available
USE_MULTI_API = len(API_KEYS) > 1 if 'API_KEYS' in globals() else False
MODEL_TYPE = os.getenv("GEMINI_MODEL", "flash")  # Defaults to 'flash'; 'pro' uses more tokens
logger = logging.getLogger(__name__)

# --- Celery Initialization with Configuration ---
celery_app = Celery("tasks")
celery_app.config_from_object('celeryconfig')

# Fix deprecated warnings for Celery 6.0
celery_app.conf.broker_connection_retry_on_startup = True
celery_app.conf.worker_cancel_long_running_tasks_on_connection_loss = True

# --- Background cleanup on worker ---
def _prune_path(base: Path, older_than_hours: int | None) -> None:
    """Delete files older than threshold and remove empty dirs under base (best-effort)."""
    if not base.exists():
        return
    now = time.time()
    # Delete files
    for p in base.rglob("*"):
        try:
            if p.is_file():
                if older_than_hours is not None:
                    age_hours = (now - p.stat().st_mtime) / 3600.0
                    if age_hours < older_than_hours:
                        continue
                p.unlink(missing_ok=True)
        except Exception:
            continue
    # Remove empty directories (deepest first)
    for d in sorted(base.rglob("*"), key=lambda x: len(str(x)), reverse=True):
        try:
            if d.is_dir():
                next(d.iterdir())
        except StopIteration:
            try:
                d.rmdir()
            except Exception:
                pass


def _cleanup_once() -> None:
    """One-shot cleanup pass for worker-side storage paths."""
    try:
        # Retention windows
        try:
            debug_hours = int(os.getenv("DEBUG_RETENTION_HOURS", "168"))
        except Exception:
            debug_hours = 168
        try:
            results_hours = int(os.getenv("RESULT_RETENTION_HOURS", "720"))
        except Exception:
            results_hours = 720
        try:
            sessions_hours = int(os.getenv("SESSIONS_RETENTION_HOURS", "168"))
        except Exception:
            sessions_hours = 168

        # Paths to clean
        try:
            sm = StorageManager()
            results_root = getattr(sm, "results_dir", Path(os.getenv("RENDER_STORAGE_PATH", "output")) / "results")
        except Exception:
            results_root = Path(os.getenv("RENDER_STORAGE_PATH", "output")) / "results"
        debug_dir = Path("output/debug")
        sessions_dir = Path("output/temp/sessions")
        tmpdir = Path(os.getenv("TMPDIR", tempfile.gettempdir()))

        _prune_path(debug_dir, debug_hours)
        _prune_path(sessions_dir, sessions_hours)
        _prune_path(results_root, results_hours)
        # Conservative temp pruning
        try:
            tmp_hours = int(os.getenv("TMP_RETENTION_HOURS", "24"))
        except Exception:
            tmp_hours = 24
        _prune_path(tmpdir, tmp_hours)
    except Exception:
        # Never disrupt worker due to cleanup errors
        pass


def _cleanup_loop() -> None:
    """Periodic cleanup loop running in a daemon thread."""
    try:
        try:
            interval_min = max(5, int(os.getenv("WORKER_CLEANUP_INTERVAL_MINUTES", "60")))
        except Exception:
            interval_min = 60
        while True:
            _cleanup_once()
            time.sleep(interval_min * 60)
    except Exception:
        return


def _maybe_start_cleanup_thread() -> None:
    enabled_env = os.getenv("ENABLE_WORKER_CLEANUP", "true").strip().lower()
    if enabled_env not in ("1", "true", "yes", "on"):
        return
    t = threading.Thread(target=_cleanup_loop, name="worker-cleanup", daemon=True)
    t.start()


@worker_ready.connect
def _on_worker_ready(sender=None, **kwargs):
    # Perform an immediate pass and start periodic cleanup
    try:
        _cleanup_once()
    except Exception:
        pass
    _maybe_start_cleanup_thread()

# --- Analysis Tasks ---
@celery_app.task(name="tasks.run_jokbo_analysis")
def run_jokbo_analysis(job_id: str, model_type: str = None, multi_api: Optional[bool] = None):
    """Run jokbo-centric analysis"""
    # Delegate to generic strategy implementation (new path)
    try:
        strategy = ModeStrategy(
            mode="jokbo-centric",
            primary_kind="jokbo",
            secondary_kind="lesson",
            analyze_name="analyze_jokbo_centric",
            analyze_multi_name="analyze_jokbo_centric_multi_api",
            create_pdf_name="create_jokbo_centric_pdf",
            output_template="jokbo_centric_{stem}_all_lessons.pdf",
        )
        return run_analysis_task(job_id, model_type, multi_api, strategy)
    except Exception:
        pass
    # Fallback to legacy flow below if delegation fails
    storage_manager = StorageManager()
    try:
        # Cooperative cancellation early check
        try:
            if storage_manager.is_cancelled(job_id):
                storage_manager.update_progress(job_id, 0, "사용자 취소됨")
                current_task.update_state(state='REVOKED', meta={"job_id": job_id, "status": "cancelled"})
                raise Ignore()
        except Exception:
            pass
        # Get job metadata from Redis
        metadata = storage_manager.get_job_metadata(job_id)
        if not metadata:
            raise Exception(f"Job metadata not found for {job_id}")
        min_relevance = None
        try:
            if isinstance(metadata, dict):
                mr = metadata.get("min_relevance")
                if mr is not None:
                    min_relevance = int(mr)
        except Exception:
            min_relevance = None
        jokbo_keys = metadata["jokbo_keys"]
        lesson_keys = metadata["lesson_keys"]
        
        # Determine multi-API usage
        meta_multi = None
        if isinstance(metadata, dict):
            meta_multi = metadata.get("multi_api")
        use_multi = meta_multi if meta_multi is not None else (multi_api if multi_api is not None else USE_MULTI_API)
        try:
            logger.info(f"run_jokbo_analysis: use_multi={use_multi}, API_KEYS_count={len(API_KEYS) if isinstance(API_KEYS, list) else 0}")
            if use_multi and (not isinstance(API_KEYS, list) or len(API_KEYS) < 2):
                logger.warning("run_jokbo_analysis: requested multi_api but only 1 key available; falling back to single-key")
        except Exception:
            pass

        # Refresh TTLs upfront to avoid expiry during queue delays
        try:
            storage_manager.refresh_ttls(jokbo_keys + lesson_keys)
        except Exception:
            pass

        # Refresh TTLs upfront to avoid expiry during queue delays
        try:
            storage_manager.refresh_ttls(jokbo_keys + lesson_keys)
        except Exception:
            pass

        # Create temporary directory for processing
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            jokbo_dir = temp_path / "jokbo"
            lesson_dir = temp_path / "lesson"
            output_dir = temp_path / "output"
            
            jokbo_dir.mkdir(parents=True, exist_ok=True)
            lesson_dir.mkdir(parents=True, exist_ok=True)
            output_dir.mkdir(parents=True, exist_ok=True)
            
            # Download files from Redis to local temp storage
            jokbo_paths = []
            for key in jokbo_keys:
                filename = key.split(":")[-2]  # Extract filename from key
                local_path = jokbo_dir / filename
                try:
                    storage_manager.refresh_ttl(key)
                except Exception:
                    pass
                storage_manager.save_file_locally(key, local_path)
                jokbo_paths.append(str(local_path))
            
            lesson_paths = []
            for key in lesson_keys:
                filename = key.split(":")[-2]  # Extract filename from key
                local_path = lesson_dir / filename
                try:
                    storage_manager.refresh_ttl(key)
                except Exception:
                    pass
                storage_manager.save_file_locally(key, local_path)
                lesson_paths.append(str(local_path))
            
            # Initialize chunk-based progress
            try:
                total_jokbos = len(jokbo_paths)
                lesson_chunks = 0
                for lp in lesson_paths:
                    lesson_chunks += len(PDFOperations.split_pdf_for_chunks(lp))
                total_chunks = max(1, total_jokbos * lesson_chunks)
                storage_manager.init_progress(job_id, total_chunks, f"총 청크: {total_chunks}")
            except Exception:
                storage_manager.init_progress(job_id, 1, "진행률 초기화")

            # Configure API and create model
            configure_api()
            selected_model = model_type or MODEL_TYPE
            model = create_model(selected_model)
            # PDFProcessor does not take a 'multi_api' arg; choose methods conditionally
            processor = PDFProcessor(model, session_id=job_id)
            if min_relevance is not None:
                try:
                    processor.set_relevance_threshold(min_relevance)
                except Exception:
                    pass
            creator = PDFCreator()
            
            # Process each jokbo file with progress tracking
            total_jokbos = len(jokbo_paths)
            aggregated_warnings = {"failed_files": [], "failed_chunks": 0}
            for idx, jokbo_path_str in enumerate(jokbo_paths, 1):
                # Check cancellation between items
                try:
                    if storage_manager.is_cancelled(job_id):
                        storage_manager.update_progress(job_id, int((storage_manager.get_progress(job_id) or {}).get('progress', 0) or 0), "사용자 취소됨")
                        current_task.update_state(state='REVOKED', meta={"job_id": job_id, "status": "cancelled"})
                        raise Ignore()
                except Ignore:
                    raise
                except Exception:
                    pass
                jokbo_path = Path(jokbo_path_str)
                
                # Update status message (percent driven by chunk progress)
                try:
                    cur = storage_manager.get_progress(job_id) or {}
                    storage_manager.update_progress(job_id, int(cur.get('progress', 0) or 0), f"분석 중: {jokbo_path.name}")
                except Exception:
                    pass
                
                # Analyze jokbo against all lessons (use multi-API when enabled)
                if use_multi and len(API_KEYS) > 1:
                    analysis_result = processor.analyze_jokbo_centric_multi_api(
                        lesson_paths, jokbo_path_str, api_keys=API_KEYS
                    )
                else:
                    analysis_result = processor.analyze_jokbo_centric(lesson_paths, jokbo_path_str)
                
                if "error" in analysis_result:
                    raise Exception(f"Analysis error for {jokbo_path.name}: {analysis_result['error']}")
                # Collect warnings from per-file result
                try:
                    w = analysis_result.get("warnings") or {}
                    if isinstance(w.get("failed_files"), list):
                        aggregated_warnings["failed_files"].extend([str(x) for x in w.get("failed_files")])
                    if isinstance(w.get("failed_chunks"), int):
                        aggregated_warnings["failed_chunks"] += int(w.get("failed_chunks"))
                except Exception:
                    pass
                
                # Update status message for PDF generation
                try:
                    cur = storage_manager.get_progress(job_id) or {}
                    storage_manager.update_progress(job_id, int(cur.get('progress', 0) or 0), f"PDF 생성 중: {jokbo_path.name}")
                except Exception:
                    pass
                
                # Generate output PDF
                output_filename = f"jokbo_centric_{jokbo_path.stem}_all_lessons.pdf"
                output_path = output_dir / output_filename
                
                creator.create_jokbo_centric_pdf(
                    str(jokbo_path),
                    analysis_result,
                    str(output_path),
                    str(lesson_dir)
                )
                
                # Store result in Redis
                storage_manager.store_result(job_id, output_path)
            
            # Clean up processor resources
            processor.cleanup_session()
            
            # Finalize progress to 100% and clamp chunks
            try:
                storage_manager.finalize_progress(job_id, "완료")
            except Exception:
                pass

            result_payload = {
                "status": "Complete",
                "job_id": job_id,
                "files_generated": len(list(output_dir.glob("*.pdf")))
            }
            try:
                if aggregated_warnings["failed_files"] or aggregated_warnings["failed_chunks"]:
                    # Normalize failed_files unique and base names
                    uniq = []
                    seen = set()
                    for f in aggregated_warnings["failed_files"]:
                        name = Path(f).name
                        if name not in seen:
                            seen.add(name)
                            uniq.append(name)
                    result_payload["warnings"] = {
                        "partial": True,
                        "failed_files": uniq,
                        "failed_chunks": int(aggregated_warnings["failed_chunks"]),
                    }
            except Exception:
                pass
            return result_payload
            
    except CancelledError:
        try:
            storage_manager.update_progress(job_id, int((storage_manager.get_progress(job_id) or {}).get('progress', 0) or 0), "사용자 취소됨")
            storage_manager.finalize_progress(job_id, "취소됨")
        except Exception:
            pass
        current_task.update_state(state='REVOKED', meta={"job_id": job_id, "status": "cancelled"})
        raise Ignore()
    except SoftTimeLimitExceeded:
        try:
            storage_manager.update_progress(job_id, int((storage_manager.get_progress(job_id) or {}).get('progress', 0) or 0), "시간 제한으로 취소됨")
            storage_manager.finalize_progress(job_id, "취소됨")
        except Exception:
            pass
        current_task.update_state(state='REVOKED', meta={"job_id": job_id, "status": "timeout"})
        raise Ignore()
    except Exception as e:
        # Celery will catch this exception and store it in the task result backend
        raise e

@celery_app.task(name="tasks.run_lesson_analysis")
def run_lesson_analysis(job_id: str, model_type: str = None, multi_api: Optional[bool] = None):
    """Run lesson-centric analysis"""
    # Delegate to generic strategy implementation (new path)
    try:
        strategy = ModeStrategy(
            mode="lesson-centric",
            primary_kind="lesson",
            secondary_kind="jokbo",
            analyze_name="analyze_lesson_centric",
            analyze_multi_name="analyze_lesson_centric_multi_api",
            create_pdf_name="create_lesson_centric_pdf",
            output_template="filtered_{stem}_all_jokbos.pdf",
        )
        return run_analysis_task(job_id, model_type, multi_api, strategy)
    except Exception:
        pass
    # Fallback to legacy flow below if delegation fails
    storage_manager = StorageManager()
    try:
        # Cooperative cancellation early check
        try:
            if storage_manager.is_cancelled(job_id):
                storage_manager.update_progress(job_id, 0, "사용자 취소됨")
                current_task.update_state(state='REVOKED', meta={"job_id": job_id, "status": "cancelled"})
                raise Ignore()
        except Exception:
            pass
        # Get job metadata from Redis
        metadata = storage_manager.get_job_metadata(job_id)
        if not metadata:
            raise Exception(f"Job metadata not found for {job_id}")
        min_relevance = None
        try:
            if isinstance(metadata, dict):
                mr = metadata.get("min_relevance")
                if mr is not None:
                    min_relevance = int(mr)
        except Exception:
            min_relevance = None
        jokbo_keys = metadata["jokbo_keys"]
        lesson_keys = metadata["lesson_keys"]
        
        # Determine multi-API usage
        meta_multi = None
        if isinstance(metadata, dict):
            meta_multi = metadata.get("multi_api")
        use_multi = meta_multi if meta_multi is not None else (multi_api if multi_api is not None else USE_MULTI_API)
        try:
            logger.info(f"run_lesson_analysis: use_multi={use_multi}, API_KEYS_count={len(API_KEYS) if isinstance(API_KEYS, list) else 0}")
            if use_multi and (not isinstance(API_KEYS, list) or len(API_KEYS) < 2):
                logger.warning("run_lesson_analysis: requested multi_api but only 1 key available; falling back to single-key")
        except Exception:
            pass

        # Create temporary directory for processing
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            jokbo_dir = temp_path / "jokbo"
            lesson_dir = temp_path / "lesson"
            output_dir = temp_path / "output"
            
            jokbo_dir.mkdir(parents=True, exist_ok=True)
            lesson_dir.mkdir(parents=True, exist_ok=True)
            output_dir.mkdir(parents=True, exist_ok=True)
            
            # Download files from Redis to local temp storage
            jokbo_paths = []
            for key in jokbo_keys:
                filename = key.split(":")[-2]  # Extract filename from key
                local_path = jokbo_dir / filename
                try:
                    storage_manager.refresh_ttl(key)
                except Exception:
                    pass
                storage_manager.save_file_locally(key, local_path)
                jokbo_paths.append(str(local_path))
            
            lesson_paths = []
            for key in lesson_keys:
                filename = key.split(":")[-2]  # Extract filename from key
                local_path = lesson_dir / filename
                try:
                    storage_manager.refresh_ttl(key)
                except Exception:
                    pass
                storage_manager.save_file_locally(key, local_path)
                lesson_paths.append(str(local_path))
            
            # Initialize chunk-based progress (lesson-centric)
            try:
                total_lessons = len(lesson_paths)
                total_jokbos = len(jokbo_paths)
                lesson_chunks = 0
                for lp in lesson_paths:
                    lesson_chunks += len(PDFOperations.split_pdf_for_chunks(lp))
                total_chunks = max(1, lesson_chunks * max(1, total_jokbos))
                storage_manager.init_progress(job_id, total_chunks, f"총 청크: {total_chunks}")
            except Exception:
                storage_manager.init_progress(job_id, 1, "진행률 초기화")

            # Configure API and create model
            configure_api()
            selected_model = model_type or MODEL_TYPE
            model = create_model(selected_model)
            # PDFProcessor does not take a 'multi_api' arg; choose methods conditionally
            processor = PDFProcessor(model, session_id=job_id)
            if min_relevance is not None:
                try:
                    processor.set_relevance_threshold(min_relevance)
                except Exception:
                    pass
            creator = PDFCreator()
            
            # Process each lesson file with progress tracking
            total_lessons = len(lesson_paths)
            aggregated_warnings = {"failed_files": [], "failed_chunks": 0}
            for idx, lesson_path_str in enumerate(lesson_paths, 1):
                # Check cancellation between items
                try:
                    if storage_manager.is_cancelled(job_id):
                        storage_manager.update_progress(job_id, int((storage_manager.get_progress(job_id) or {}).get('progress', 0) or 0), "사용자 취소됨")
                        current_task.update_state(state='REVOKED', meta={"job_id": job_id, "status": "cancelled"})
                        raise Ignore()
                except Ignore:
                    raise
                except Exception:
                    pass
                lesson_path = Path(lesson_path_str)
                
                # Update status message (percent driven by chunk progress)
                try:
                    cur = storage_manager.get_progress(job_id) or {}
                    storage_manager.update_progress(job_id, int(cur.get('progress', 0) or 0), f"분석 중: {lesson_path.name}")
                except Exception:
                    pass
                
                # Analyze lesson against all jokbos (use multi-API when enabled)
                if use_multi and len(API_KEYS) > 1:
                    analysis_result = processor.analyze_lesson_centric_multi_api(
                        jokbo_paths, lesson_path_str, api_keys=API_KEYS
                    )
                else:
                    analysis_result = processor.analyze_lesson_centric(jokbo_paths, lesson_path_str)
                
                if "error" in analysis_result:
                    raise Exception(f"Analysis error for {lesson_path.name}: {analysis_result['error']}")
                # Collect warnings from per-file result
                try:
                    w = analysis_result.get("warnings") or {}
                    if isinstance(w.get("failed_files"), list):
                        aggregated_warnings["failed_files"].extend([str(x) for x in w.get("failed_files")])
                    if isinstance(w.get("failed_chunks"), int):
                        aggregated_warnings["failed_chunks"] += int(w.get("failed_chunks"))
                except Exception:
                    pass
                
                # Update status message for PDF generation
                try:
                    cur = storage_manager.get_progress(job_id) or {}
                    storage_manager.update_progress(job_id, int(cur.get('progress', 0) or 0), f"PDF 생성 중: {lesson_path.name}")
                except Exception:
                    pass
                
                # Generate output PDF
                output_filename = f"filtered_{lesson_path.stem}_all_jokbos.pdf"
                output_path = output_dir / output_filename
                
                creator.create_lesson_centric_pdf(
                    str(lesson_path),
                    analysis_result,
                    str(output_path),
                    str(jokbo_dir)
                )
                
                # Store result in Redis
                storage_manager.store_result(job_id, output_path)
            
            # Clean up processor resources
            processor.cleanup_session()
            
            # Finalize progress to 100% and clamp chunks
            try:
                storage_manager.finalize_progress(job_id, "완료")
            except Exception:
                pass
            
            result_payload = {
                "status": "Complete",
                "job_id": job_id,
                "files_generated": len(list(output_dir.glob("*.pdf")))
            }
            try:
                if aggregated_warnings["failed_files"] or aggregated_warnings["failed_chunks"]:
                    uniq = []
                    seen = set()
                    for f in aggregated_warnings["failed_files"]:
                        name = Path(f).name
                        if name not in seen:
                            seen.add(name)
                            uniq.append(name)
                    result_payload["warnings"] = {
                        "partial": True,
                        "failed_files": uniq,
                        "failed_chunks": int(aggregated_warnings["failed_chunks"]),
                    }
            except Exception:
                pass
            return result_payload
            
    except CancelledError:
        try:
            storage_manager.update_progress(job_id, int((storage_manager.get_progress(job_id) or {}).get('progress', 0) or 0), "사용자 취소됨")
            storage_manager.finalize_progress(job_id, "취소됨")
        except Exception:
            pass
        current_task.update_state(state='REVOKED', meta={"job_id": job_id, "status": "cancelled"})
        raise Ignore()
    except SoftTimeLimitExceeded:
        try:
            storage_manager.update_progress(job_id, int((storage_manager.get_progress(job_id) or {}).get('progress', 0) or 0), "시간 제한으로 취소됨")
            storage_manager.finalize_progress(job_id, "취소됨")
        except Exception:
            pass
        current_task.update_state(state='REVOKED', meta={"job_id": job_id, "status": "timeout"})
        raise Ignore()
    except Exception as e:
        # Celery will catch this exception and store it in the task result backend
        raise e


# --- Batch (multi-request in one server call) ---
@celery_app.task(name="tasks.batch_analyze_single")
def batch_analyze_single(
    job_id: str,
    mode: str,
    sub_index: int,
    primary_key: str,
    other_keys: list[str],
    model_type: Optional[str] = None,
    min_relevance: Optional[int] = None,
    multi_api: Optional[bool] = None,
):
    """Execute a single, isolated sub-analysis.

    - mode: 'jokbo-centric' => primary is jokbo, others are lessons
            'lesson-centric' => primary is lesson, others are jokbos
    Each subtask keeps Gemini context strictly to the provided files only.
    """
    storage_manager = StorageManager()
    try:
        # Cooperative cancel check
        try:
            if storage_manager.is_cancelled(job_id):
                storage_manager.update_progress(job_id, int((storage_manager.get_progress(job_id) or {}).get('progress', 0) or 0), "사용자 취소됨")
                current_task.update_state(state='REVOKED', meta={"job_id": job_id, "status": "cancelled"})
                raise Ignore()
        except Ignore:
            raise
        except Exception:
            pass

        # Refresh TTLs upfront
        try:
            storage_manager.refresh_ttls([primary_key] + list(other_keys))
        except Exception:
            pass

        # Configure API and create model
        configure_api()
        selected_model = model_type or MODEL_TYPE
        model = create_model(selected_model)
        processor = PDFProcessor(model, session_id=f"{job_id}:{mode}:{sub_index}")
        if min_relevance is not None:
            try:
                processor.set_relevance_threshold(int(min_relevance))
            except Exception:
                pass
        creator = PDFCreator()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            a_dir = temp_path / ("jokbo" if mode == "jokbo-centric" else "lesson")
            b_dir = temp_path / ("lesson" if mode == "jokbo-centric" else "jokbo")
            out_dir = temp_path / "output"
            a_dir.mkdir(parents=True, exist_ok=True)
            b_dir.mkdir(parents=True, exist_ok=True)
            out_dir.mkdir(parents=True, exist_ok=True)

            # Download primary
            a_name = primary_key.split(":")[-2]
            a_path = a_dir / a_name
            storage_manager.save_file_locally(primary_key, a_path)

            # Download others
            b_paths: list[str] = []
            for k in other_keys:
                name = k.split(":")[-2]
                p = b_dir / name
                storage_manager.save_file_locally(k, p)
                b_paths.append(str(p))

            # Do analysis isolated to this pair-set
            if mode == "jokbo-centric":
                analysis_result = (
                    processor.analyze_jokbo_centric_multi_api(b_paths, str(a_path), api_keys=API_KEYS)
                    if (multi_api and len(API_KEYS) > 1)
                    else processor.analyze_jokbo_centric(b_paths, str(a_path))
                )
                if "error" in analysis_result:
                    raise Exception(f"Analysis error for {a_path.name}: {analysis_result['error']}")
                output_filename = f"jokbo_centric_{a_path.stem}_all_lessons.pdf"
                output_path = out_dir / output_filename
                creator.create_jokbo_centric_pdf(
                    str(a_path), analysis_result, str(output_path), str(b_dir)
                )
            else:
                analysis_result = (
                    processor.analyze_lesson_centric_multi_api(b_paths, str(a_path), api_keys=API_KEYS)
                    if (multi_api and len(API_KEYS) > 1)
                    else processor.analyze_lesson_centric(b_paths, str(a_path))
                )
                if "error" in analysis_result:
                    raise Exception(f"Analysis error for {a_path.name}: {analysis_result['error']}")
                output_filename = f"filtered_{a_path.stem}_all_jokbos.pdf"
                output_path = out_dir / output_filename
                creator.create_lesson_centric_pdf(
                    str(a_path), analysis_result, str(output_path), str(b_dir)
                )

            # Persist result
            storage_manager.store_result(job_id, output_path)

            # Update progress by one completed subtask
            try:
                storage_manager.increment_chunk(job_id, 1, message=f"서브작업 완료: {a_path.name}")
            except Exception:
                pass

            # Cleanup
            try:
                processor.cleanup_session()
            except Exception:
                pass

            return {
                "status": "OK",
                "job_id": job_id,
                "mode": mode,
                "index": sub_index,
                "output": output_filename,
            }
    except Exception as e:
        raise e


@celery_app.task(name="tasks.generate_partial_jokbo")
def generate_partial_jokbo(job_id: str, model_type: Optional[str] = None, multi_api: Optional[bool] = None) -> dict:
    """Generate a partial jokbo PDF with cropped question regions + explanations.

    Parameters may be provided via job metadata or directly as kwargs
    (for consistency with other modes):
    - model_type: "flash" or "pro"
    - multi_api: prefer multi-API when True and multiple keys configured
    """

    sm = StorageManager()
    try:
        metadata = sm.get_job_metadata(job_id)
        if not metadata:
            raise Exception(f"Job metadata not found for {job_id}")

        jokbo_keys = metadata.get("jokbo_keys", [])
        lesson_keys = metadata.get("lesson_keys", [])

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            jokbo_dir = temp_path / "jokbo"
            lesson_dir = temp_path / "lesson"
            output_dir = temp_path / "output"

            jokbo_dir.mkdir(parents=True, exist_ok=True)
            lesson_dir.mkdir(parents=True, exist_ok=True)
            output_dir.mkdir(parents=True, exist_ok=True)

            # Download inputs
            jokbo_paths: list[str] = []
            for key in jokbo_keys:
                name = key.split(":")[-2]
                local_path = jokbo_dir / name
                sm.save_file_locally(key, local_path)
                jokbo_paths.append(str(local_path))

            lesson_paths: list[str] = []
            for key in lesson_keys:
                name = key.split(":")[-2]
                local_path = lesson_dir / name
                sm.save_file_locally(key, local_path)
                lesson_paths.append(str(local_path))

            # Initialize progress: total chunks = jokbo_count * sum(lesson_chunks)
            try:
                lesson_chunks = 0
                for lp in lesson_paths:
                    try:
                        lesson_chunks += len(PDFOperations.split_pdf_for_chunks(lp))
                    except Exception:
                        lesson_chunks += 1
                total_chunks = max(1, len(jokbo_paths) * max(1, lesson_chunks))
                sm.init_progress(job_id, total_chunks, "부분 족보 분석 시작")
            except Exception:
                try:
                    sm.init_progress(job_id, max(1, len(jokbo_paths)), "부분 족보 분석 시작")
                except Exception:
                    pass

            # Configure API + model, honoring overrides from metadata/kwargs
            configure_api()
            try:
                meta_model = None
                meta_multi = None
                if isinstance(metadata, dict):
                    meta_model = metadata.get("model")
                    meta_multi = metadata.get("multi_api")
            except Exception:
                meta_model = None
                meta_multi = None

            selected_model = model_type or meta_model or MODEL_TYPE
            model = create_model(selected_model)
            processor = PDFProcessor(model, session_id=job_id)

            # Determine multi-API strategy
            try:
                from config import API_KEYS as _API_KEYS  # type: ignore
            except Exception:
                _API_KEYS = []
            prefer_multi = meta_multi if meta_multi is not None else (multi_api if multi_api is not None else (len(_API_KEYS) > 1))
            try:
                logger.info(f"generate_partial_jokbo: prefer_multi={prefer_multi}, API_KEYS_count={len(_API_KEYS) if isinstance(_API_KEYS, list) else 0}")
                if prefer_multi and (not isinstance(_API_KEYS, list) or len(_API_KEYS) < 2):
                    logger.warning("generate_partial_jokbo: requested multi_api but only 1 key available; falling back to single-key")
            except Exception:
                pass

            # Ask Gemini for question spans (use multi when requested and keys available)
            try:
                if prefer_multi and isinstance(_API_KEYS, list) and len(_API_KEYS) > 1:
                    analysis = processor.analyze_partial_jokbo_multi_api(jokbo_paths, lesson_paths, api_keys=_API_KEYS)
                else:
                    analysis = processor.analyze_partial_jokbo(jokbo_paths, lesson_paths)
            except Exception:
                analysis = processor.analyze_partial_jokbo(jokbo_paths, lesson_paths)

            # Build a cache for question-number -> pages index per jokbo, to avoid
            # trusting model-reported page_start for final cropping.
            qindex_cache: dict[str, dict[int, list[int]]] = {}
            def _qindex_for(path: str) -> dict[int, list[int]]:
                key = str(Path(path).resolve()) if path else path
                if key in qindex_cache:
                    return qindex_cache[key]
                try:
                    pairs = PDFOperations.index_questions(path)
                except Exception:
                    pairs = []
                m: dict[int, list[int]] = {}
                for p, qv in pairs or []:
                    try:
                        qn = int(qv)
                        pg = int(p)
                    except Exception:
                        continue
                    if qn <= 0 or pg <= 0:
                        continue
                    m.setdefault(qn, [])
                    if pg not in m[qn]:
                        m[qn].append(pg)
                for k in list(m.keys()):
                    try:
                        m[k].sort()
                    except Exception:
                        pass
                qindex_cache[key] = m
                return m

            # Crop questions
            questions: list[dict[str, str]] = []
            for idx, q in enumerate(analysis.get("questions", []), 1):
                try:
                    src_path = q.get("_jokbo_path") or (jokbo_paths[0] if jokbo_paths else None)
                    if not src_path:
                        continue
                    # Resolve start page by scanning jokbo for the question number
                    qn_int = 0
                    try:
                        qn_int = int(str(q.get("question_number") or "").strip().split()[0])
                    except Exception:
                        qn_int = 0
                    reported_ps = int(q.get("page_start") or 1)
                    sp = reported_ps
                    if qn_int > 0:
                        pages = _qindex_for(str(src_path)).get(qn_int) or []
                        if pages:
                            # Choose nearest to reported hint
                            try:
                                sp = min(pages, key=lambda p: abs(int(p) - int(reported_ps)))
                            except Exception:
                                sp = int(pages[0])
                    q_pdf = PDFOperations.extract_question_region(
                        src_path,
                        int(sp or 1),
                        q.get("next_question_start"),
                        q.get("question_number"),
                    )
                    questions.append({
                        "question_pdf": q_pdf,
                        "explanation": q.get("explanation") or "",
                    })
                except Exception:
                    continue

            creator = PDFCreator()
            output_path = output_dir / "partial_jokbo.pdf"
            creator.create_partial_jokbo_pdf(questions, str(output_path))
            sm.store_result(job_id, output_path)

            # Best-effort cleanup of per-question temporary PDFs created during cropping
            try:
                for q in questions:
                    try:
                        qp = q.get("question_pdf")
                        if qp:
                            Path(str(qp)).unlink(missing_ok=True)
                    except Exception:
                        continue
            except Exception:
                pass

            try:
                sm.finalize_progress(job_id, "완료")
            except Exception:
                pass

            result = {"status": "OK", "job_id": job_id, "output": output_path.name}
            if isinstance(analysis, dict) and analysis.get("warnings"):
                result["warnings"] = analysis["warnings"]
            return result
    except CancelledError:
        try:
            sm.update_progress(job_id, int((sm.get_progress(job_id) or {}).get('progress', 0) or 0), "사용자 취소됨")
            sm.finalize_progress(job_id, "취소됨")
        except Exception:
            pass
        current_task.update_state(state='REVOKED', meta={"job_id": job_id, "status": "cancelled"})
        raise Ignore()
    except SoftTimeLimitExceeded:
        try:
            sm.update_progress(job_id, int((sm.get_progress(job_id) or {}).get('progress', 0) or 0), "시간 제한으로 취소됨")
            sm.finalize_progress(job_id, "취소됨")
        except Exception:
            pass
        current_task.update_state(state='REVOKED', meta={"job_id": job_id, "status": "timeout"})
        raise Ignore()
    except Exception as exc:
        raise exc


@celery_app.task(name="tasks.aggregate_batch")
def aggregate_batch(results: list, job_id: str):
    """Finalize a batch job after all subtasks completed.

    - Marks job progress as complete
    - Writes a simple manifest JSON alongside PDFs (best-effort)
    """
    sm = StorageManager()
    try:
        # Finalize progress
        try:
            sm.finalize_progress(job_id, "완료")
        except Exception:
            pass

        # Persist a manifest file to results directory for bookkeeping
        try:
            from pathlib import Path
            import json as _json
            manifest = {
                "job_id": job_id,
                "generated": [r.get("output") for r in (results or []) if isinstance(r, dict)],
                "count": len([r for r in (results or []) if isinstance(r, dict)]),
            }
            dest_dir = sm.results_dir / job_id
            dest_dir.mkdir(parents=True, exist_ok=True)
            (dest_dir / "manifest.json").write_text(_json.dumps(manifest, ensure_ascii=False, indent=2))
        except Exception:
            pass
        return {"job_id": job_id, "subtask_results": results}
    except Exception as e:
        raise e


@celery_app.task(name="tasks.run_exam_only")
def run_exam_only(job_id: str, model_type: Optional[str] = None, multi_api: Optional[bool] = None) -> dict:
    """Run Exam Only mode: input jokbo only, output problems with detailed explanations.

    Flow:
    - Download jokbo files
    - Detect question ranges and split into 20-question chunks (by page spans)
    - Fan out chunks (multi-API when enabled) and collect questions
    - Crop questions and assemble final PDF: [Q pages] + [explanation page]
    """
    sm = StorageManager()
    try:
        metadata = sm.get_job_metadata(job_id)
        if not metadata:
            raise Exception(f"Job metadata not found for {job_id}")
        jokbo_keys = list(metadata.get("jokbo_keys", []) or [])
        # Resolve model + multi-api
        selected_model = model_type or metadata.get("model") or MODEL_TYPE
        prefer_multi = metadata.get("multi_api") if metadata.get("multi_api") is not None else multi_api

        # Prepare temp dirs
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            jokbo_dir = base / "jokbo"
            out_dir = base / "output"
            jokbo_dir.mkdir(parents=True, exist_ok=True)
            out_dir.mkdir(parents=True, exist_ok=True)

            # Download jokbos
            jokbo_paths: list[str] = []
            for k in jokbo_keys:
                name = k.split(":")[-2]
                lp = jokbo_dir / name
                sm.save_file_locally(k, lp)
                jokbo_paths.append(str(lp))

            # Build chunks across all jokbos and initialize progress
            from pdf_processor.pdf.operations import PDFOperations as _PDFOps
            all_chunks: list[tuple[str, tuple[int, int, int, int]]] = []  # (jokbo_path, (s, e, qstart, qend))
            for jp in jokbo_paths:
                try:
                    groups = _PDFOps.split_by_question_groups(jp, group_size=20)
                    try:
                        logger.info(f"exam-only: {Path(jp).name} -> {len(groups)} chunk(s) by ordinal question grouping")
                    except Exception:
                        pass
                    for (s, e, qs, qe) in groups:
                        all_chunks.append((jp, (int(s), int(e), int(qs), int(qe))))
                except Exception:
                    # Fallback: treat whole file as one chunk
                    try:
                        pc = _PDFOps.get_page_count(jp)
                    except Exception:
                        pc = 1
                    all_chunks.append((jp, (1, pc, 1, 20)))

            total_chunks = max(1, len(all_chunks))
            try:
                sm.init_progress(job_id, total_chunks, f"청크 준비 완료: {total_chunks}개")
                # Brief chunk list snapshot for debugging order issues
                try:
                    preview = [f"{Path(jp).name}:{s}-{e}(Q{qs}-{qe})" for jp, (s, e, qs, qe) in all_chunks[:10]]
                    logger.info(f"exam-only: first chunks => {preview}")
                except Exception:
                    pass
            except Exception:
                pass

            # Configure API + model
            configure_api()
            model = create_model(selected_model)

            # Analyzer runner per chunk
            from pdf_processor.analyzers.exam_only import ExamOnlyAnalyzer
            from pdf_processor.api.multi_api_manager import MultiAPIManager
            from pdf_processor.api.file_manager import FileManager

            questions_acc: list[dict] = []

            if prefer_multi and isinstance(API_KEYS, list) and len(API_KEYS) > 1:
                # Extract chunk PDFs first to enable distribution
                task_items: list[tuple[str, str, tuple[int, int], tuple[int, int, int, int]]] = []
                tmp_paths: list[Path] = []
                for jp, (s, e, qs, qe) in all_chunks:
                    cpath = _PDFOps.extract_pages(jp, s, e)
                    tmp_paths.append(Path(cpath))
                    task_items.append((jp, cpath, (s, e), (qs, qe)))

                # Distribute across keys
                api_manager = MultiAPIManager(API_KEYS, {"model": selected_model})

                def op(task, api_client, _model):
                    orig_jp, chunk_path, (s, e), (qs, qe) = task
                    analyzer = ExamOnlyAnalyzer(api_client, FileManager(api_client), job_id, Path("output/debug"))
                    res = analyzer.analyze_chunk(chunk_path, Path(orig_jp).name, (qs, qe), chunk_info=(s, e))
                    # Tag each question with its source jokbo filename for reliable grouping later
                    try:
                        src_name = Path(orig_jp).name
                        for q in (res.get("questions") or []):
                            if isinstance(q, dict):
                                q["source_filename"] = src_name
                    except Exception:
                        pass
                    return res

                def on_progress(_):
                    try:
                        sm.increment_chunk(job_id, 1)
                    except Exception:
                        pass

                results = api_manager.distribute_tasks(task_items, op, parallel=True, max_workers=None, on_progress=on_progress)

                for r in results or []:
                    if isinstance(r, dict):
                        for q in (r.get("questions") or []):
                            qq = dict(q)
                            questions_acc.append(qq)

                # Cleanup tmp chunk PDFs
                for p in tmp_paths:
                    try:
                        p.unlink(missing_ok=True)
                    except Exception:
                        pass
            else:
                # Single-key sequential processing
                _pp = PDFProcessor(model, session_id=job_id)
                analyzer = ExamOnlyAnalyzer(_pp.api_client, FileManager(_pp.api_client), job_id, Path("output/debug"))
                for jp, (s, e, qs, qe) in all_chunks:
                    try:
                        cpath = _PDFOps.extract_pages(jp, s, e)
                        res = analyzer.analyze_chunk(cpath, Path(jp).name, (qs, qe), chunk_info=(s, e))
                        # Tag questions with source
                        try:
                            src_name = Path(jp).name
                            for q in (res.get("questions") or []):
                                qq = dict(q)
                                qq["source_filename"] = src_name
                                questions_acc.append(qq)
                        except Exception:
                            for q in (res.get("questions") or []):
                                questions_acc.append(dict(q))
                    finally:
                        try:
                            Path(cpath).unlink(missing_ok=True)
                        except Exception:
                            pass
                    try:
                        sm.increment_chunk(job_id, 1)
                    except Exception:
                        pass

            # Crop and assemble final PDF per jokbo (questions are page-referenced to original)
            creator = PDFCreator()
            # For each jokbo, assemble a consolidated PDF preserving order by page_start then question_number
            for jp in jokbo_paths:
                jp_name = Path(jp).name
                # Select questions belonging to this jokbo via explicit source tag;
                # fall back to page-range heuristic if tag is missing.
                try:
                    pc = _PDFOps.get_page_count(jp)
                except Exception:
                    pc = 0
                qs_for_file: list[dict] = []
                for q in questions_acc:
                    src = q.get("source_filename")
                    if src == jp_name:
                        qs_for_file.append(q)
                    else:
                        # Heuristic fallback for legacy results
                        try:
                            ps = int(q.get("page_start") or 0)
                        except Exception:
                            ps = 0
                        if src is None and (pc <= 0 or 1 <= ps <= pc):
                            qs_for_file.append(q)
                # Build cropped question PDFs
                items: list[dict] = []
                # Order
                try:
                    import re as _re
                    # Primary: by page_start; secondary: by next_question_start; tertiary: by question_number (numeric part only)
                    def _num(val) -> int:
                        try:
                            s = str(val or "")
                            m = _re.search(r"(\d+)", s)
                            return int(m.group(1)) if m else 0
                        except Exception:
                            return 0
                    def _key(x: dict):
                        ps = _num(x.get('page_start'))
                        nqs = x.get('next_question_start')
                        nqs_i = _num(nqs) if nqs is not None else 10**9
                        qn = _num(x.get('question_number'))
                        return (ps, nqs_i, qn)
                    qs_for_file.sort(key=_key)
                except Exception:
                    pass
                for q in qs_for_file:
                    try:
                        ps = int(q.get("page_start") or 0)
                        nqs = q.get("next_question_start")
                        nqs_i = int(nqs) if nqs is not None else None
                        qnum = q.get("question_number")
                        q_pdf = _PDFOps.extract_question_region(jp, ps, nqs_i, qnum)
                        item = {
                            "question_pdf": q_pdf,
                            "question_number": q.get("question_number"),
                            "question_text": q.get("question_text"),
                            "answer": q.get("answer"),
                            "explanation": q.get("explanation"),
                            "background_knowledge": q.get("background_knowledge"),
                            "wrong_answer_explanations": q.get("wrong_answer_explanations") if isinstance(q.get("wrong_answer_explanations"), dict) else {},
                        }
                        items.append(item)
                    except Exception:
                        continue
                if not items:
                    continue
                out_path = out_dir / f"exam_only_{Path(jp).stem}.pdf"
                creator.create_exam_only_pdf(items, str(out_path))
                sm.store_result(job_id, out_path)

            try:
                sm.finalize_progress(job_id, "완료")
            except Exception:
                pass
            return {"status": "OK", "job_id": job_id, "files_generated": len(list(out_dir.glob('*.pdf')))}
    except CancelledError:
        try:
            sm.update_progress(job_id, int((sm.get_progress(job_id) or {}).get('progress', 0) or 0), "사용자 취소됨")
            sm.finalize_progress(job_id, "취소됨")
        except Exception:
            pass
        current_task.update_state(state='REVOKED', meta={"job_id": job_id, "status": "cancelled"})
        raise Ignore()
    except SoftTimeLimitExceeded:
        try:
            sm.update_progress(job_id, int((sm.get_progress(job_id) or {}).get('progress', 0) or 0), "시간 제한으로 취소됨")
            sm.finalize_progress(job_id, "취소됨")
        except Exception:
            pass
        current_task.update_state(state='REVOKED', meta={"job_id": job_id, "status": "timeout"})
        raise Ignore()
    except Exception as exc:
        raise exc
