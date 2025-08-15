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
from ..api.multi_api_manager import MultiAPIManager
from ..analyzers.lesson_centric import LessonCentricAnalyzer
from ..analyzers.jokbo_centric import JokboCentricAnalyzer
from ..analyzers.multi_api_analyzer import MultiAPIAnalyzer
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
        # Bind file manager to this API client (single-key context)
        self.file_manager = FileManager(self.api_client)
        
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
    
    # parallel mode removed
    
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
    
    # parallel mode removed
    
    # Multi-API support
    def analyze_lesson_centric_multi_api(self, jokbo_paths: List[str], lesson_path: str,
                                        api_keys: List[str], max_workers: int = 3) -> Dict[str, Any]:
        """
        Analyze multiple jokbos using multi-API support (lesson-centric mode).
        
        Args:
            jokbo_paths: List of jokbo file paths
            lesson_path: Path to lesson file
            api_keys: List of API keys to use
            max_workers: Maximum parallel workers
            
        Returns:
            Analysis results
        """
        logger.info(f"Starting multi-API lesson-centric analysis with {len(api_keys)} keys")
        
        # Create multi-API manager
        model_config = self._get_model_config()
        api_manager = MultiAPIManager(api_keys, model_config)
        
        # Create multi-API analyzer
        multi_analyzer = MultiAPIAnalyzer(api_manager, self.session_id, self.debug_dir)
        
        # If lesson file is large, split into chunks and distribute chunks across APIs per jokbo
        from ..pdf.operations import PDFOperations
        chunks = PDFOperations.split_pdf_for_chunks(lesson_path)
        if len(chunks) > 1:
            aggregated_results: List[Dict[str, Any]] = []
            # Extract chunk files once
            chunk_paths: List[tuple] = []
            for _, start_page, end_page in chunks:
                chunk_path = PDFOperations.extract_pages(lesson_path, start_page, end_page)
                chunk_paths.append((chunk_path, start_page, end_page))
            try:
                for jokbo_path in jokbo_paths:
                    # For lesson-centric chunking, the file being chunked is the lesson,
                    # and the center (pre-uploaded) file is the jokbo.
                    result = multi_analyzer.analyze_with_chunk_retry(
                        "lesson-centric", lesson_path, jokbo_path, chunk_paths
                    )
                    aggregated_results.append(result)
                results = aggregated_results
            finally:
                for chunk_path, _, _ in chunk_paths:
                    Path(chunk_path).unlink(missing_ok=True)
        else:
            # Distribute jokbos across APIs without chunking
            file_pairs = [(jokbo_path, lesson_path) for jokbo_path in jokbo_paths]
            # Use all API keys up to number of pairs
            workers = min(len(api_keys), len(file_pairs)) if api_keys else min(3, len(file_pairs))
            results = multi_analyzer.analyze_multiple_with_distribution(
                "lesson-centric", file_pairs, parallel=True, max_workers=workers
            )
        
        # Log API status
        status = api_manager.get_status_report()
        logger.info(f"API Status: {status['available_apis']}/{status['total_apis']} available")
        
        # Merge results
        return self._merge_lesson_centric_results(results)
    
    def analyze_jokbo_centric_multi_api(self, lesson_paths: List[str], jokbo_path: str,
                                       api_keys: List[str], max_workers: int = 3) -> Dict[str, Any]:
        """
        Analyze multiple lessons using multi-API support (jokbo-centric mode).
        
        Args:
            lesson_paths: List of lesson file paths
            jokbo_path: Path to jokbo file
            api_keys: List of API keys to use
            max_workers: Maximum parallel workers
            
        Returns:
            Analysis results
        """
        logger.info(f"Starting multi-API jokbo-centric analysis with {len(api_keys)} keys")
        
        # Create multi-API manager
        model_config = self._get_model_config()
        api_manager = MultiAPIManager(api_keys, model_config)
        
        # Create multi-API analyzer
        multi_analyzer = MultiAPIAnalyzer(api_manager, self.session_id, self.debug_dir)
        
        # Check if we need chunking for lessons
        from ..pdf.operations import PDFOperations
        chunked_lessons = []
        
        for lesson_path in lesson_paths:
            chunks = PDFOperations.split_pdf_for_chunks(lesson_path)
            if len(chunks) > 1:
                # This lesson needs chunking
                logger.info(f"Lesson {Path(lesson_path).name} will be processed in {len(chunks)} chunks")
                for chunk_info in chunks:
                    chunked_lessons.append((lesson_path, chunk_info))
            else:
                # Process as single file
                chunked_lessons.append((lesson_path, None))
        
        # Process with multi-API support
        if any(chunk_info for _, chunk_info in chunked_lessons):
            # Some lessons need chunking - handle specially
            results = self._process_chunked_lessons_multi_api(
                chunked_lessons, jokbo_path, multi_analyzer
            )
        else:
            # All lessons are single files
            file_pairs = [(lesson_path, jokbo_path) for lesson_path in lesson_paths]
            # Use all API keys up to number of pairs
            workers = min(len(api_keys), len(file_pairs)) if api_keys else min(3, len(file_pairs))
            results = multi_analyzer.analyze_multiple_with_distribution(
                "jokbo-centric", file_pairs, parallel=(workers > 1), max_workers=workers
            )
        
        # Log API status
        status = api_manager.get_status_report()
        logger.info(f"API Status: {status['available_apis']}/{status['total_apis']} available")
        
        # Merge results
        return self.jokbo_analyzer._merge_lesson_results(results, jokbo_path)
    
    def _process_chunked_lessons_multi_api(self, chunked_lessons: List[tuple],
                                          jokbo_path: str, multi_analyzer: MultiAPIAnalyzer) -> List[Dict[str, Any]]:
        """Process lessons that need chunking with multi-API support."""
        results = []
        
        for lesson_path, chunk_info in chunked_lessons:
            if chunk_info is None:
                # Single file
                result = multi_analyzer.analyze_jokbo_centric(lesson_path, jokbo_path)
                results.append(result)
            else:
                # Process chunks
                from ..pdf.operations import PDFOperations
                chunks = PDFOperations.split_pdf_for_chunks(lesson_path)
                
                # Extract chunk files
                chunk_paths = []
                for _, start_page, end_page in chunks:
                    chunk_path = PDFOperations.extract_pages(lesson_path, start_page, end_page)
                    chunk_paths.append((chunk_path, start_page, end_page))
                
                try:
                    # Analyze chunks with retry on different APIs
                    result = multi_analyzer.analyze_with_chunk_retry(
                        "jokbo-centric", lesson_path, jokbo_path, chunk_paths
                    )
                    results.append(result)
                finally:
                    # Clean up chunk files
                    for chunk_path, _, _ in chunk_paths:
                        Path(chunk_path).unlink(missing_ok=True)
        
        return results
    
    def _get_model_config(self) -> Dict[str, Any]:
        """Get the model configuration from the current model."""
        # Extract config from current model
        # This is a simplified version - in reality, you'd want to properly extract the config
        return {
            "model_name": getattr(self.model, "_model_name", "gemini-1.5-pro"),
            "generation_config": getattr(self.model, "_generation_config", None),
            "safety_settings": getattr(self.model, "_safety_settings", None)
        }
    
    # Utility methods
    def _merge_lesson_centric_results(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Merge lesson-centric results."""
        all_slides = []
        
        for result in results:
            if "error" not in result and "related_slides" in result:
                all_slides.extend(result["related_slides"])
        
        # Sort by page number (normalize to int)
        all_slides.sort(key=lambda x: int(str(x.get("lesson_page", 0)) or 0))
        
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
