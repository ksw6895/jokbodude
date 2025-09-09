import os
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile, Depends

from ..core import MAX_FILE_SIZE, celery_app
from ._helpers import save_files_metadata_with_info
from .auth import require_user

# Local imports for PDF ops
from pdf_processor.pdf.operations import PDFOperations

router = APIRouter()


def _per_chunk_tokens(model: Optional[str]) -> int:
    """Return configured tokens-per-chunk for the given model.

    Defaults: flash=1, pro=4 (overridable via env FLASH_TOKENS_PER_CHUNK / PRO_TOKENS_PER_CHUNK).
    """
    m = (model or "flash").strip().lower()
    try:
        flash_cost = max(0, int(os.getenv("FLASH_TOKENS_PER_CHUNK", "1")))
    except Exception:
        flash_cost = 1
    try:
        pro_cost = max(0, int(os.getenv("PRO_TOKENS_PER_CHUNK", "4")))
    except Exception:
        pro_cost = 4
    return pro_cost if m == "pro" else flash_cost


def _build_file_info(path: Path) -> dict:
    try:
        pages = int(PDFOperations.get_page_count(str(path)))
    except Exception:
        pages = 0
    try:
        chunks = int(len(PDFOperations.split_pdf_for_chunks(str(path))))
    except Exception:
        chunks = 1
    return {"filename": path.name, "pages": pages, "chunks": chunks}


@router.post("/preflight/exam-only")
async def preflight_exam_only(
    request: Request,
    jokbo_files: list[UploadFile] = File(...),
    model: Optional[str] = Query("flash", regex="^(flash|pro)$"),
    multi_api: bool = Query(True),
    multi_api_form: Optional[bool] = Form(None, alias="multi_api"),
    user: dict = Depends(require_user),
):
    """Preflight for exam-only mode: store jokbo files and estimate chunks by 20-question groups.

    Estimation uses OCR-aided question detection to split by 20-question groups.
    """
    from pdf_processor.pdf.operations import PDFOperations
    storage_manager = request.app.state.storage_manager

    try:
        effective_multi = True

        def _builder(p: Path, kind: str) -> dict:
            # Base info
            info = _build_file_info(p)
            # Enhanced groups for exam-only
            try:
                groups = PDFOperations.split_by_question_groups(str(p))
                group_count = len(groups)
            except Exception:
                try:
                    group_count = len(PDFOperations.split_pdf_for_chunks(str(p)))
                except Exception:
                    group_count = 1
            info["question_groups"] = group_count
            return info

        job_id, meta = await save_files_metadata_with_info(
            request,
            jokbo_files,
            [],
            mode="exam-only",
            model=model,
            multi_api=bool(effective_multi),
            min_relevance=None,
            user_id=user.get("sub"),
            info_builder=_builder,
        )

        files_info = (meta or {}).get("preflight_files") or {}
        joker_info = list(files_info.get("jokbo") or [])
        try:
            total_chunks = sum(int(max(1, (ji.get('question_groups') or 1))) for ji in (joker_info or []))
        except Exception:
            total_chunks = len(joker_info) if joker_info else 1
        total_chunks = max(1, int(total_chunks))
        tokens_per_chunk = _per_chunk_tokens(model)
        est_tokens = tokens_per_chunk * total_chunks
        pct_per_chunk = 100 / total_chunks if total_chunks > 0 else 100

        # Augment metadata as preflight
        try:
            meta["preflight"] = True
            meta["preflight_stats"] = {
                "jokbo": joker_info,
                "total_chunks": total_chunks,
                "tokens_per_chunk": tokens_per_chunk,
                "estimated_tokens": est_tokens,
            }
            storage_manager.store_job_metadata(job_id, meta)
        except Exception:
            pass

        return {
            "job_id": job_id,
            "mode": "exam-only",
            "model": model,
            "multi_api": effective_multi,
            "files": {"jokbo": joker_info},
            "total_chunks": total_chunks,
            "tokens_per_chunk": tokens_per_chunk,
            "estimated_tokens": est_tokens,
            "pct_per_chunk": pct_per_chunk,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Preflight failed: {str(e)}")


@router.post("/preflight/partial-jokbo")
async def preflight_partial_jokbo(
    request: Request,
    jokbo_files: list[UploadFile] = File(...),
    lesson_files: list[UploadFile] = File(...),
    model: Optional[str] = Query("flash", regex="^(flash|pro)$"),
    multi_api: bool = Query(True),
    # Accept for parity (informational only for now)
    min_relevance: Optional[int] = Query(None, ge=0, le=110),
    multi_api_form: Optional[bool] = Form(None, alias="multi_api"),
    min_relevance_form: Optional[int] = Form(None, alias="min_relevance"),
    user: dict = Depends(require_user),
):
    """Upload files, compute simple cost estimate for partial-jokbo, store metadata, and return a job_id.

    Estimation model: jokbo_count × sum(lesson_chunks).
    This mirrors runtime progress for partial‑jokbo (each jokbo counts as lesson_chunks units).
    """
    storage_manager = request.app.state.storage_manager

    try:
        # Normalize options
        effective_multi = True
        effective_min_rel: Optional[int] = None
        try:
            mr = min_relevance_form if min_relevance_form is not None else min_relevance
            if mr is not None:
                effective_min_rel = max(0, min(int(mr), 110))
        except Exception:
            effective_min_rel = None

        def _builder(p: Path, kind: str) -> dict:
            return _build_file_info(p)

        job_id, meta = await save_files_metadata_with_info(
            request,
            jokbo_files,
            lesson_files,
            mode="partial-jokbo",
            model=model,
            multi_api=bool(effective_multi),
            min_relevance=effective_min_rel,
            user_id=user.get("sub"),
            info_builder=_builder,
        )

        files_info = (meta or {}).get("preflight_files") or {}
        jokbo_info = list(files_info.get("jokbo") or [])
        lesson_info = list(files_info.get("lesson") or [])

        try:
            lesson_chunks_sum = sum(int(max(1, (li.get('chunks') or 1))) for li in (lesson_info or []))
        except Exception:
            lesson_chunks_sum = len(lesson_info) if lesson_info else 1
        if lesson_chunks_sum <= 0:
            lesson_chunks_sum = 1
        total_chunks = max(1, len(jokbo_info) * lesson_chunks_sum)
        tokens_per_chunk = _per_chunk_tokens(model)
        est_tokens = tokens_per_chunk * total_chunks
        pct_per_chunk = 100 / total_chunks if total_chunks > 0 else 100

        try:
            meta["preflight"] = True
            meta["preflight_stats"] = {
                "jokbo": jokbo_info,
                "lesson": lesson_info,
                "total_chunks": total_chunks,
                "tokens_per_chunk": tokens_per_chunk,
                "estimated_tokens": est_tokens,
            }
            storage_manager.store_job_metadata(job_id, meta)
        except Exception:
            pass

        return {
            "job_id": job_id,
            "mode": "partial-jokbo",
            "model": model,
            "multi_api": effective_multi,
            "min_relevance": effective_min_rel,
            "files": {"jokbo": jokbo_info, "lesson": lesson_info},
            "total_chunks": total_chunks,
            "tokens_per_chunk": tokens_per_chunk,
            "estimated_tokens": est_tokens,
            "pct_per_chunk": pct_per_chunk,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Preflight failed: {str(e)}")

@router.post("/preflight/jokbo-centric")
async def preflight_jokbo_centric(
    request: Request,
    jokbo_files: list[UploadFile] = File(...),
    lesson_files: list[UploadFile] = File(...),
    model: Optional[str] = Query("flash", regex="^(flash|pro)$"),
    multi_api: bool = Query(True),
    min_relevance: Optional[int] = Query(80, ge=0, le=110),
    # Allow overrides when sent as multipart fields (frontend sends 'multi_api' and 'min_relevance')
    multi_api_form: Optional[bool] = Form(None, alias="multi_api"),
    min_relevance_form: Optional[int] = Form(None, alias="min_relevance"),
    user: dict = Depends(require_user),
):
    """Upload files, compute page + chunk counts, store metadata, but do not start the job.

    Returns a job_id that can be confirmed via POST /jobs/{job_id}/start.
    """
    storage_manager = request.app.state.storage_manager

    try:
        effective_multi = True
        effective_min_rel: Optional[int] = None
        try:
            eff = min_relevance_form if min_relevance_form is not None else min_relevance
            if eff is not None:
                effective_min_rel = max(0, min(int(eff), 110))
        except Exception:
            effective_min_rel = None

        def _builder(p: Path, kind: str) -> dict:
            return _build_file_info(p)

        job_id, meta = await save_files_metadata_with_info(
            request,
            jokbo_files,
            lesson_files,
            mode="jokbo-centric",
            model=model,
            multi_api=bool(effective_multi),
            min_relevance=effective_min_rel,
            user_id=user.get("sub"),
            info_builder=_builder,
        )

        files_info = (meta or {}).get("preflight_files") or {}
        jokbo_info = list(files_info.get("jokbo") or [])
        lesson_info = list(files_info.get("lesson") or [])

        total_jokbos = max(1, len(jokbo_info))
        lesson_chunks = 0
        for li in lesson_info:
            try:
                lesson_chunks += int(li.get("chunks", 1))
            except Exception:
                lesson_chunks += 1
        total_chunks = max(1, total_jokbos * lesson_chunks)
        pct_per_chunk = 100 / total_chunks if total_chunks > 0 else 100
        tokens_per_chunk = _per_chunk_tokens(model)
        est_tokens = tokens_per_chunk * total_chunks

        try:
            meta["preflight"] = True
            meta["preflight_stats"] = {
                "jokbo": jokbo_info,
                "lesson": lesson_info,
                "total_chunks": total_chunks,
                "tokens_per_chunk": tokens_per_chunk,
                "estimated_tokens": est_tokens,
            }
            storage_manager.store_job_metadata(job_id, meta)
        except Exception:
            pass

        return {
            "job_id": job_id,
            "mode": "jokbo-centric",
            "model": model,
            "multi_api": effective_multi,
            "min_relevance": effective_min_rel,
            "files": {"jokbo": jokbo_info, "lesson": lesson_info},
            "total_chunks": total_chunks,
            "tokens_per_chunk": tokens_per_chunk,
            "estimated_tokens": est_tokens,
            "pct_per_chunk": pct_per_chunk,
        }
    except HTTPException:
        # rethrow known HTTP errors
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Preflight failed: {str(e)}")


@router.post("/preflight/lesson-centric")
async def preflight_lesson_centric(
    request: Request,
    jokbo_files: list[UploadFile] = File(...),
    lesson_files: list[UploadFile] = File(...),
    model: Optional[str] = Query("flash", regex="^(flash|pro)$"),
    multi_api: bool = Query(True),
    min_relevance: Optional[int] = Query(80, ge=0, le=110),
    multi_api_form: Optional[bool] = Form(None, alias="multi_api"),
    min_relevance_form: Optional[int] = Form(None, alias="min_relevance"),
    user: dict = Depends(require_user),
):
    """Preflight for lesson-centric mode. Stores files, returns counts, does not start job."""
    storage_manager = request.app.state.storage_manager

    try:
        effective_multi = True
        effective_min_rel: Optional[int] = None
        try:
            eff = min_relevance_form if min_relevance_form is not None else min_relevance
            if eff is not None:
                effective_min_rel = max(0, min(int(eff), 110))
        except Exception:
            effective_min_rel = None

        def _builder(p: Path, kind: str) -> dict:
            return _build_file_info(p)

        job_id, meta = await save_files_metadata_with_info(
            request,
            jokbo_files,
            lesson_files,
            mode="lesson-centric",
            model=model,
            multi_api=bool(effective_multi),
            min_relevance=effective_min_rel,
            user_id=user.get("sub"),
            info_builder=_builder,
        )

        files_info = (meta or {}).get("preflight_files") or {}
        jokbo_info = list(files_info.get("jokbo") or [])
        lesson_info = list(files_info.get("lesson") or [])

        total_lessons = max(1, len(lesson_info))
        total_jokbos = max(1, len(jokbo_info))
        lesson_chunks = 0
        for li in lesson_info:
            try:
                lesson_chunks += int(li.get("chunks", 1))
            except Exception:
                lesson_chunks += 1
        total_chunks = max(1, lesson_chunks * max(1, total_jokbos))
        pct_per_chunk = 100 / total_chunks if total_chunks > 0 else 100
        tokens_per_chunk = _per_chunk_tokens(model)
        est_tokens = tokens_per_chunk * total_chunks

        try:
            meta["preflight"] = True
            meta["preflight_stats"] = {
                "jokbo": jokbo_info,
                "lesson": lesson_info,
                "total_chunks": total_chunks,
                "tokens_per_chunk": tokens_per_chunk,
                "estimated_tokens": est_tokens,
            }
            storage_manager.store_job_metadata(job_id, meta)
        except Exception:
            pass

        return {
            "job_id": job_id,
            "mode": "lesson-centric",
            "model": model,
            "multi_api": effective_multi,
            "min_relevance": effective_min_rel,
            "files": {"jokbo": jokbo_info, "lesson": lesson_info},
            "total_chunks": total_chunks,
            "tokens_per_chunk": tokens_per_chunk,
            "estimated_tokens": est_tokens,
            "pct_per_chunk": pct_per_chunk,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Preflight failed: {str(e)}")


@router.post("/jobs/{job_id}/start")
def start_preflight_job(request: Request, job_id: str, user: dict = Depends(require_user)):
    """Confirm and start a previously created preflight job.

    Returns { job_id, task_id } on success.
    """
    storage_manager = request.app.state.storage_manager
    # Ensure ownership
    try:
        owner = storage_manager.get_job_owner(job_id)
    except Exception:
        owner = None
    if not owner or owner != user.get("sub"):
        raise HTTPException(status_code=403, detail="Not authorized for this job")

    meta = storage_manager.get_job_metadata(job_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Job metadata not found")
    mode = (meta or {}).get("mode")
    model = (meta or {}).get("model") or "flash"
    # Robustly interpret the stored multi_api flag
    # Force Multi-API on start regardless of stored meta
    use_multi = True

    # Clear preflight flag; job is starting
    try:
        meta["preflight"] = False
        storage_manager.store_job_metadata(job_id, meta)
    except Exception:
        pass

    if mode == "jokbo-centric":
        task = celery_app.send_task(
            "tasks.run_jokbo_analysis",
            args=[job_id],
            kwargs={"model_type": model, "multi_api": use_multi},
            queue="analysis",
        )
    elif mode == "lesson-centric":
        task = celery_app.send_task(
            "tasks.run_lesson_analysis",
            args=[job_id],
            kwargs={"model_type": model, "multi_api": use_multi},
            queue="analysis",
        )
    elif mode == "partial-jokbo":
        task = celery_app.send_task(
            "tasks.generate_partial_jokbo",
            args=[job_id],
            kwargs={"model_type": model, "multi_api": use_multi},
            queue="analysis",
        )
    elif mode == "exam-only":
        task = celery_app.send_task(
            "tasks.run_exam_only",
            args=[job_id],
            kwargs={"model_type": model, "multi_api": use_multi},
            queue="analysis",
        )
    else:
        raise HTTPException(status_code=400, detail="Unsupported mode for start")

    try:
        storage_manager.set_job_task(job_id, task.id)
    except Exception:
        pass
    return {"job_id": job_id, "task_id": task.id}
