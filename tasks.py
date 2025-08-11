# tasks.py
import os
from pathlib import Path
from celery import Celery
from config import create_model, configure_api
from pdf_processor.core.processor import PDFProcessor
from pdf_creator import PDFCreator

# --- Configuration ---
STORAGE_PATH = Path(os.getenv("RENDER_STORAGE_PATH", "persistent_storage"))
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# --- Celery Initialization ---
celery_app = Celery("tasks", broker=REDIS_URL, backend=REDIS_URL)

# --- Analysis Tasks ---
@celery_app.task(name="tasks.run_jokbo_analysis")
def run_jokbo_analysis(job_id: str, jokbo_paths: list[str], lesson_paths: list[str]):
    """Run jokbo-centric analysis"""
    try:
        job_dir = STORAGE_PATH / job_id
        output_dir = job_dir / "output"
        output_dir.mkdir(exist_ok=True)

        # Configure API and create model inside the task
        configure_api()
        model = create_model("pro")  # You can make this configurable later
        processor = PDFProcessor(model, session_id=job_id)
        creator = PDFCreator()

        # Process each jokbo file
        for jokbo_path_str in jokbo_paths:
            jokbo_path = Path(jokbo_path_str)
            
            # Analyze jokbo against all lessons
            analysis_result = processor.analyze_jokbo_centric(lesson_paths, jokbo_path_str)
            
            if "error" in analysis_result:
                raise Exception(f"Analysis error for {jokbo_path.name}: {analysis_result['error']}")

            # Generate output PDF
            output_filename = f"jokbo_centric_{jokbo_path.stem}_all_lessons.pdf"
            output_path = output_dir / output_filename
            
            creator.create_jokbo_centric_pdf(
                str(jokbo_path),
                analysis_result,
                str(output_path),
                str(job_dir / "lesson")
            )
        
        # Clean up processor resources
        processor.cleanup()
        
        return {
            "status": "Complete",
            "output_path": str(output_dir),
            "files_generated": len(list(output_dir.glob("*.pdf")))
        }
    except Exception as e:
        # Celery will catch this exception and store it in the task result backend
        raise e

@celery_app.task(name="tasks.run_lesson_analysis")
def run_lesson_analysis(job_id: str, jokbo_paths: list[str], lesson_paths: list[str]):
    """Run lesson-centric analysis"""
    try:
        job_dir = STORAGE_PATH / job_id
        output_dir = job_dir / "output"
        output_dir.mkdir(exist_ok=True)

        # Configure API and create model inside the task
        configure_api()
        model = create_model("pro")  # You can make this configurable later
        processor = PDFProcessor(model, session_id=job_id)
        creator = PDFCreator()

        # Process each lesson file
        for lesson_path_str in lesson_paths:
            lesson_path = Path(lesson_path_str)
            
            # Analyze lesson against all jokbos
            analysis_result = processor.analyze_lesson_centric(jokbo_paths, lesson_path_str)
            
            if "error" in analysis_result:
                raise Exception(f"Analysis error for {lesson_path.name}: {analysis_result['error']}")

            # Generate output PDF
            output_filename = f"filtered_{lesson_path.stem}_all_jokbos.pdf"
            output_path = output_dir / output_filename
            
            creator.create_lesson_centric_pdf(
                str(lesson_path),
                analysis_result,
                str(output_path),
                str(job_dir / "jokbo")
            )
        
        # Clean up processor resources
        processor.cleanup()
        
        return {
            "status": "Complete",
            "output_path": str(output_dir),
            "files_generated": len(list(output_dir.glob("*.pdf")))
        }
    except Exception as e:
        # Celery will catch this exception and store it in the task result backend
        raise e