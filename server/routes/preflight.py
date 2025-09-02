import os
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile, Depends

from ..core import MAX_FILE_SIZE, celery_app
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


@router.post("/preflight/partial-jokbo")
async def preflight_partial_jokbo(
    request: Request,
    jokbo_files: list[UploadFile] = File(...),
    lesson_files: list[UploadFile] = File(...),
    model: Optional[str] = Query("flash", regex="^(flash|pro)$"),
    multi_api: bool = Query(False),
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
    job_id = str(uuid.uuid4())

    try:
        for f in jokbo_files + lesson_files:
            if f.size and f.size > MAX_FILE_SIZE:
                raise HTTPException(status_code=413, detail=f"File {f.filename} exceeds maximum size of 50MB")

        jokbo_keys: list[str] = []
        lesson_keys: list[str] = []
        jokbo_info: list[dict] = []
        lesson_info: list[dict] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            tdir = Path(temp_dir)
            # Save jokbo files
            for f in jokbo_files:
                p = tdir / f.filename
                content = await f.read()
                p.write_bytes(content)
                info = _build_file_info(p)
                jokbo_info.append(info)
                k = storage_manager.store_file(p, job_id, "jokbo")
                jokbo_keys.append(k)
                if not storage_manager.verify_file_available(k):
                    raise HTTPException(status_code=503, detail=f"Storage unavailable for {f.filename}; please retry later")
            # Save lesson files
            for f in lesson_files:
                p = tdir / f.filename
                content = await f.read()
                p.write_bytes(content)
                info = _build_file_info(p)
                lesson_info.append(info)
                k = storage_manager.store_file(p, job_id, "lesson")
                lesson_keys.append(k)
                if not storage_manager.verify_file_available(k):
                    raise HTTPException(status_code=503, detail=f"Storage unavailable for {f.filename}; please retry later")

        # Refresh TTLs for safety while waiting for confirmation
        try:
            storage_manager.refresh_ttls(jokbo_keys + lesson_keys)
        except Exception:
            pass

        # Normalize options
        effective_multi = multi_api_form if multi_api_form is not None else multi_api
        effective_min_rel: Optional[int] = None
        try:
            mr = min_relevance_form if min_relevance_form is not None else min_relevance
            if mr is not None:
                effective_min_rel = max(0, min(int(mr), 110))
        except Exception:
            effective_min_rel = None

        # Estimate: jokbo_count × sum(lesson_chunks)
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

        metadata = {
            "mode": "partial-jokbo",
            "jokbo_keys": jokbo_keys,
            "lesson_keys": lesson_keys,
            "model": model,
            "multi_api": effective_multi,
            "min_relevance": effective_min_rel,
            "user_id": user.get("sub"),
            "preflight": True,
            "preflight_stats": {
                "jokbo": jokbo_info,
                "lesson": lesson_info,
                "total_chunks": total_chunks,
                "tokens_per_chunk": tokens_per_chunk,
                "estimated_tokens": est_tokens,
            },
        }
        storage_manager.store_job_metadata(job_id, metadata)
        user_id = user.get("sub")
        if user_id:
            storage_manager.add_user_job(user_id, job_id)

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
    multi_api: bool = Query(False),
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
    job_id = str(uuid.uuid4())

    try:
        for f in jokbo_files + lesson_files:
            if f.size and f.size > MAX_FILE_SIZE:
                raise HTTPException(status_code=413, detail=f"File {f.filename} exceeds maximum size of 50MB")

        jokbo_keys: list[str] = []
        lesson_keys: list[str] = []
        jokbo_info: list[dict] = []
        lesson_info: list[dict] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            tdir = Path(temp_dir)
            # Save jokbo files
            for f in jokbo_files:
                p = tdir / f.filename
                content = await f.read()
                p.write_bytes(content)
                info = _build_file_info(p)
                jokbo_info.append(info)
                k = storage_manager.store_file(p, job_id, "jokbo")
                jokbo_keys.append(k)
                if not storage_manager.verify_file_available(k):
                    raise HTTPException(status_code=503, detail=f"Storage unavailable for {f.filename}; please retry later")
            # Save lesson files
            for f in lesson_files:
                p = tdir / f.filename
                content = await f.read()
                p.write_bytes(content)
                info = _build_file_info(p)
                lesson_info.append(info)
                k = storage_manager.store_file(p, job_id, "lesson")
                lesson_keys.append(k)
                if not storage_manager.verify_file_available(k):
                    raise HTTPException(status_code=503, detail=f"Storage unavailable for {f.filename}; please retry later")

        # Refresh TTLs to keep files alive while waiting for confirmation
        try:
            storage_manager.refresh_ttls(jokbo_keys + lesson_keys)
        except Exception:
            pass

        effective_multi = multi_api_form if multi_api_form is not None else multi_api
        effective_min_rel: Optional[int] = None
        try:
            eff = min_relevance_form if min_relevance_form is not None else min_relevance
            if eff is not None:
                effective_min_rel = max(0, min(int(eff), 110))
        except Exception:
            effective_min_rel = None

        # Compute total chunks (mirror tasks.run_jokbo_analysis logic)
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

        metadata = {
            "mode": "jokbo-centric",
            "jokbo_keys": jokbo_keys,
            "lesson_keys": lesson_keys,
            "model": model,
            "multi_api": effective_multi,
            "min_relevance": effective_min_rel,
            "user_id": user.get("sub"),
            "preflight": True,
            "preflight_stats": {
                "jokbo": jokbo_info,
                "lesson": lesson_info,
                "total_chunks": total_chunks,
                "tokens_per_chunk": tokens_per_chunk,
                "estimated_tokens": est_tokens,
            },
        }
        storage_manager.store_job_metadata(job_id, metadata)
        user_id = user.get("sub")
        if user_id:
            storage_manager.add_user_job(user_id, job_id)

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
    multi_api: bool = Query(False),
    min_relevance: Optional[int] = Query(80, ge=0, le=110),
    multi_api_form: Optional[bool] = Form(None, alias="multi_api"),
    min_relevance_form: Optional[int] = Form(None, alias="min_relevance"),
    user: dict = Depends(require_user),
):
    """Preflight for lesson-centric mode. Stores files, returns counts, does not start job."""
    storage_manager = request.app.state.storage_manager
    job_id = str(uuid.uuid4())

    try:
        for f in jokbo_files + lesson_files:
            if f.size and f.size > MAX_FILE_SIZE:
                raise HTTPException(status_code=413, detail=f"File {f.filename} exceeds maximum size of 50MB")

        jokbo_keys: list[str] = []
        lesson_keys: list[str] = []
        jokbo_info: list[dict] = []
        lesson_info: list[dict] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            tdir = Path(temp_dir)
            # Save jokbos
            for f in jokbo_files:
                p = tdir / f.filename
                content = await f.read()
                p.write_bytes(content)
                info = _build_file_info(p)
                jokbo_info.append(info)
                k = storage_manager.store_file(p, job_id, "jokbo")
                jokbo_keys.append(k)
                if not storage_manager.verify_file_available(k):
                    raise HTTPException(status_code=503, detail=f"Storage unavailable for {f.filename}; please retry later")
            # Save lessons
            for f in lesson_files:
                p = tdir / f.filename
                content = await f.read()
                p.write_bytes(content)
                info = _build_file_info(p)
                lesson_info.append(info)
                k = storage_manager.store_file(p, job_id, "lesson")
                lesson_keys.append(k)
                if not storage_manager.verify_file_available(k):
                    raise HTTPException(status_code=503, detail=f"Storage unavailable for {f.filename}; please retry later")

        try:
            storage_manager.refresh_ttls(jokbo_keys + lesson_keys)
        except Exception:
            pass

        effective_multi = multi_api_form if multi_api_form is not None else multi_api
        effective_min_rel: Optional[int] = None
        try:
            eff = min_relevance_form if min_relevance_form is not None else min_relevance
            if eff is not None:
                effective_min_rel = max(0, min(int(eff), 110))
        except Exception:
            effective_min_rel = None

        # Compute total chunks (mirror tasks.run_lesson_analysis logic)
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

        metadata = {
            "mode": "lesson-centric",
            "jokbo_keys": jokbo_keys,
            "lesson_keys": lesson_keys,
            "model": model,
            "multi_api": effective_multi,
            "min_relevance": effective_min_rel,
            "user_id": user.get("sub"),
            "preflight": True,
            "preflight_stats": {
                "jokbo": jokbo_info,
                "lesson": lesson_info,
                "total_chunks": total_chunks,
                "tokens_per_chunk": tokens_per_chunk,
                "estimated_tokens": est_tokens,
            },
        }
        storage_manager.store_job_metadata(job_id, metadata)
        user_id = user.get("sub")
        if user_id:
            storage_manager.add_user_job(user_id, job_id)

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
    _mval = (meta or {}).get("multi_api")
    if isinstance(_mval, str):
        use_multi = _mval.strip().lower() in {"1", "true", "yes", "on"}
    else:
        use_multi = bool(_mval)

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
    else:
        raise HTTPException(status_code=400, detail="Unsupported mode for start")

    try:
        storage_manager.set_job_task(job_id, task.id)
    except Exception:
        pass
    return {"job_id": job_id, "task_id": task.id}
