import tempfile
import uuid
from pathlib import Path
from typing import Optional

from celery import chord, group
from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile

from ..core import MAX_FILE_SIZE, PRO_MODEL_PASSWORD, celery_app
from ..utils import save_uploaded_file

router = APIRouter()


@router.post("/analyze/jokbo-centric", status_code=202)
async def analyze_jokbo_centric(
    request: Request,
    jokbo_files: list[UploadFile] = File(...),
    lesson_files: list[UploadFile] = File(...),
    model: Optional[str] = Query("flash", regex="^(flash|pro)$"),
    password: Optional[str] = Query(None),
    multi_api: bool = Query(False),
    min_relevance: Optional[int] = Query(80, ge=0, le=110),
    multi_api_form: Optional[bool] = Form(None),
    min_relevance_form: Optional[int] = Form(None),
    user_id: Optional[str] = Query(None),
):
    job_id = str(uuid.uuid4())
    storage_manager = request.app.state.storage_manager

    try:
        if model == "pro" and password != PRO_MODEL_PASSWORD:
            raise HTTPException(status_code=403, detail="Invalid model password")

        for f in jokbo_files + lesson_files:
            if f.size and f.size > MAX_FILE_SIZE:
                raise HTTPException(
                    status_code=413,
                    detail=f"File {f.filename} exceeds maximum size of 50MB",
                )

        jokbo_keys: list[str] = []
        lesson_keys: list[str] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            for f in jokbo_files:
                file_path = temp_path / f.filename
                content = await f.read()
                file_path.write_bytes(content)
                file_key = storage_manager.store_file(file_path, job_id, "jokbo")
                jokbo_keys.append(file_key)
                if not storage_manager.verify_file_available(file_key):
                    raise HTTPException(status_code=503, detail=f"Storage unavailable for {f.filename}; please retry later")
            for f in lesson_files:
                file_path = temp_path / f.filename
                content = await f.read()
                file_path.write_bytes(content)
                file_key = storage_manager.store_file(file_path, job_id, "lesson")
                lesson_keys.append(file_key)
                if not storage_manager.verify_file_available(file_key):
                    raise HTTPException(status_code=503, detail=f"Storage unavailable for {f.filename}; please retry later")

        try:
            storage_manager.refresh_ttls(jokbo_keys + lesson_keys)
        except Exception:
            pass

        effective_multi = multi_api_form if multi_api_form is not None else multi_api
        effective_min_rel = None
        try:
            eff = min_relevance_form if min_relevance_form is not None else min_relevance
            if eff is not None:
                effective_min_rel = max(0, min(int(eff), 110))
        except Exception:
            effective_min_rel = None

        metadata = {
            "jokbo_keys": jokbo_keys,
            "lesson_keys": lesson_keys,
            "model": model,
            "multi_api": effective_multi,
            "min_relevance": effective_min_rel,
            "user_id": user_id,
        }
        storage_manager.store_job_metadata(job_id, metadata)
        if user_id:
            storage_manager.add_user_job(user_id, job_id)

        task = celery_app.send_task(
            "tasks.run_jokbo_analysis",
            args=[job_id],
            kwargs={"model_type": model, "multi_api": effective_multi},
        )
        try:
            storage_manager.set_job_task(job_id, task.id)
        except Exception:
            pass
        return {"job_id": job_id, "task_id": task.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"File upload failed: {str(e)}")


@router.post("/analyze/lesson-centric", status_code=202)
async def analyze_lesson_centric(
    request: Request,
    jokbo_files: list[UploadFile] = File(...),
    lesson_files: list[UploadFile] = File(...),
    model: Optional[str] = Query("flash", regex="^(flash|pro)$"),
    password: Optional[str] = Query(None),
    multi_api: bool = Query(False),
    min_relevance: Optional[int] = Query(80, ge=0, le=110),
    multi_api_form: Optional[bool] = Form(None),
    min_relevance_form: Optional[int] = Form(None),
    user_id: Optional[str] = Query(None),
):
    job_id = str(uuid.uuid4())
    storage_manager = request.app.state.storage_manager

    try:
        if model == "pro" and password != PRO_MODEL_PASSWORD:
            raise HTTPException(status_code=403, detail="Invalid model password")

        for f in jokbo_files + lesson_files:
            if f.size and f.size > MAX_FILE_SIZE:
                raise HTTPException(
                    status_code=413,
                    detail=f"File {f.filename} exceeds maximum size of 50MB",
                )

        jokbo_keys: list[str] = []
        lesson_keys: list[str] = []
        # Mirror the temp-directory usage of the jokbo-centric endpoint to avoid /tmp leaks
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            for f in jokbo_files:
                file_path = temp_path / f.filename
                content = await f.read()
                file_path.write_bytes(content)
                key = storage_manager.store_file(file_path, job_id, "jokbo")
                jokbo_keys.append(key)
                if not storage_manager.verify_file_available(key):
                    raise HTTPException(status_code=503, detail=f"Storage unavailable for {f.filename}; please retry later")

            for f in lesson_files:
                file_path = temp_path / f.filename
                content = await f.read()
                file_path.write_bytes(content)
                key = storage_manager.store_file(file_path, job_id, "lesson")
                lesson_keys.append(key)
                if not storage_manager.verify_file_available(key):
                    raise HTTPException(status_code=503, detail=f"Storage unavailable for {f.filename}; please retry later")

        try:
            storage_manager.refresh_ttls(jokbo_keys + lesson_keys)
        except Exception:
            pass

        effective_multi = multi_api_form if multi_api_form is not None else multi_api
        effective_min_rel = None
        try:
            eff = min_relevance_form if min_relevance_form is not None else min_relevance
            if eff is not None:
                effective_min_rel = max(0, min(int(eff), 110))
        except Exception:
            effective_min_rel = None

        metadata = {
            "jokbo_keys": jokbo_keys,
            "lesson_keys": lesson_keys,
            "model": model,
            "multi_api": effective_multi,
            "min_relevance": effective_min_rel,
            "user_id": user_id,
        }
        storage_manager.store_job_metadata(job_id, metadata)
        if user_id:
            storage_manager.add_user_job(user_id, job_id)

        task = celery_app.send_task(
            "tasks.run_lesson_analysis",
            args=[job_id],
            kwargs={"model_type": model, "multi_api": effective_multi},
        )
        try:
            storage_manager.set_job_task(job_id, task.id)
        except Exception:
            pass
        return {"job_id": job_id, "task_id": task.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"File upload failed: {str(e)}")


@router.post("/analyze/batch", status_code=202)
async def analyze_batch(
    request: Request,
    jokbo_files: list[UploadFile] = File(...),
    lesson_files: list[UploadFile] = File(...),
    mode: str = Query("jokbo-centric", regex="^(jokbo-centric|lesson-centric)$"),
    model: Optional[str] = Query("flash", regex="^(flash|pro)$"),
    password: Optional[str] = Query(None),
    multi_api: bool = Query(False),
    min_relevance: Optional[int] = Query(80, ge=0, le=110),
    multi_api_form: Optional[bool] = Form(None),
    min_relevance_form: Optional[int] = Form(None),
    user_id: Optional[str] = Query(None),
):
    """Submit a batch job that fans out into isolated subtasks."""
    job_id = str(uuid.uuid4())
    storage_manager = request.app.state.storage_manager
    try:
        if model == "pro" and password != PRO_MODEL_PASSWORD:
            raise HTTPException(status_code=403, detail="Invalid model password")

        for f in jokbo_files + lesson_files:
            if f.size and f.size > MAX_FILE_SIZE:
                raise HTTPException(status_code=413, detail=f"File {f.filename} exceeds maximum size of 50MB")

        jokbo_keys: list[str] = []
        lesson_keys: list[str] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            tdir = Path(temp_dir)
            for f in jokbo_files:
                p = tdir / f.filename
                content = await f.read()
                p.write_bytes(content)
                k = storage_manager.store_file(p, job_id, "jokbo")
                jokbo_keys.append(k)
                if not storage_manager.verify_file_available(k):
                    raise HTTPException(status_code=503, detail=f"Storage unavailable for {f.filename}; please retry later")
            for f in lesson_files:
                p = tdir / f.filename
                content = await f.read()
                p.write_bytes(content)
                k = storage_manager.store_file(p, job_id, "lesson")
                lesson_keys.append(k)
                if not storage_manager.verify_file_available(k):
                    raise HTTPException(status_code=503, detail=f"Storage unavailable for {f.filename}; please retry later")

        try:
            storage_manager.refresh_ttls(jokbo_keys + lesson_keys)
        except Exception:
            pass

        effective_multi = multi_api_form if multi_api_form is not None else multi_api
        effective_min_rel = None
        try:
            eff = min_relevance_form if min_relevance_form is not None else min_relevance
            if eff is not None:
                effective_min_rel = max(0, min(int(eff), 110))
        except Exception:
            effective_min_rel = None

        metadata = {
            "mode": mode,
            "jokbo_keys": jokbo_keys,
            "lesson_keys": lesson_keys,
            "model": model,
            "multi_api": effective_multi,
            "min_relevance": effective_min_rel,
            "user_id": user_id,
            "batch": True,
        }
        storage_manager.store_job_metadata(job_id, metadata)
        if user_id:
            storage_manager.add_user_job(user_id, job_id)

        sub_count = len(jokbo_keys) if mode == "jokbo-centric" else len(lesson_keys)
        if sub_count == 0:
            raise HTTPException(status_code=400, detail="No input files provided for batch")
        storage_manager.init_progress(job_id, sub_count, f"배치 시작: 총 {sub_count}건")

        header = []
        if mode == "jokbo-centric":
            for idx, pk in enumerate(jokbo_keys):
                header.append(
                    celery_app.signature(
                        "tasks.batch_analyze_single",
                        args=[job_id, mode, idx, pk, lesson_keys],
                        kwargs={"model_type": model, "min_relevance": effective_min_rel, "multi_api": effective_multi},
                        queue="analysis",
                    )
                )
        else:
            for idx, pk in enumerate(lesson_keys):
                header.append(
                    celery_app.signature(
                        "tasks.batch_analyze_single",
                        args=[job_id, mode, idx, pk, jokbo_keys],
                        kwargs={"model_type": model, "min_relevance": effective_min_rel, "multi_api": effective_multi},
                        queue="analysis",
                    )
                )

        callback = celery_app.signature("tasks.aggregate_batch", args=[job_id], queue="analysis")
        async_result = chord(group(header))(callback)

        try:
            storage_manager.set_job_task(job_id, async_result.id)
        except Exception:
            pass
        return {"job_id": job_id, "task_id": async_result.id, "subtasks": sub_count}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Batch submission failed: {str(e)}")


@router.post("/analyze/partial-jokbo", status_code=202)
async def analyze_partial_jokbo(
    request: Request,
    jokbo_files: list[UploadFile] = File(...),
    lesson_files: list[UploadFile] = File(...),
    # Align parameters with other modes for consistent UX
    model: Optional[str] = Query("flash", regex="^(flash|pro)$"),
    password: Optional[str] = Query(None),
    multi_api: bool = Query(False),
    # also allow form fallbacks if clients send as multipart fields
    multi_api_form: Optional[bool] = Form(None),
    user_id: Optional[str] = Query(None),
):
    """Endpoint to generate a partial jokbo PDF."""

    job_id = str(uuid.uuid4())
    storage_manager = request.app.state.storage_manager

    try:
        # Gate pro model behind password for parity with other endpoints
        if model == "pro" and password != PRO_MODEL_PASSWORD:
            raise HTTPException(status_code=403, detail="Invalid model password")

        for f in jokbo_files + lesson_files:
            if f.size and f.size > MAX_FILE_SIZE:
                raise HTTPException(
                    status_code=413,
                    detail=f"File {f.filename} exceeds maximum size of 50MB",
                )

        jokbo_keys: list[str] = []
        lesson_keys: list[str] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            tdir = Path(temp_dir)
            for f in jokbo_files:
                p = tdir / f.filename
                content = await f.read()
                p.write_bytes(content)
                k = storage_manager.store_file(p, job_id, "jokbo")
                jokbo_keys.append(k)
            for f in lesson_files:
                p = tdir / f.filename
                content = await f.read()
                p.write_bytes(content)
                k = storage_manager.store_file(p, job_id, "lesson")
                lesson_keys.append(k)

        # Resolve effective multi-API toggle (form overrides query if provided)
        effective_multi = multi_api_form if multi_api_form is not None else multi_api

        metadata = {
            "jokbo_keys": jokbo_keys,
            "lesson_keys": lesson_keys,
            "model": model,
            "multi_api": effective_multi,
            "user_id": user_id,
        }
        storage_manager.store_job_metadata(job_id, metadata)
        if user_id:
            storage_manager.add_user_job(user_id, job_id)

        task = celery_app.send_task(
            "tasks.generate_partial_jokbo",
            args=[job_id],
            kwargs={"model_type": model, "multi_api": effective_multi},
        )
        try:
            storage_manager.set_job_task(job_id, task.id)
        except Exception:
            pass
        return {"job_id": job_id, "task_id": task.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"File upload failed: {str(e)}")
