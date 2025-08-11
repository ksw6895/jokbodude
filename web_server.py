# web_server.py
import os
import shutil
import uuid
import tempfile
from pathlib import Path
from typing import Optional
from datetime import datetime
from fastapi import FastAPI, File, UploadFile, HTTPException, Form, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from celery import Celery
from storage_manager import StorageManager

# --- Configuration ---
# Use an environment variable for the storage path, essential for Render.
STORAGE_PATH = Path(os.getenv("RENDER_STORAGE_PATH", "persistent_storage"))
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# --- App & Celery Initialization ---
app = FastAPI(title="JokboDude API", version="1.0.0")

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

# Initialize storage manager
storage_manager = StorageManager(REDIS_URL)

# --- Helper Functions ---
def save_uploaded_file(upload_file: UploadFile, destination_dir: Path) -> Path:
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination_path = destination_dir / upload_file.filename
    with destination_path.open("wb") as buffer:
        shutil.copyfileobj(upload_file.file, buffer)
    return destination_path

# --- API Endpoints ---
@app.get("/")
def read_root():
    return FileResponse('frontend/index.html')

@app.post("/analyze/jokbo-centric", status_code=202)
async def analyze_jokbo_centric(
    jokbo_files: list[UploadFile] = File(...), 
    lesson_files: list[UploadFile] = File(...),
    model: Optional[str] = Query("flash", regex="^(pro|flash|flash-lite)$")
):
    job_id = str(uuid.uuid4())

    try:
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
            
            # Process lesson files
            for f in lesson_files:
                file_path = temp_path / f.filename
                content = await f.read()
                file_path.write_bytes(content)
                file_key = storage_manager.store_file(file_path, job_id, "lesson")
                lesson_keys.append(file_key)
        
        # Store job metadata
        metadata = {
            "jokbo_keys": jokbo_keys,
            "lesson_keys": lesson_keys,
            "model": model
        }
        storage_manager.store_job_metadata(job_id, metadata)

        # Send the processing task to the Celery worker
        task = celery_app.send_task(
            "tasks.run_jokbo_analysis",
            args=[job_id],
            kwargs={"model_type": model}
        )
        return {"job_id": job_id, "task_id": task.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"File upload failed: {str(e)}")

@app.post("/analyze/lesson-centric", status_code=202)
async def analyze_lesson_centric(
    jokbo_files: list[UploadFile] = File(...), 
    lesson_files: list[UploadFile] = File(...),
    model: Optional[str] = Query("flash", regex="^(pro|flash|flash-lite)$")
):
    job_id = str(uuid.uuid4())
    storage_manager = StorageManager()

    try:
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
        
        lesson_keys = []
        for file in lesson_files:
            saved_path = save_uploaded_file(file, Path("/tmp") / job_id / "lesson")
            key = storage_manager.store_file(saved_path, job_id, "lesson")
            lesson_keys.append(key)
        
        # Store metadata in Redis
        metadata = {
            "jokbo_keys": jokbo_keys,
            "lesson_keys": lesson_keys,
            "model": model
        }
        storage_manager.store_job_metadata(job_id, metadata)

        # Send the processing task to the Celery worker with model selection
        task = celery_app.send_task(
            "tasks.run_lesson_analysis",
            args=[job_id],
            kwargs={"model_type": model}
        )
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
def get_result_file(job_id: str):
    # Get result from Redis
    result_keys = storage_manager.redis_client.keys(f"result:{job_id}:*")
    if not result_keys:
        raise HTTPException(status_code=404, detail="Result not found or job not complete.")
    
    # Get the first result
    result_key = result_keys[0].decode() if isinstance(result_keys[0], bytes) else result_keys[0]
    content = storage_manager.get_result(result_key)
    
    if not content:
        raise HTTPException(status_code=404, detail="Generated PDF not found.")
    
    filename = result_key.split(":")[-1]
    return Response(
        content=content,
        media_type='application/pdf',
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@app.get("/results/{job_id}")
def list_result_files(job_id: str):
    # Get result keys from Redis
    result_keys = storage_manager.redis_client.keys(f"result:{job_id}:*")
    if not result_keys:
        raise HTTPException(status_code=404, detail="Result not found or job not complete.")
    
    # Extract filenames from keys
    files = []
    for key in result_keys:
        key_str = key.decode() if isinstance(key, bytes) else key
        filename = key_str.split(":")[-1]
        files.append(filename)
    
    return {"files": files}

@app.get("/result/{job_id}/{filename}")
def get_specific_result_file(job_id: str, filename: str):
    result_key = f"result:{job_id}:{filename}"
    content = storage_manager.get_result(result_key)
    
    if not content:
        raise HTTPException(status_code=404, detail="File not found.")
    
    return Response(
        content=content,
        media_type='application/pdf',
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@app.get("/progress/{job_id}")
def get_job_progress(job_id: str):
    """Get job progress information"""
    progress_data = storage_manager.get_progress(job_id)
    if not progress_data:
        raise HTTPException(status_code=404, detail="Progress information not found")
    return progress_data

@app.get("/health")
async def health_check():
    """Health check endpoint for monitoring"""
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