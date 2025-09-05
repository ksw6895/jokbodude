# tasks.py
import os
import tempfile
import time
import threading
from pathlib import Path
from celery import Celery, current_task
from celery.exceptions import Ignore, SoftTimeLimitExceeded
from typing import Optional
from config import create_model, configure_api, API_KEYS
import logging
from pdf_processor.core.processor import PDFProcessor
from pdf_creator import PDFCreator
from storage_manager import StorageManager
from pdf_processor.pdf.operations import PDFOperations
from celery import group, chord
from pdf_processor.utils.exceptions import CancelledError

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

# --- Worker-side periodic storage prune (keeps ephemeral/persistent disk in check) ---
def _prune_path(base: Path, older_than_hours: int) -> None:
    now = time.time()
    if not base.exists():
        return
    for p in base.rglob("*"):
        try:
            if p.is_file():
                age_hours = (now - p.stat().st_mtime) / 3600.0
                if age_hours >= max(0, int(older_than_hours)):
                    p.unlink(missing_ok=True)
        except Exception:
            continue
    # Remove empty dirs
    for d in sorted(base.rglob("*"), key=lambda x: len(str(x)), reverse=True):
        try:
            if d.is_dir():
                next(d.iterdir())
        except StopIteration:
            try:
                d.rmdir()
            except Exception:
                pass


def _start_storage_pruner_thread() -> None:
    try:
        base = Path(os.getenv("RENDER_STORAGE_PATH", "output")).resolve()
        try:
            retention = int(os.getenv("STORAGE_RETENTION_HOURS", "24"))
        except Exception:
            retention = 24
        try:
            interval = int(os.getenv("PRUNE_INTERVAL_MINUTES", "60"))
        except Exception:
            interval = 60

        def _loop():
            while True:
                try:
                    if base.exists():
                        _prune_path(base, retention)
                except Exception:
                    pass
                time.sleep(max(60, interval * 60))

        t = threading.Thread(target=_loop, name="storage-pruner", daemon=True)
        t.start()
        try:
            logger.info("Worker storage-pruner thread started")
        except Exception:
            pass
    except Exception:
        pass


# Start the pruner when the module is imported (once per worker process)
_start_storage_pruner_thread()

# --- Analysis Tasks ---
@celery_app.task(name="tasks.run_jokbo_analysis")
def run_jokbo_analysis(job_id: str, model_type: str = None, multi_api: Optional[bool] = None):
    """Run jokbo-centric analysis"""
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
                # Add one extra slot per jokbo for the PDF build/store phase so
                # the UI does not show 100% until files are actually generated.
                postprocess_slots = max(0, total_jokbos)
                total_chunks = max(1, total_jokbos * lesson_chunks + postprocess_slots)
                logger.info(
                    f"Initializing progress for job {job_id}: jokbos={total_jokbos} lesson_chunks={lesson_chunks} post_slots={postprocess_slots} total_chunks={total_chunks}"
                )
                storage_manager.init_progress(job_id, total_chunks, f"총 청크: {total_chunks}")
            except Exception as e:
                logger.warning(f"Progress init fallback for job {job_id}: {e}")
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
                # Use multi-API orchestration when requested, even with a single key.
                if use_multi:
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
                
                logger.info(f"[job={job_id}] Creating jokbo-centric PDF for {jokbo_path.name} ...")
                creator.create_jokbo_centric_pdf(
                    str(jokbo_path),
                    analysis_result,
                    str(output_path),
                    str(lesson_dir)
                )
                
                # Store result in Redis
                try:
                    key = storage_manager.store_result(job_id, output_path)
                    logger.info(f"[job={job_id}] Stored result for job {job_id}: key={key}")
                except Exception as e:
                    logger.warning(f"Failed to store result for job {job_id}: {e}")
                # Count one post-processing unit for this jokbo (PDF build/store)
                try:
                    storage_manager.increment_chunk(job_id, 1, message=f"PDF 완료: {jokbo_path.name}")
                except Exception:
                    pass
            
            # Clean up processor resources
            processor.cleanup_session()
            
            # Finalize progress to 100% and clamp chunks
            try:
                storage_manager.finalize_progress(job_id, "완료")
            except Exception as e:
                logger.warning(f"Failed to finalize progress for job {job_id}: {e}")

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
        # Normalize failure without relying on backend exception encoding
        try:
            # Surface user-friendly status and freeze progress
            storage_manager.update_progress(job_id, int((storage_manager.get_progress(job_id) or {}).get('progress', 0) or 0), str(e))
            storage_manager.finalize_progress(job_id, "실패")
        except Exception:
            pass
        try:
            current_task.update_state(state='FAILURE', meta={
                "job_id": job_id,
                "exc_type": e.__class__.__name__,
                "error": str(e),
            })
        except Exception:
            pass
        # Avoid Celery JSON exception serialization pitfalls
        raise Ignore()

@celery_app.task(name="tasks.run_lesson_analysis")
def run_lesson_analysis(job_id: str, model_type: str = None, multi_api: Optional[bool] = None):
    """Run lesson-centric analysis"""
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
                # Add one extra slot per lesson for the PDF build/store phase
                postprocess_slots = max(0, total_lessons)
                total_chunks = max(1, lesson_chunks * max(1, total_jokbos) + postprocess_slots)
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
                # Use multi-API orchestration when requested, even with a single key.
                if use_multi:
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
                
                try:
                    logger.info(f"[job={job_id}] Creating lesson-centric PDF for {lesson_path.name} ...")
                except Exception:
                    pass
                creator.create_lesson_centric_pdf(
                    str(lesson_path),
                    analysis_result,
                    str(output_path),
                    str(jokbo_dir)
                )
                
                # Store result in Redis
                storage_manager.store_result(job_id, output_path)
                # Count one post-processing unit for this lesson (PDF build/store)
                try:
                    storage_manager.increment_chunk(job_id, 1, message=f"PDF 완료: {lesson_path.name}")
                except Exception:
                    pass
            
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
        try:
            storage_manager.update_progress(job_id, int((storage_manager.get_progress(job_id) or {}).get('progress', 0) or 0), str(e))
            storage_manager.finalize_progress(job_id, "실패")
        except Exception:
            pass
        try:
            current_task.update_state(state='FAILURE', meta={
                "job_id": job_id,
                "exc_type": e.__class__.__name__,
                "error": str(e),
            })
        except Exception:
            pass
        raise Ignore()


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
                    if multi_api
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
                    if multi_api
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
            # Allow multi-API orchestration even with a single key to leverage
            # per-key concurrency settings
            prefer_multi = meta_multi if meta_multi is not None else (multi_api if multi_api is not None else (len(_API_KEYS) >= 1))
            try:
                logger.info(f"generate_partial_jokbo: prefer_multi={prefer_multi}, API_KEYS_count={len(_API_KEYS) if isinstance(_API_KEYS, list) else 0}")
                if prefer_multi and (not isinstance(_API_KEYS, list) or len(_API_KEYS) < 2):
                    logger.warning("generate_partial_jokbo: requested multi_api but only 1 key available; falling back to single-key")
            except Exception:
                pass

            # Ask Gemini for question spans (use multi when requested and keys available)
            try:
                if prefer_multi and isinstance(_API_KEYS, list) and len(_API_KEYS) >= 1:
                    analysis = processor.analyze_partial_jokbo_multi_api(jokbo_paths, lesson_paths, api_keys=_API_KEYS)
                else:
                    analysis = processor.analyze_partial_jokbo(jokbo_paths, lesson_paths)
            except Exception:
                analysis = processor.analyze_partial_jokbo(jokbo_paths, lesson_paths)

            # Crop questions
            questions: list[dict[str, str]] = []
            for idx, q in enumerate(analysis.get("questions", []), 1):
                try:
                    src_path = q.get("_jokbo_path") or (jokbo_paths[0] if jokbo_paths else None)
                    if not src_path:
                        continue
                    q_pdf = PDFOperations.extract_question_region(
                        src_path,
                        int(q.get("page_start") or 1),
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
        try:
            sm.update_progress(job_id, int((sm.get_progress(job_id) or {}).get('progress', 0) or 0), str(exc))
            sm.finalize_progress(job_id, "실패")
        except Exception:
            pass
        try:
            current_task.update_state(state='FAILURE', meta={
                "job_id": job_id,
                "exc_type": exc.__class__.__name__,
                "error": str(exc),
            })
        except Exception:
            pass
        raise Ignore()


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
