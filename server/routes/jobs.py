from fastapi import APIRouter, HTTPException, Request, Response, Depends
from fastapi.responses import FileResponse, RedirectResponse

from ..core import celery_app
from ..utils import build_content_disposition
from .auth import require_user

router = APIRouter()


def _ensure_owner(request: Request, job_id: str, user: dict) -> None:
    """Ensure the current session user owns the given job_id."""
    try:
        storage_manager = request.app.state.storage_manager
        owner = storage_manager.get_job_owner(job_id)
    except Exception:
        owner = None
    if not owner or owner != user.get("sub"):
        raise HTTPException(status_code=403, detail="Not authorized for this job")


@router.get("/status/{task_id}")
def get_task_status(task_id: str, user: dict = Depends(require_user)):
    task_result = celery_app.AsyncResult(task_id)
    response = {"task_id": task_id, "status": task_result.status}
    if task_result.successful():
        response["result"] = task_result.get()
    elif task_result.failed():
        response["error"] = str(task_result.info)
    return response


@router.get("/result/{job_id}")
def get_result_file(request: Request, job_id: str, user: dict = Depends(require_user)):
    _ensure_owner(request, job_id, user)
    storage_manager = request.app.state.storage_manager
    files = storage_manager.list_result_files(job_id)
    if not files:
        raise HTTPException(status_code=404, detail="Result not found or job not complete.")
    filename = files[0]
    path = storage_manager.get_result_path(job_id, filename)
    if path and path.exists():
        disposition = build_content_disposition(filename)
        return FileResponse(path, media_type="application/pdf", filename=filename, headers={"Content-Disposition": disposition})
    # If stored in object storage, redirect to a presigned URL
    try:
        disposition = build_content_disposition(filename)
        url = storage_manager.get_result_presigned_url(job_id, filename, content_disposition=disposition)
        if url:
            return RedirectResponse(url=url, status_code=302)
    except Exception:
        pass
    content = storage_manager.get_result(f"result:{job_id}:{filename}")
    if not content:
        raise HTTPException(status_code=404, detail="Generated PDF not found.")
    disposition = build_content_disposition(filename)
    return Response(content=content, media_type="application/pdf", headers={"Content-Disposition": disposition})


@router.get("/results/{job_id}")
def list_result_files(request: Request, job_id: str, user: dict = Depends(require_user)):
    _ensure_owner(request, job_id, user)
    storage_manager = request.app.state.storage_manager
    files = storage_manager.list_result_files(job_id)
    if not files:
        raise HTTPException(status_code=404, detail="Result not found or job not complete.")
    return {"files": files}


@router.get("/result/{job_id}/{filename}")
def get_specific_result_file(request: Request, job_id: str, filename: str, user: dict = Depends(require_user)):
    _ensure_owner(request, job_id, user)
    storage_manager = request.app.state.storage_manager
    path = storage_manager.get_result_path(job_id, filename)
    if path and path.exists():
        disposition = build_content_disposition(filename)
        return FileResponse(path, media_type="application/pdf", filename=filename, headers={"Content-Disposition": disposition})
    # If stored in object storage, redirect to a presigned URL
    try:
        disposition = build_content_disposition(filename)
        url = storage_manager.get_result_presigned_url(job_id, filename, content_disposition=disposition)
        if url:
            return RedirectResponse(url=url, status_code=302)
    except Exception:
        pass
    result_key = f"result:{job_id}:{filename}"
    content = storage_manager.get_result(result_key)
    if not content:
        raise HTTPException(status_code=404, detail="File not found.")
    disposition = build_content_disposition(filename)
    return Response(content=content, media_type="application/pdf", headers={"Content-Disposition": disposition})


@router.delete("/result/{job_id}/{filename}")
def delete_specific_result_file(request: Request, job_id: str, filename: str, user: dict = Depends(require_user)):
    _ensure_owner(request, job_id, user)
    storage_manager = request.app.state.storage_manager
    removed = storage_manager.delete_result(job_id, filename)
    if not removed:
        raise HTTPException(status_code=404, detail="File not found")
    return {"status": "deleted", "job_id": job_id, "filename": filename}


@router.delete("/results/{job_id}")
def delete_all_result_files(request: Request, job_id: str, user: dict = Depends(require_user)):
    _ensure_owner(request, job_id, user)
    storage_manager = request.app.state.storage_manager
    count = storage_manager.delete_all_results(job_id)
    return {"status": "deleted", "job_id": job_id, "deleted_count": int(count)}


@router.get("/progress/{job_id}")
def get_job_progress(request: Request, job_id: str, user: dict = Depends(require_user)):
    _ensure_owner(request, job_id, user)
    storage_manager = request.app.state.storage_manager
    progress_data = storage_manager.get_progress(job_id)
    if not progress_data:
        raise HTTPException(status_code=404, detail="Progress information not found")
    return progress_data


@router.get("/user/{user_id}/jobs")
def get_user_jobs(
    request: Request,
    user_id: str,
    limit: int = 50,
    include_preflight: bool = False,
    user: dict = Depends(require_user),
):
    if user_id != user.get("sub"):
        raise HTTPException(status_code=403, detail="Not authorized for this user")
    storage_manager = request.app.state.storage_manager
    job_ids = storage_manager.get_user_jobs(user_id, limit=limit) or []
    results = []
    for job_id in job_ids:
        entry = {"job_id": job_id}
        # Determine draft/preflight status
        is_draft = False
        try:
            meta = storage_manager.get_job_metadata(job_id) or {}
            is_draft = bool((meta or {}).get("preflight"))
        except Exception:
            is_draft = False
        if is_draft and not include_preflight:
            continue
        try:
            task_id = storage_manager.get_job_task(job_id)
            if task_id:
                tr = celery_app.AsyncResult(task_id)
                entry["status"] = tr.status
            else:
                # No task bound yet. Classify as DRAFT or derive from files if any
                if is_draft:
                    entry["status"] = "DRAFT"
                else:
                    # If results already exist, mark success
                    try:
                        if storage_manager.list_result_files(job_id):
                            entry["status"] = "SUCCESS"
                        else:
                            entry["status"] = "UNKNOWN"
                    except Exception:
                        entry["status"] = "UNKNOWN"
        except Exception:
            entry["status"] = "UNKNOWN"
        # If a cancel flag is present, surface as CANCELLED
        try:
            if storage_manager.is_cancelled(job_id):
                entry["status"] = "CANCELLED"
        except Exception:
            pass
        try:
            entry["progress"] = storage_manager.get_progress(job_id) or {}
        except Exception:
            entry["progress"] = None
        try:
            entry["files"] = storage_manager.list_result_files(job_id)
        except Exception:
            entry["files"] = []
        if is_draft:
            entry["draft"] = True
        results.append(entry)
    return {"user_id": user_id, "jobs": results}


@router.post("/jobs/{job_id}/cancel")
def cancel_job(request: Request, job_id: str, user: dict = Depends(require_user)):
    _ensure_owner(request, job_id, user)
    storage_manager = request.app.state.storage_manager
    task_id = storage_manager.get_job_task(job_id)
    try:
        storage_manager.request_cancel(job_id)
    except Exception:
        pass
    # If there's no task, treat this as a draft/unstarted job: cleanup immediately
    if not task_id:
        try:
            storage_manager.delete_all_results(job_id)
        except Exception:
            pass
        try:
            storage_manager.cleanup_job(job_id)
        except Exception:
            pass
        return {"job_id": job_id, "task_id": None, "revoked": False, "draft_deleted": True}
    revoked = False
    try:
        celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")
        revoked = True
    except Exception:
        revoked = False
    # Best-effort: mark progress as cancelled for UI
    try:
        storage_manager.update_progress(job_id, int((storage_manager.get_progress(job_id) or {}).get('progress', 0) or 0), "취소 요청됨")
    except Exception:
        pass
    return {"job_id": job_id, "task_id": task_id, "revoked": revoked}


@router.delete("/jobs/{job_id}")
def delete_job(request: Request, job_id: str, cancel_if_running: bool = True, user: dict = Depends(require_user)):
    storage_manager = request.app.state.storage_manager
    # Authorization: allow delete if (a) owner matches OR (b) the job id exists in this user's job list
    owner_ok = False
    try:
        owner = storage_manager.get_job_owner(job_id)
        owner_ok = bool(owner and owner == user.get("sub"))
    except Exception:
        owner_ok = False
    if not owner_ok:
        try:
            user_jobs = storage_manager.get_user_jobs(user.get("sub"), limit=1000) or []
            if job_id not in user_jobs:
                raise HTTPException(status_code=403, detail="Not authorized for this job")
        except HTTPException:
            raise
        except Exception:
            # As a safe fallback, require explicit ownership when we cannot verify membership
            raise HTTPException(status_code=403, detail="Not authorized for this job")
    if cancel_if_running:
        try:
            task_id = storage_manager.get_job_task(job_id)
            if task_id:
                tr = celery_app.AsyncResult(task_id)
                if tr.status in ("PENDING", "STARTED", "RETRY"):
                    celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")
        except Exception:
            pass
    try:
        storage_manager.delete_all_results(job_id)
    except Exception:
        pass
    storage_manager.cleanup_job(job_id)
    # Ensure removal from this user's visible list even if job->owner mapping is missing/expired
    try:
        storage_manager.remove_user_job(user.get("sub"), job_id)
    except Exception:
        pass
    return {"status": "deleted", "job_id": job_id}
