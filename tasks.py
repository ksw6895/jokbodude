# tasks.py
import os
import tempfile
from pathlib import Path
from celery import Celery
from typing import Optional
from config import create_model, configure_api, API_KEYS
from pdf_processor.core.processor import PDFProcessor
from pdf_creator import PDFCreator
from storage_manager import StorageManager
from pdf_processor.pdf.operations import PDFOperations

# --- Configuration ---
# Use /tmp for initial file storage if persistent storage not available
STORAGE_PATH = Path(os.getenv("RENDER_STORAGE_PATH", "/tmp/persistent_storage"))
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Ensure storage path exists
STORAGE_PATH.mkdir(parents=True, exist_ok=True)

# Check if multi-API mode is available
USE_MULTI_API = len(API_KEYS) > 1 if 'API_KEYS' in globals() else False
MODEL_TYPE = os.getenv("GEMINI_MODEL", "pro")  # Allow model selection via env var

# --- Celery Initialization with Configuration ---
celery_app = Celery("tasks")
celery_app.config_from_object('celeryconfig')

# Fix deprecated warnings for Celery 6.0
celery_app.conf.broker_connection_retry_on_startup = True
celery_app.conf.worker_cancel_long_running_tasks_on_connection_loss = True

# --- Analysis Tasks ---
@celery_app.task(name="tasks.run_jokbo_analysis")
def run_jokbo_analysis(job_id: str, model_type: str = None, multi_api: Optional[bool] = None):
    """Run jokbo-centric analysis"""
    storage_manager = StorageManager()
    
    try:
        # Get job metadata from Redis
        metadata = storage_manager.get_job_metadata(job_id)
        if not metadata:
            raise Exception(f"Job metadata not found for {job_id}")
        
        jokbo_keys = metadata["jokbo_keys"]
        lesson_keys = metadata["lesson_keys"]
        
        # Determine multi-API usage
        meta_multi = None
        if isinstance(metadata, dict):
            meta_multi = metadata.get("multi_api")
        use_multi = meta_multi if meta_multi is not None else (multi_api if multi_api is not None else USE_MULTI_API)

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
                storage_manager.save_file_locally(key, local_path)
                jokbo_paths.append(str(local_path))
            
            lesson_paths = []
            for key in lesson_keys:
                filename = key.split(":")[-2]  # Extract filename from key
                local_path = lesson_dir / filename
                storage_manager.save_file_locally(key, local_path)
                lesson_paths.append(str(local_path))
            
            # Initialize chunk-based progress
            try:
                total_jokbos = len(jokbo_paths)
                lesson_chunks = 0
                for lp in lesson_paths:
                    lesson_chunks += len(PDFOperations.split_pdf_for_chunks(lp))
                total_chunks = max(1, total_jokbos * lesson_chunks)
                storage_manager.init_progress(job_id, total_chunks, f"총 청크: {total_chunks}")
            except Exception:
                storage_manager.init_progress(job_id, 1, "진행률 초기화")

            # Configure API and create model
            configure_api()
            selected_model = model_type or MODEL_TYPE
            model = create_model(selected_model)
            # PDFProcessor does not take a 'multi_api' arg; choose methods conditionally
            processor = PDFProcessor(model, session_id=job_id)
            creator = PDFCreator()
            
            # Process each jokbo file with progress tracking
            total_jokbos = len(jokbo_paths)
            for idx, jokbo_path_str in enumerate(jokbo_paths, 1):
                jokbo_path = Path(jokbo_path_str)
                
                # Update status message (percent driven by chunk progress)
                try:
                    cur = storage_manager.get_progress(job_id) or {}
                    storage_manager.update_progress(job_id, int(cur.get('progress', 0) or 0), f"분석 중: {jokbo_path.name}")
                except Exception:
                    pass
                
                # Analyze jokbo against all lessons (use multi-API when enabled)
                if use_multi and len(API_KEYS) > 1:
                    analysis_result = processor.analyze_jokbo_centric_multi_api(
                        lesson_paths, jokbo_path_str, api_keys=API_KEYS
                    )
                else:
                    analysis_result = processor.analyze_jokbo_centric(lesson_paths, jokbo_path_str)
                
                if "error" in analysis_result:
                    raise Exception(f"Analysis error for {jokbo_path.name}: {analysis_result['error']}")
                
                # Update status message for PDF generation
                try:
                    cur = storage_manager.get_progress(job_id) or {}
                    storage_manager.update_progress(job_id, int(cur.get('progress', 0) or 0), f"PDF 생성 중: {jokbo_path.name}")
                except Exception:
                    pass
                
                # Generate output PDF
                output_filename = f"jokbo_centric_{jokbo_path.stem}_all_lessons.pdf"
                output_path = output_dir / output_filename
                
                creator.create_jokbo_centric_pdf(
                    str(jokbo_path),
                    analysis_result,
                    str(output_path),
                    str(lesson_dir)
                )
                
                # Store result in Redis
                storage_manager.store_result(job_id, output_path)
            
            # Clean up processor resources
            processor.cleanup_session()
            
            # Finalize progress to 100%
            try:
                storage_manager.update_progress(job_id, 100, "완료")
            except Exception:
                pass

            return {
                "status": "Complete",
                "job_id": job_id,
                "files_generated": len(list(output_dir.glob("*.pdf")))
            }
            
    except Exception as e:
        # Celery will catch this exception and store it in the task result backend
        raise e

@celery_app.task(name="tasks.run_lesson_analysis")
def run_lesson_analysis(job_id: str, model_type: str = None, multi_api: Optional[bool] = None):
    """Run lesson-centric analysis"""
    storage_manager = StorageManager()
    
    try:
        # Get job metadata from Redis
        metadata = storage_manager.get_job_metadata(job_id)
        if not metadata:
            raise Exception(f"Job metadata not found for {job_id}")
        
        jokbo_keys = metadata["jokbo_keys"]
        lesson_keys = metadata["lesson_keys"]
        
        # Determine multi-API usage
        meta_multi = None
        if isinstance(metadata, dict):
            meta_multi = metadata.get("multi_api")
        use_multi = meta_multi if meta_multi is not None else (multi_api if multi_api is not None else USE_MULTI_API)

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
                storage_manager.save_file_locally(key, local_path)
                jokbo_paths.append(str(local_path))
            
            lesson_paths = []
            for key in lesson_keys:
                filename = key.split(":")[-2]  # Extract filename from key
                local_path = lesson_dir / filename
                storage_manager.save_file_locally(key, local_path)
                lesson_paths.append(str(local_path))
            
            # Initialize chunk-based progress (lesson-centric)
            try:
                total_lessons = len(lesson_paths)
                total_jokbos = len(jokbo_paths)
                lesson_chunks = 0
                for lp in lesson_paths:
                    lesson_chunks += len(PDFOperations.split_pdf_for_chunks(lp))
                total_chunks = max(1, lesson_chunks * max(1, total_jokbos))
                storage_manager.init_progress(job_id, total_chunks, f"총 청크: {total_chunks}")
            except Exception:
                storage_manager.init_progress(job_id, 1, "진행률 초기화")

            # Configure API and create model
            configure_api()
            selected_model = model_type or MODEL_TYPE
            model = create_model(selected_model)
            # PDFProcessor does not take a 'multi_api' arg; choose methods conditionally
            processor = PDFProcessor(model, session_id=job_id)
            creator = PDFCreator()
            
            # Process each lesson file with progress tracking
            total_lessons = len(lesson_paths)
            for idx, lesson_path_str in enumerate(lesson_paths, 1):
                lesson_path = Path(lesson_path_str)
                
                # Update status message (percent driven by chunk progress)
                try:
                    cur = storage_manager.get_progress(job_id) or {}
                    storage_manager.update_progress(job_id, int(cur.get('progress', 0) or 0), f"분석 중: {lesson_path.name}")
                except Exception:
                    pass
                
                # Analyze lesson against all jokbos (use multi-API when enabled)
                if use_multi and len(API_KEYS) > 1:
                    analysis_result = processor.analyze_lesson_centric_multi_api(
                        jokbo_paths, lesson_path_str, api_keys=API_KEYS
                    )
                else:
                    analysis_result = processor.analyze_lesson_centric(jokbo_paths, lesson_path_str)
                
                if "error" in analysis_result:
                    raise Exception(f"Analysis error for {lesson_path.name}: {analysis_result['error']}")
                
                # Update status message for PDF generation
                try:
                    cur = storage_manager.get_progress(job_id) or {}
                    storage_manager.update_progress(job_id, int(cur.get('progress', 0) or 0), f"PDF 생성 중: {lesson_path.name}")
                except Exception:
                    pass
                
                # Generate output PDF
                output_filename = f"filtered_{lesson_path.stem}_all_jokbos.pdf"
                output_path = output_dir / output_filename
                
                creator.create_lesson_centric_pdf(
                    str(lesson_path),
                    analysis_result,
                    str(output_path),
                    str(jokbo_dir)
                )
                
                # Store result in Redis
                storage_manager.store_result(job_id, output_path)
            
            # Clean up processor resources
            processor.cleanup_session()
            
            # Finalize progress to 100%
            try:
                storage_manager.update_progress(job_id, 100, "완료")
            except Exception:
                pass
            
            return {
                "status": "Complete",
                "job_id": job_id,
                "files_generated": len(list(output_dir.glob("*.pdf")))
            }
            
    except Exception as e:
        # Celery will catch this exception and store it in the task result backend
        raise e
