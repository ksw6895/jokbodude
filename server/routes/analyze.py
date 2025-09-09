import tempfile
import uuid
from pathlib import Path
from typing import Optional

from celery import chord, group
from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile, Depends

from ..core import MAX_FILE_SIZE, celery_app
from ..utils import save_uploaded_file
from ._helpers import save_files_and_metadata, start_job
from .auth import require_user

router = APIRouter()


@router.post("/analyze/jokbo-centric", status_code=202)
async def analyze_jokbo_centric(
    request: Request,
    jokbo_files: list[UploadFile] = File(...),
    lesson_files: list[UploadFile] = File(...),
    model: Optional[str] = Query("flash", regex="^(flash|pro)$"),
    multi_api: bool = Query(False),
    min_relevance: Optional[int] = Query(80, ge=0, le=110),
    # Frontend sends as form fields named 'multi_api' and 'min_relevance'
    multi_api_form: Optional[bool] = Form(None, alias="multi_api"),
    min_relevance_form: Optional[int] = Form(None, alias="min_relevance"),
    user: dict = Depends(require_user),
):
    try:
        # Single-key mode removed: always run in Multi-API
        effective_multi = True
        effective_min_rel = None
        try:
            eff = min_relevance_form if min_relevance_form is not None else min_relevance
            if eff is not None:
                effective_min_rel = max(0, min(int(eff), 110))
        except Exception:
            effective_min_rel = None

        job_id, _ = await save_files_and_metadata(
            request,
            jokbo_files,
            lesson_files,
            mode="jokbo-centric",
            model=model,
            multi_api=bool(effective_multi),
            min_relevance=effective_min_rel,
            user_id=user.get("sub"),
        )
        task_id = start_job(request, job_id, mode="jokbo-centric", model=model, multi_api=bool(effective_multi))
        return {"job_id": job_id, "task_id": task_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"File upload failed: {str(e)}")


@router.post("/analyze/lesson-centric", status_code=202)
async def analyze_lesson_centric(
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
    try:
        effective_multi = True
        effective_min_rel = None
        try:
            eff = min_relevance_form if min_relevance_form is not None else min_relevance
            if eff is not None:
                effective_min_rel = max(0, min(int(eff), 110))
        except Exception:
            effective_min_rel = None

        job_id, _ = await save_files_and_metadata(
            request,
            jokbo_files,
            lesson_files,
            mode="lesson-centric",
            model=model,
            multi_api=bool(effective_multi),
            min_relevance=effective_min_rel,
            user_id=user.get("sub"),
        )
        task_id = start_job(request, job_id, mode="lesson-centric", model=model, multi_api=bool(effective_multi))
        return {"job_id": job_id, "task_id": task_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"File upload failed: {str(e)}")


@router.post("/analyze/batch", status_code=202)
async def analyze_batch(
    request: Request,
    jokbo_files: list[UploadFile] = File(...),
    lesson_files: list[UploadFile] = File(...),
    mode: str = Query("jokbo-centric", regex="^(jokbo-centric|lesson-centric)$"),
    model: Optional[str] = Query("flash", regex="^(flash|pro)$"),
    multi_api: bool = Query(False),
    min_relevance: Optional[int] = Query(80, ge=0, le=110),
    multi_api_form: Optional[bool] = Form(None, alias="multi_api"),
    min_relevance_form: Optional[int] = Form(None, alias="min_relevance"),
    user: dict = Depends(require_user),
):
    """Submit a batch job that fans out into isolated subtasks."""
    job_id = str(uuid.uuid4())
    storage_manager = request.app.state.storage_manager
    try:

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

        effective_multi = True
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
            "user_id": user.get("sub"),
            "batch": True,
        }
        storage_manager.store_job_metadata(job_id, metadata)
        user_id = user.get("sub")
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
    multi_api: bool = Query(False),
    # Optional relevance cutoff for consistency (currently informational)
    min_relevance: Optional[int] = Query(None, ge=0, le=110),
    # also allow form fallbacks if clients send as multipart fields
    multi_api_form: Optional[bool] = Form(None, alias="multi_api"),
    min_relevance_form: Optional[int] = Form(None, alias="min_relevance"),
    user: dict = Depends(require_user),
):
    """Endpoint to generate a partial jokbo PDF.

    Notes:
    - min_relevance is accepted/stored for UX consistency with other modes.
      The partial-jokbo analyzer primarily detects question spans; this
      value may be used by downstream components in future iterations.
    """

    try:
        # Resolve effective multi-API toggle (form overrides query if provided)
        effective_multi = True
        # Normalize min_relevance if provided (form overrides query)
        effective_min_rel: Optional[int] = None
        try:
            mr = min_relevance_form if min_relevance_form is not None else min_relevance
            if mr is not None:
                effective_min_rel = max(0, min(int(mr), 110))
        except Exception:
            effective_min_rel = None

        job_id, _ = await save_files_and_metadata(
            request,
            jokbo_files,
            lesson_files,
            mode="partial-jokbo",
            model=model,
            multi_api=bool(effective_multi),
            min_relevance=effective_min_rel,
            user_id=user.get("sub"),
        )
        task_id = start_job(request, job_id, mode="partial-jokbo", model=model, multi_api=bool(effective_multi))
        return {"job_id": job_id, "task_id": task_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"File upload failed: {str(e)}")
@router.post("/analyze/exam-only", status_code=202)
async def analyze_exam_only(
    request: Request,
    jokbo_files: list[UploadFile] = File(...),
    # No lesson files in exam-only mode
    model: Optional[str] = Query("flash", regex="^(flash|pro)$"),
    multi_api: bool = Query(False),
    # Accept these as form overrides too, for consistency with other endpoints
    multi_api_form: Optional[bool] = Form(None, alias="multi_api"),
    user: dict = Depends(require_user),
):
    try:
        effective_multi = True
        # exam-only has no lesson files
        job_id, _ = await save_files_and_metadata(
            request,
            jokbo_files,
            [],
            mode="exam-only",
            model=model,
            multi_api=bool(effective_multi),
            min_relevance=None,
            user_id=user.get("sub"),
        )
        task_id = start_job(request, job_id, mode="exam-only", model=model, multi_api=bool(effective_multi))
        return {"job_id": job_id, "task_id": task_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"File upload failed: {str(e)}")
