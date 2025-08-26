# web_server.py
import os
import shutil
import uuid
import tempfile
from pathlib import Path
from typing import Optional
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, File, UploadFile, HTTPException, Form, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from celery import Celery
from celery import group, chord
from storage_manager import StorageManager
from urllib.parse import quote
import re
from pdf_processor.pdf.cache import get_global_cache, clear_global_cache
import time

# --- Configuration ---
# Use an environment variable for the storage path, essential for Render.
STORAGE_PATH = Path(os.getenv("RENDER_STORAGE_PATH", "persistent_storage"))
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
PRO_MODEL_PASSWORD = os.getenv("PRO_MODEL_PASSWORD", "")

# --- App & Celery Initialization ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Application startup: initialize resources
    print("Application startup: Initializing resources...")
    # Initialize StorageManager and PDF cache
    app.state.storage_manager = StorageManager(REDIS_URL)
    get_global_cache()
    # Best-effort auto-prune of old debug/temp files on startup (configurable)
    try:
        _ret_hours = int(os.getenv("DEBUG_RETENTION_HOURS", "168"))  # 7 days default
        _prune_dirs = [Path("output/debug"), Path("output/temp/sessions")]
        now = time.time()
        for d in _prune_dirs:
            if not d.exists():
                continue
            for p in d.rglob("*"):
                try:
                    if p.is_file():
                        age_hours = (now - p.stat().st_mtime) / 3600.0
                        if age_hours >= _ret_hours:
                            p.unlink(missing_ok=True)
                    # Remove empty dirs opportunistically
                    if p.is_dir():
                        try:
                            next(p.iterdir())
                        except StopIteration:
                            p.rmdir()
                except Exception:
                    continue
    except Exception:
        pass
    yield
    # Application shutdown: clean up resources
    print("Application shutdown: Cleaning up resources...")
    clear_global_cache()
    sm = getattr(app.state, "storage_manager", None)
    if sm is not None:
        try:
            sm.close()
        except Exception:
            pass

app = FastAPI(title="JokboDude API", version="1.0.0", lifespan=lifespan)

# Add CORS middleware for web frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify actual origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

celery_app = Celery("tasks")
celery_app.config_from_object('celeryconfig')

# File size limit: 50MB
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB in bytes

# StorageManager is initialized in lifespan and accessed via app.state

# --- Helper Functions ---
def save_uploaded_file(upload_file: UploadFile, destination_dir: Path) -> Path:
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination_path = destination_dir / upload_file.filename
    with destination_path.open("wb") as buffer:
        shutil.copyfileobj(upload_file.file, buffer)
    return destination_path

def build_content_disposition(original_name: str) -> str:
    """Build a RFC 6266 compliant Content-Disposition header value.

    Provides an ASCII-only fallback filename plus UTF-8 filename* parameter
    to safely handle non-ASCII characters (e.g., Korean) without header
    encoding errors.
    """
    # Preserve extension if present
    match = re.search(r"(\.[A-Za-z0-9]+)$", original_name)
    ext = match.group(1) if match else ""
    base = original_name[:-len(ext)] if ext else original_name

    # Create ASCII-only fallback: replace non-safe chars with '_'
    # Keep common safe chars: letters, digits, dot, underscore, hyphen
    fallback_base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("_") or "download"
    # Trim to a reasonable length to avoid overly long headers
    if len(fallback_base) > 150:
        fallback_base = fallback_base[:150] + "_"
    fallback = f"{fallback_base}{ext or '.pdf'}"

    # UTF-8 encoded filename* per RFC 5987/6266
    utf8_star = quote(original_name)

    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{utf8_star}"

# --- API Endpoints ---
@app.get("/")
def read_root():
    return FileResponse('frontend/index.html')

@app.get("/guide")
def read_guide():
    return FileResponse('frontend/guide.html')

@app.get("/config")
def get_config(password: Optional[str] = None):
    """Expose server capabilities for the frontend UI."""
    try:
        # Lazy import to avoid hard dependency at module import time
        from config import API_KEYS as _API_KEYS  # type: ignore
        keys_count = len(_API_KEYS) if isinstance(_API_KEYS, list) else (1 if _API_KEYS else 0)
    except Exception:
        keys_count = 0
    models = ["flash"]
    if password and PRO_MODEL_PASSWORD and password == PRO_MODEL_PASSWORD:
        models.append("pro")
    return {
        "multi_api_available": keys_count > 1,
        "api_keys_count": keys_count,
        "models": models,
    }

@app.post("/analyze/jokbo-centric", status_code=202)
async def analyze_jokbo_centric(
    request: Request,
    jokbo_files: list[UploadFile] = File(...), 
    lesson_files: list[UploadFile] = File(...),
    model: Optional[str] = Query("flash", regex="^(flash|pro)$"),
    password: Optional[str] = Query(None),
    multi_api: bool = Query(False),
    min_relevance: Optional[int] = Query(80, ge=0, le=110),
    # Also accept multi_api via multipart form for robustness
    multi_api_form: Optional[bool] = Form(None),
    min_relevance_form: Optional[int] = Form(None),
    user_id: Optional[str] = Query(None)
):
    job_id = str(uuid.uuid4())
    storage_manager = request.app.state.storage_manager

    try:
        # Validate model password if needed
        if model == "pro" and password != PRO_MODEL_PASSWORD:
            raise HTTPException(status_code=403, detail="Invalid model password")

        # Validate file sizes
        for f in jokbo_files + lesson_files:
            if f.size and f.size > MAX_FILE_SIZE:
                raise HTTPException(
                    status_code=413,
                    detail=f"File {f.filename} exceeds maximum size of 50MB"
                )
        
        # Save files to temporary location and store in Redis
        jokbo_keys = []
        lesson_keys = []
        
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            
            # Process jokbo files
            for f in jokbo_files:
                file_path = temp_path / f.filename
                content = await f.read()
                file_path.write_bytes(content)
                file_key = storage_manager.store_file(file_path, job_id, "jokbo")
                jokbo_keys.append(file_key)
                # Fail-fast: verify stored and TTL is healthy
                if not storage_manager.verify_file_available(file_key):
                    raise HTTPException(status_code=503, detail=f"Storage unavailable for {f.filename}; please retry later")
            
            # Process lesson files
            for f in lesson_files:
                file_path = temp_path / f.filename
                content = await f.read()
                file_path.write_bytes(content)
                file_key = storage_manager.store_file(file_path, job_id, "lesson")
                lesson_keys.append(file_key)
                # Fail-fast: verify stored and TTL is healthy
                if not storage_manager.verify_file_available(file_key):
                    raise HTTPException(status_code=503, detail=f"Storage unavailable for {f.filename}; please retry later")

        # Ensure TTLs are fresh before enqueueing
        try:
            storage_manager.refresh_ttls(jokbo_keys + lesson_keys)
        except Exception:
            pass
        
        # Determine effective multi_api value (form overrides query if provided)
        effective_multi = (multi_api_form if multi_api_form is not None else multi_api)
        effective_min_rel = None
        try:
            # Prefer form value if provided
            eff = min_relevance_form if min_relevance_form is not None else min_relevance
            if eff is not None:
                effective_min_rel = max(0, min(int(eff), 110))
        except Exception:
            effective_min_rel = None

        # Store job metadata
        metadata = {
            "jokbo_keys": jokbo_keys,
            "lesson_keys": lesson_keys,
            "model": model,
            "multi_api": effective_multi,
            "min_relevance": effective_min_rel,
            "user_id": user_id
        }
        storage_manager.store_job_metadata(job_id, metadata)
        if user_id:
            storage_manager.add_user_job(user_id, job_id)

        # Send the processing task to the Celery worker
        task = celery_app.send_task(
            "tasks.run_jokbo_analysis",
            args=[job_id],
            kwargs={"model_type": model, "multi_api": effective_multi}
        )
        try:
            storage_manager.set_job_task(job_id, task.id)
        except Exception:
            pass
        return {"job_id": job_id, "task_id": task.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"File upload failed: {str(e)}")

@app.post("/analyze/lesson-centric", status_code=202)
async def analyze_lesson_centric(
    request: Request,
    jokbo_files: list[UploadFile] = File(...), 
    lesson_files: list[UploadFile] = File(...),
    model: Optional[str] = Query("flash", regex="^(flash|pro)$"),
    password: Optional[str] = Query(None),
    multi_api: bool = Query(False),
    min_relevance: Optional[int] = Query(80, ge=0, le=110),
    # Also accept multi_api via multipart form for robustness
    multi_api_form: Optional[bool] = Form(None),
    min_relevance_form: Optional[int] = Form(None),
    user_id: Optional[str] = Query(None)
):
    job_id = str(uuid.uuid4())
    storage_manager = request.app.state.storage_manager

    try:
        # Validate model password if needed
        if model == "pro" and password != PRO_MODEL_PASSWORD:
            raise HTTPException(status_code=403, detail="Invalid model password")

        # Validate file sizes
        for f in jokbo_files + lesson_files:
            if f.size and f.size > MAX_FILE_SIZE:
                raise HTTPException(
                    status_code=413,
                    detail=f"File {f.filename} exceeds maximum size of 50MB"
                )
        
        # Save files and collect Redis keys
        jokbo_keys = []
        for file in jokbo_files:
            saved_path = save_uploaded_file(file, Path("/tmp") / job_id / "jokbo")
            key = storage_manager.store_file(saved_path, job_id, "jokbo")
            jokbo_keys.append(key)
            if not storage_manager.verify_file_available(key):
                raise HTTPException(status_code=503, detail=f"Storage unavailable for {file.filename}; please retry later")
        
        lesson_keys = []
        for file in lesson_files:
            saved_path = save_uploaded_file(file, Path("/tmp") / job_id / "lesson")
            key = storage_manager.store_file(saved_path, job_id, "lesson")
            lesson_keys.append(key)
            if not storage_manager.verify_file_available(key):
                raise HTTPException(status_code=503, detail=f"Storage unavailable for {file.filename}; please retry later")

        # Ensure TTLs are fresh before enqueueing
        try:
            storage_manager.refresh_ttls(jokbo_keys + lesson_keys)
        except Exception:
            pass
        
        # Determine effective multi_api value (form overrides query if provided)
        effective_multi = (multi_api_form if multi_api_form is not None else multi_api)
        effective_min_rel = None
        try:
            eff = min_relevance_form if min_relevance_form is not None else min_relevance
            if eff is not None:
                effective_min_rel = max(0, min(int(eff), 110))
        except Exception:
            effective_min_rel = None

        # Store metadata in Redis
        metadata = {
            "jokbo_keys": jokbo_keys,
            "lesson_keys": lesson_keys,
            "model": model,
            "multi_api": effective_multi,
            "min_relevance": effective_min_rel,
            "user_id": user_id
        }
        storage_manager.store_job_metadata(job_id, metadata)
        if user_id:
            storage_manager.add_user_job(user_id, job_id)

        # Send the processing task to the Celery worker with model selection
        task = celery_app.send_task(
            "tasks.run_lesson_analysis",
            args=[job_id],
            kwargs={"model_type": model, "multi_api": effective_multi}
        )
        try:
            storage_manager.set_job_task(job_id, task.id)
        except Exception:
            pass
        return {"job_id": job_id, "task_id": task.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"File upload failed: {str(e)}")

@app.get("/status/{task_id}")
def get_task_status(task_id: str):
    task_result = celery_app.AsyncResult(task_id)
    response = {"task_id": task_id, "status": task_result.status}
    if task_result.successful():
        response["result"] = task_result.get()
    elif task_result.failed():
        response["error"] = str(task_result.info)
    return response

@app.get("/result/{job_id}")
def get_result_file(request: Request, job_id: str):
    storage_manager = request.app.state.storage_manager
    # Prefer on-disk results; fallback to Redis
    files = storage_manager.list_result_files(job_id)
    if not files:
        raise HTTPException(status_code=404, detail="Result not found or job not complete.")
    filename = files[0]
    path = storage_manager.get_result_path(job_id, filename)
    if path and path.exists():
        disposition = build_content_disposition(filename)
        return FileResponse(path, media_type='application/pdf', filename=filename,
                            headers={"Content-Disposition": disposition})
    # fallback to Redis blob
    content = storage_manager.get_result(f"result:{job_id}:{filename}")
    if not content:
        raise HTTPException(status_code=404, detail="Generated PDF not found.")
    disposition = build_content_disposition(filename)
    return Response(content=content, media_type='application/pdf', headers={"Content-Disposition": disposition})

@app.get("/results/{job_id}")
def list_result_files(request: Request, job_id: str):
    storage_manager = request.app.state.storage_manager
    files = storage_manager.list_result_files(job_id)
    if not files:
        raise HTTPException(status_code=404, detail="Result not found or job not complete.")
    return {"files": files}

@app.get("/result/{job_id}/{filename}")
def get_specific_result_file(request: Request, job_id: str, filename: str):
    storage_manager = request.app.state.storage_manager
    # Try on-disk file first
    path = storage_manager.get_result_path(job_id, filename)
    if path and path.exists():
        disposition = build_content_disposition(filename)
        return FileResponse(path, media_type='application/pdf', filename=filename,
                            headers={"Content-Disposition": disposition})
    # Fallback to Redis
    result_key = f"result:{job_id}:{filename}"
    content = storage_manager.get_result(result_key)
    if not content:
        raise HTTPException(status_code=404, detail="File not found.")
    disposition = build_content_disposition(filename)
    return Response(content=content, media_type='application/pdf', headers={"Content-Disposition": disposition})

@app.delete("/result/{job_id}/{filename}")
def delete_specific_result_file(request: Request, job_id: str, filename: str):
    storage_manager = request.app.state.storage_manager
    removed = storage_manager.delete_result(job_id, filename)
    if not removed:
        raise HTTPException(status_code=404, detail="File not found")
    return {"status": "deleted", "job_id": job_id, "filename": filename}

@app.delete("/results/{job_id}")
def delete_all_result_files(request: Request, job_id: str):
    storage_manager = request.app.state.storage_manager
    count = storage_manager.delete_all_results(job_id)
    return {"status": "deleted", "job_id": job_id, "deleted_count": int(count)}

@app.post("/analyze/batch", status_code=202)
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
    user_id: Optional[str] = Query(None)
):
    """Submit a batch job that fans out into isolated subtasks.

    - mode=jokbo-centric: one subtask per jokbo vs all lessons
    - mode=lesson-centric: one subtask per lesson vs all jokbos
    """
    job_id = str(uuid.uuid4())
    storage_manager = request.app.state.storage_manager
    try:
        # Validate model password if needed
        if model == "pro" and password != PRO_MODEL_PASSWORD:
            raise HTTPException(status_code=403, detail="Invalid model password")

        # Validate sizes
        for f in jokbo_files + lesson_files:
            if f.size and f.size > MAX_FILE_SIZE:
                raise HTTPException(status_code=413, detail=f"File {f.filename} exceeds maximum size of 50MB")

        jokbo_keys: list[str] = []
        lesson_keys: list[str] = []
        # Save to temp then storage manager
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

        # Refresh TTLs
        try:
            storage_manager.refresh_ttls(jokbo_keys + lesson_keys)
        except Exception:
            pass

        # Effective options
        effective_multi = (multi_api_form if multi_api_form is not None else multi_api)
        effective_min_rel = None
        try:
            eff = min_relevance_form if min_relevance_form is not None else min_relevance
            if eff is not None:
                effective_min_rel = max(0, min(int(eff), 110))
        except Exception:
            effective_min_rel = None

        # Persist job metadata (for bookkeeping/UI)
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

        # Init coarse progress: one chunk per subtask
        sub_count = len(jokbo_keys) if mode == "jokbo-centric" else len(lesson_keys)
        if sub_count == 0:
            raise HTTPException(status_code=400, detail="No input files provided for batch")
        storage_manager.init_progress(job_id, sub_count, f"배치 시작: 총 {sub_count}건")

        # Build group of isolated subtasks
        header = []
        if mode == "jokbo-centric":
            for idx, pk in enumerate(jokbo_keys):
                header.append(
                    celery_app.signature(
                        "tasks.batch_analyze_single",
                        args=[job_id, mode, idx, pk, lesson_keys],
                        kwargs={
                            "model_type": model,
                            "min_relevance": effective_min_rel,
                            "multi_api": effective_multi,
                        },
                        queue="analysis",
                    )
                )
        else:
            for idx, pk in enumerate(lesson_keys):
                header.append(
                    celery_app.signature(
                        "tasks.batch_analyze_single",
                        args=[job_id, mode, idx, pk, jokbo_keys],
                        kwargs={
                            "model_type": model,
                            "min_relevance": effective_min_rel,
                            "multi_api": effective_multi,
                        },
                        queue="analysis",
                    )
                )

        # Aggregate with chord callback
        callback = celery_app.signature("tasks.aggregate_batch", args=[job_id], queue="analysis")
        async_result = chord(group(header))(callback)

        # Map job to the chord result id for status polling
        try:
            storage_manager.set_job_task(job_id, async_result.id)
        except Exception:
            pass
        return {"job_id": job_id, "task_id": async_result.id, "subtasks": sub_count}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Batch submission failed: {str(e)}")

@app.get("/progress/{job_id}")
def get_job_progress(request: Request, job_id: str):
    """Get job progress information"""
    storage_manager = request.app.state.storage_manager
    progress_data = storage_manager.get_progress(job_id)
    if not progress_data:
        raise HTTPException(status_code=404, detail="Progress information not found")
    return progress_data

@app.get("/user/{user_id}/jobs")
def get_user_jobs(request: Request, user_id: str, limit: int = 50):
    """List recent jobs for a user with status, progress, and files."""
    storage_manager = request.app.state.storage_manager
    job_ids = storage_manager.get_user_jobs(user_id, limit=limit) or []
    results = []
    for job_id in job_ids:
        entry = {"job_id": job_id}
        try:
            task_id = storage_manager.get_job_task(job_id)
            if task_id:
                tr = celery_app.AsyncResult(task_id)
                entry["status"] = tr.status
            else:
                entry["status"] = "UNKNOWN"
        except Exception:
            entry["status"] = "UNKNOWN"
        try:
            entry["progress"] = storage_manager.get_progress(job_id) or {}
        except Exception:
            entry["progress"] = None
        try:
            # List result filenames if any (Redis + disk)
            entry["files"] = storage_manager.list_result_files(job_id)
        except Exception:
            entry["files"] = []
        results.append(entry)
    return {"user_id": user_id, "jobs": results}

@app.post("/jobs/{job_id}/cancel")
def cancel_job(request: Request, job_id: str):
    """Request cancellation of a running or queued job."""
    storage_manager = request.app.state.storage_manager
    task_id = storage_manager.get_job_task(job_id)
    # Mark cancel flag for cooperative stops
    try:
        storage_manager.request_cancel(job_id)
    except Exception:
        pass
    revoked = False
    if task_id:
        try:
            celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")
            revoked = True
        except Exception:
            revoked = False
    return {"job_id": job_id, "task_id": task_id, "revoked": revoked}

@app.delete("/jobs/{job_id}")
def delete_job(request: Request, job_id: str, cancel_if_running: bool = True):
    """Delete all data for a job (results, metadata, progress). Optionally cancels first."""
    storage_manager = request.app.state.storage_manager
    # Optionally cancel
    if cancel_if_running:
        try:
            task_id = storage_manager.get_job_task(job_id)
            if task_id:
                tr = celery_app.AsyncResult(task_id)
                if tr.status in ("PENDING", "STARTED", "RETRY"):
                    celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")
        except Exception:
            pass
    # Remove results and keys
    try:
        storage_manager.delete_all_results(job_id)
    except Exception:
        pass
    storage_manager.cleanup_job(job_id)
    return {"status": "deleted", "job_id": job_id}

@app.get("/health")
async def health_check(request: Request):
    """Health check endpoint for monitoring"""
    storage_manager = request.app.state.storage_manager
    checks = {
        "web": "healthy",
        "redis": "unknown",
        "worker": "unknown"
    }
    
    # Check Redis connection
    try:
        storage_manager.redis_client.ping()
        checks["redis"] = "healthy"
    except Exception as e:
        checks["redis"] = f"unhealthy: {str(e)}"
    
    # Check Celery worker
    try:
        inspector = celery_app.control.inspect()
        active_workers = inspector.active()
        if active_workers:
            checks["worker"] = "healthy"
        else:
            checks["worker"] = "no active workers"
    except Exception as e:
        checks["worker"] = f"unhealthy: {str(e)}"
    
    # Determine overall status
    all_healthy = all(v == "healthy" for v in checks.values())
    status_code = 200 if all_healthy else 503
    
    return JSONResponse(
        content={
            "status": "healthy" if all_healthy else "degraded",
            "checks": checks,
            "timestamp": datetime.now().isoformat()
        },
        status_code=status_code
    )

# --- Admin: Cleanup / Cache Clear ---
def _delete_path_contents(base: Path, older_than_hours: int | None = None) -> dict:
    deleted_files = 0
    deleted_dirs = 0
    now = time.time()
    if not base.exists():
        return {"files": 0, "dirs": 0}
    # Walk bottom-up to delete files then empty dirs
    for p in sorted(base.rglob("*"), key=lambda x: len(str(x)), reverse=True):
        try:
            if p.is_file():
                if older_than_hours is not None:
                    age_hours = (now - p.stat().st_mtime) / 3600.0
                    if age_hours < older_than_hours:
                        continue
                p.unlink(missing_ok=True)
                deleted_files += 1
            elif p.is_dir():
                # Attempt to remove empty dirs
                try:
                    next(p.iterdir())
                except StopIteration:
                    p.rmdir()
                    deleted_dirs += 1
        except Exception:
            continue
    return {"files": deleted_files, "dirs": deleted_dirs}

@app.post("/admin/cleanup")
def admin_cleanup(
    clear_cache: bool = Query(True),
    clear_debug: bool = Query(True),
    clear_temp_sessions: bool = Query(True),
    older_than_hours: int | None = Query(None, ge=1, description="Only delete files older than this many hours")
):
    """Administrative cleanup: clear in-memory cache and prune debug/temp files.

    - clear_cache: clears in-memory PDF page-count cache
    - clear_debug: deletes files under output/debug (optionally older-than filter)
    - clear_temp_sessions: deletes files under output/temp/sessions (optionally older-than)
    - older_than_hours: if provided, only deletes files older than the threshold
    """
    summary: dict[str, dict | bool] = {}
    if clear_cache:
        try:
            clear_global_cache()
            summary["cache_cleared"] = True
        except Exception as e:
            summary["cache_cleared"] = {"error": str(e)}
    if clear_debug:
        try:
            summary["debug_deleted"] = _delete_path_contents(Path("output/debug"), older_than_hours)
        except Exception as e:
            summary["debug_deleted"] = {"error": str(e)}
    if clear_temp_sessions:
        try:
            summary["temp_sessions_deleted"] = _delete_path_contents(Path("output/temp/sessions"), older_than_hours)
        except Exception as e:
            summary["temp_sessions_deleted"] = {"error": str(e)}
    return summary
