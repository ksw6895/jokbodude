"""
Main PDF processor orchestrator.
Coordinates all components for PDF analysis tasks.
"""

import random
import string
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional, Union
import json
import shutil

from ..api.client import GeminiAPIClient
from ..api.file_manager import FileManager
from ..analyzers.lesson_centric import LessonCentricAnalyzer
from ..analyzers.jokbo_centric import JokboCentricAnalyzer
from ..parallel.executor import ParallelExecutor
from ..pdf.cache import get_global_cache, clear_global_cache
from ..utils.logging import get_logger
from ..utils.exceptions import PDFProcessorError

logger = get_logger(__name__)


class PDFProcessor:
    """Main orchestrator for PDF processing tasks."""
    
    def __init__(self, model, session_id: Optional[str] = None):
        """
        Initialize the PDF processor.
        
        Args:
            model: Gemini model instance
            session_id: Optional session ID for tracking
        """
        self.model = model
        self.api_client = GeminiAPIClient(model)
        self.file_manager = FileManager()
        
        # Session management
        if session_id:
            self.session_id = session_id
        else:
            self.session_id = self._generate_session_id()
        
        # Create session directories
        self.session_dir = Path("output/temp/sessions") / self.session_id
        self.chunk_results_dir = self.session_dir / "chunk_results"
        self.chunk_results_dir.mkdir(parents=True, exist_ok=True)
        
        # Debug directory
        self.debug_dir = Path("output/debug")
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize analyzers
        self.lesson_analyzer = LessonCentricAnalyzer(
            self.api_client, self.file_manager, self.session_id, self.debug_dir
        )
        self.jokbo_analyzer = JokboCentricAnalyzer(
            self.api_client, self.file_manager, self.session_id, self.debug_dir
        )
        
        # PDF cache
        self.pdf_cache = get_global_cache()
        
        logger.info(f"Initialized PDFProcessor with session ID: {self.session_id}")
    
    def _generate_session_id(self) -> str:
        """Generate a unique session ID."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
        return f"{timestamp}_{random_suffix}"
    
    # Lesson-centric methods
    def analyze_lesson_centric(self, jokbo_paths: List[str], lesson_path: str) -> Dict[str, Any]:
        """
        Analyze multiple jokbos against a single lesson (lesson-centric mode).
        
        Args:
            jokbo_paths: List of jokbo file paths
            lesson_path: Path to lesson file
            
        Returns:
            Analysis results
        """
        logger.info(f"Starting lesson-centric analysis: {len(jokbo_paths)} jokbos, 1 lesson")
        
        results = self.lesson_analyzer.analyze_multiple_jokbos(jokbo_paths, lesson_path)
        
        # Merge results
        merged = self._merge_lesson_centric_results(results)
        
        return merged
    
    def analyze_lesson_centric_parallel(self, jokbo_paths: List[str], lesson_path: str,
                                       max_workers: int = 3) -> Dict[str, Any]:
        """
        Analyze multiple jokbos in parallel (lesson-centric mode).
        
        Args:
            jokbo_paths: List of jokbo file paths
            lesson_path: Path to lesson file
            max_workers: Maximum parallel workers
            
        Returns:
            Analysis results
        """
        logger.info(f"Starting parallel lesson-centric analysis: {len(jokbo_paths)} jokbos, {max_workers} workers")
        
        # Pre-upload lesson file
        lesson_filename = Path(lesson_path).name
        lesson_file = self.api_client.upload_file(lesson_path, f"강의자료_{lesson_filename}")
        self.file_manager.track_file(lesson_file)
        
        try:
            # Create task function for parallel execution
            def analyze_task(jokbo_path):
                # Create analyzer with same session ID
                analyzer = LessonCentricAnalyzer(
                    self.api_client, self.file_manager, self.session_id, self.debug_dir
                )
                return analyzer.analyze(jokbo_path, lesson_path, lesson_file)
            
            # Execute in parallel
            executor = ParallelExecutor(max_workers)
            results = executor.execute_parallel(analyze_task, jokbo_paths, "Analyzing jokbos")
            
            # Merge results
            return self._merge_lesson_centric_results(results)
            
        finally:
            # Clean up lesson file
            self.file_manager.delete_file_safe(lesson_file)
    
    # Jokbo-centric methods
    def analyze_jokbo_centric(self, lesson_paths: List[str], jokbo_path: str) -> Dict[str, Any]:
        """
        Analyze multiple lessons against a single jokbo (jokbo-centric mode).
        
        Args:
            lesson_paths: List of lesson file paths
            jokbo_path: Path to jokbo file
            
        Returns:
            Analysis results
        """
        logger.info(f"Starting jokbo-centric analysis: {len(lesson_paths)} lessons, 1 jokbo")
        
        return self.jokbo_analyzer.analyze_multiple_lessons(lesson_paths, jokbo_path)
    
    def analyze_jokbo_centric_parallel(self, lesson_paths: List[str], jokbo_path: str,
                                      max_workers: int = 3) -> Dict[str, Any]:
        """
        Analyze multiple lessons in parallel (jokbo-centric mode).
        
        Args:
            lesson_paths: List of lesson file paths
            jokbo_path: Path to jokbo file
            max_workers: Maximum parallel workers
            
        Returns:
            Analysis results
        """
        logger.info(f"Starting parallel jokbo-centric analysis: {len(lesson_paths)} lessons, {max_workers} workers")
        
        # Pre-upload jokbo file
        jokbo_filename = Path(jokbo_path).name
        jokbo_file = self.api_client.upload_file(jokbo_path, f"족보_{jokbo_filename}")
        self.file_manager.track_file(jokbo_file)
        
        try:
            # Create task function for parallel execution
            def analyze_task(lesson_path):
                # Create analyzer with same session ID
                analyzer = JokboCentricAnalyzer(
                    self.api_client, self.file_manager, self.session_id, self.debug_dir
                )
                return analyzer.analyze(lesson_path, jokbo_path, jokbo_file)
            
            # Execute in parallel
            executor = ParallelExecutor(max_workers)
            results = executor.execute_parallel(analyze_task, lesson_paths, "Analyzing lessons")
            
            # Merge results
            return self.jokbo_analyzer._merge_lesson_results(results, jokbo_path)
            
        finally:
            # Clean up jokbo file
            self.file_manager.delete_file_safe(jokbo_file)
    
    # Multi-API support
    def analyze_with_multi_api(self, mode: str, api_keys: List[str], **kwargs) -> Dict[str, Any]:
        """
        Analyze using multiple API keys for better reliability.
        
        Args:
            mode: Analysis mode ('lesson-centric' or 'jokbo-centric')
            api_keys: List of API keys to use
            **kwargs: Additional arguments for analysis
            
        Returns:
            Analysis results
        """
        logger.info(f"Starting multi-API analysis with {len(api_keys)} keys")
        
        # TODO: Implement multi-API rotation logic
        # This would require creating multiple model instances with different API keys
        # and distributing work among them
        
        raise NotImplementedError("Multi-API support to be implemented")
    
    # Utility methods
    def _merge_lesson_centric_results(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Merge lesson-centric results."""
        all_slides = []
        
        for result in results:
            if "error" not in result and "related_slides" in result:
                all_slides.extend(result["related_slides"])
        
        # Sort by page number
        all_slides.sort(key=lambda x: x.get("lesson_page", 0))
        
        return {"related_slides": all_slides}
    
    def save_processing_state(self, state: Dict[str, Any]) -> None:
        """Save processing state to session directory."""
        state_file = self.session_dir / "processing_state.json"
        
        with open(state_file, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    
    def cleanup_session(self) -> None:
        """Clean up session directory and files."""
        logger.info(f"Cleaning up session {self.session_id}")
        
        # Clean up uploaded files
        self.file_manager.cleanup_tracked_files()
        
        # Clean up session directory
        if self.session_dir.exists():
            shutil.rmtree(self.session_dir)
            logger.info(f"Removed session directory: {self.session_dir}")
    
    def list_uploaded_files(self) -> List[Any]:
        """List all uploaded files."""
        return self.file_manager.list_uploaded_files()
    
    def delete_all_uploaded_files(self) -> int:
        """Delete all uploaded files."""
        return self.file_manager.delete_all_uploaded_files()
    
    def get_pdf_page_count(self, pdf_path: str) -> int:
        """Get cached page count for a PDF."""
        return self.pdf_cache.get_page_count(pdf_path)
    
    def __del__(self):
        """Clean up resources when object is destroyed."""
        # Clean up tracked files
        if hasattr(self, 'file_manager'):
            self.file_manager.cleanup_tracked_files()
        
        # Note: We don't clear the global PDF cache here as it might be used by other instances