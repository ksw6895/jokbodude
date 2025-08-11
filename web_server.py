# web_server.py
import os
import shutil
import uuid
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, File, UploadFile, HTTPException, Form, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from celery import Celery

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

celery_app = Celery("tasks", broker=REDIS_URL, backend=REDIS_URL)

# File size limit: 50MB
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB in bytes

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
    job_dir = STORAGE_PATH / job_id

    try:
        # Validate file sizes
        for f in jokbo_files + lesson_files:
            if f.size and f.size > MAX_FILE_SIZE:
                raise HTTPException(
                    status_code=413,
                    detail=f"File {f.filename} exceeds maximum size of 50MB"
                )
        
        jokbo_paths_str = [str(save_uploaded_file(f, job_dir / "jokbo")) for f in jokbo_files]
        lesson_paths_str = [str(save_uploaded_file(f, job_dir / "lesson")) for f in lesson_files]

        # Send the processing task to the Celery worker with model selection
        task = celery_app.send_task(
            "tasks.run_jokbo_analysis",
            args=[job_id, jokbo_paths_str, lesson_paths_str],
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
    job_dir = STORAGE_PATH / job_id

    try:
        # Validate file sizes
        for f in jokbo_files + lesson_files:
            if f.size and f.size > MAX_FILE_SIZE:
                raise HTTPException(
                    status_code=413,
                    detail=f"File {f.filename} exceeds maximum size of 50MB"
                )
        
        jokbo_paths_str = [str(save_uploaded_file(f, job_dir / "jokbo")) for f in jokbo_files]
        lesson_paths_str = [str(save_uploaded_file(f, job_dir / "lesson")) for f in lesson_files]

        # Send the processing task to the Celery worker with model selection
        task = celery_app.send_task(
            "tasks.run_lesson_analysis",
            args=[job_id, jokbo_paths_str, lesson_paths_str],
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
    output_dir = STORAGE_PATH / job_id / "output"
    if not output_dir.exists():
        raise HTTPException(status_code=404, detail="Result not found or job not complete.")
    
    output_files = list(output_dir.glob("*.pdf"))
    if not output_files:
        raise HTTPException(status_code=404, detail="Generated PDF not found.")

    return FileResponse(output_files[0], media_type='application/pdf', filename=output_files[0].name)

@app.get("/results/{job_id}")
def list_result_files(job_id: str):
    output_dir = STORAGE_PATH / job_id / "output"
    if not output_dir.exists():
        raise HTTPException(status_code=404, detail="Result not found or job not complete.")
    
    output_files = list(output_dir.glob("*.pdf"))
    if not output_files:
        raise HTTPException(status_code=404, detail="Generated PDFs not found.")
    
    return {"files": [f.name for f in output_files]}

@app.get("/result/{job_id}/{filename}")
def get_specific_result_file(job_id: str, filename: str):
    file_path = STORAGE_PATH / job_id / "output" / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found.")
    
    return FileResponse(file_path, media_type='application/pdf', filename=filename)