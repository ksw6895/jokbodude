from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from typing import Optional, Tuple

from fastapi import HTTPException, Request, UploadFile
from typing import Callable, Dict

from ..core import MAX_FILE_SIZE, celery_app


def _ensure_size_limit(files: list[UploadFile]) -> None:
    for f in files:
        if f.size and f.size > MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail=f"File {f.filename} exceeds maximum size of 50MB")


async def save_files_and_metadata(
    request: Request,
    jokbo_files: list[UploadFile],
    lesson_files: list[UploadFile],
    *,
    mode: str,
    model: Optional[str],
    multi_api: bool,
    min_relevance: Optional[int],
    user_id: Optional[str],
) -> Tuple[str, dict]:
    """Common helper to persist uploaded files and store job metadata.

    - Validates file sizes
    - Saves files to Redis via StorageManager
    - Refreshes TTLs best-effort
    - Stores metadata and user↔job mapping
    - Optionally enforces positive token balance (best-effort)
    """
    job_id = str(uuid.uuid4())
    sm = request.app.state.storage_manager

    _ensure_size_limit(jokbo_files + lesson_files)

    jokbo_keys: list[str] = []
    lesson_keys: list[str] = []
    with tempfile.TemporaryDirectory() as temp_dir:
        tdir = Path(temp_dir)
        for f in jokbo_files:
            p = tdir / f.filename
            content = await f.read()
            p.write_bytes(content)
            k = sm.store_file(p, job_id, "jokbo")
            jokbo_keys.append(k)
            if not sm.verify_file_available(k):
                raise HTTPException(status_code=503, detail=f"Storage unavailable for {f.filename}; please retry later")
        for f in lesson_files:
            p = tdir / f.filename
            content = await f.read()
            p.write_bytes(content)
            k = sm.store_file(p, job_id, "lesson")
            lesson_keys.append(k)
            if not sm.verify_file_available(k):
                raise HTTPException(status_code=503, detail=f"Storage unavailable for {f.filename}; please retry later")

    try:
        sm.refresh_ttls(jokbo_keys + lesson_keys)
    except Exception:
        pass

    metadata = {
        "mode": mode,
        "jokbo_keys": jokbo_keys,
        "lesson_keys": lesson_keys,
        "model": model,
        "multi_api": multi_api,
        "min_relevance": min_relevance,
        "user_id": user_id,
    }
    sm.store_job_metadata(job_id, metadata)

    if user_id:
        sm.add_user_job(user_id, job_id)
        # Optional preflight: require positive token balance
        try:
            bal = sm.get_user_tokens(user_id)
            if bal is not None and bal <= 0:
                raise HTTPException(status_code=402, detail="Insufficient tokens for analysis. Contact admin.")
        except HTTPException:
            raise
        except Exception:
            pass

    return job_id, metadata


async def save_files_metadata_with_info(
    request: Request,
    jokbo_files: list[UploadFile],
    lesson_files: list[UploadFile],
    *,
    mode: str,
    model: Optional[str],
    multi_api: bool,
    min_relevance: Optional[int],
    user_id: Optional[str],
    info_builder: Optional[Callable[[Path, str], Dict]] = None,
) -> Tuple[str, dict]:
    """Variant of save_files_and_metadata that also collects per-file info.

    The info_builder receives (temp_path, kind) and returns a dict with details
    like { filename, pages, chunks, ... } which will be stored under
    metadata['preflight_files'] = { 'jokbo': [...], 'lesson': [...] }.
    """
    job_id = str(uuid.uuid4())
    sm = request.app.state.storage_manager

    _ensure_size_limit(jokbo_files + lesson_files)

    jokbo_keys: list[str] = []
    lesson_keys: list[str] = []
    jokbo_info: list[Dict] = []
    lesson_info: list[Dict] = []
    with tempfile.TemporaryDirectory() as temp_dir:
        tdir = Path(temp_dir)
        for f in jokbo_files:
            p = tdir / f.filename
            content = await f.read()
            p.write_bytes(content)
            if info_builder is not None:
                try:
                    info = dict(info_builder(p, "jokbo") or {})
                except Exception:
                    info = {"filename": p.name}
                jokbo_info.append(info)
            k = sm.store_file(p, job_id, "jokbo")
            jokbo_keys.append(k)
            if not sm.verify_file_available(k):
                raise HTTPException(status_code=503, detail=f"Storage unavailable for {f.filename}; please retry later")
        for f in lesson_files:
            p = tdir / f.filename
            content = await f.read()
            p.write_bytes(content)
            if info_builder is not None:
                try:
                    info = dict(info_builder(p, "lesson") or {})
                except Exception:
                    info = {"filename": p.name}
                lesson_info.append(info)
            k = sm.store_file(p, job_id, "lesson")
            lesson_keys.append(k)
            if not sm.verify_file_available(k):
                raise HTTPException(status_code=503, detail=f"Storage unavailable for {f.filename}; please retry later")

    try:
        sm.refresh_ttls(jokbo_keys + lesson_keys)
    except Exception:
        pass

    metadata = {
        "mode": mode,
        "jokbo_keys": jokbo_keys,
        "lesson_keys": lesson_keys,
        "model": model,
        "multi_api": multi_api,
        "min_relevance": min_relevance,
        "user_id": user_id,
    }
    if info_builder is not None:
        metadata["preflight_files"] = {"jokbo": jokbo_info, "lesson": lesson_info}

    sm.store_job_metadata(job_id, metadata)

    if user_id:
        sm.add_user_job(user_id, job_id)
        try:
            bal = sm.get_user_tokens(user_id)
            if bal is not None and bal <= 0:
                raise HTTPException(status_code=402, detail="Insufficient tokens for analysis. Contact admin.")
        except HTTPException:
            raise
        except Exception:
            pass

    return job_id, metadata


def start_job(request: Request, job_id: str, *, mode: str, model: Optional[str], multi_api: bool) -> str:
    """Start a Celery job for the given mode and bind the task id to the job.

    Returns the task id.
    """
    task_name: str
    if mode == "jokbo-centric":
        task_name = "tasks.run_jokbo_analysis"
    elif mode == "lesson-centric":
        task_name = "tasks.run_lesson_analysis"
    elif mode == "partial-jokbo":
        task_name = "tasks.generate_partial_jokbo"
    elif mode == "exam-only":
        task_name = "tasks.run_exam_only"
    else:
        raise HTTPException(status_code=400, detail="Unsupported mode")

    task = celery_app.send_task(
        task_name,
        args=[job_id],
        kwargs={"model_type": model, "multi_api": multi_api},
        queue="analysis",
    )
    # Bind task id to job for status lookups (best-effort)
    try:
        sm = request.app.state.storage_manager
        sm.set_job_task(job_id, task.id)
    except Exception:
        pass
    return task.id
