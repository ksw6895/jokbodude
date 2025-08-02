"""
Lesson-centric analyzer implementation.
Analyzes lessons against jokbo (exam) files to find relevant content.
"""

from typing import Dict, Any, List, Tuple, Optional
from pathlib import Path
import pymupdf as fitz

from .base import BaseAnalyzer
from ..utils.logging import get_logger
from ..utils.exceptions import PDFProcessorError

logger = get_logger(__name__)


class LessonCentricAnalyzer(BaseAnalyzer):
    """Analyzer for lesson-centric mode."""
    
    def get_mode(self) -> str:
        """Get the analyzer mode name."""
        return "lesson-centric"
    
    def build_prompt(self, jokbo_filename: str) -> str:
        """
        Build the lesson-centric analysis prompt.
        
        Args:
            jokbo_filename: Name of the jokbo file being analyzed
            
        Returns:
            Complete prompt string
        """
        # Import from constants to avoid circular imports
        from constants import (
            COMMON_PROMPT_INTRO, COMMON_WARNINGS, RELEVANCE_CRITERIA,
            LESSON_CENTRIC_TASK, LESSON_CENTRIC_OUTPUT_FORMAT
        )
        
        prompt = f"""
{COMMON_PROMPT_INTRO}

족보 파일: {jokbo_filename}

{LESSON_CENTRIC_TASK}

{COMMON_WARNINGS}

{RELEVANCE_CRITERIA}

{LESSON_CENTRIC_OUTPUT_FORMAT}
"""
        return prompt.strip()
    
    def analyze(self, jokbo_path: str, lesson_path: str, 
                preloaded_lesson_file: Optional[Any] = None) -> Dict[str, Any]:
        """
        Analyze a jokbo against a lesson.
        
        Args:
            jokbo_path: Path to jokbo PDF
            lesson_path: Path to lesson PDF
            preloaded_lesson_file: Optional pre-uploaded lesson file
            
        Returns:
            Analysis results
        """
        jokbo_filename = Path(jokbo_path).name
        lesson_filename = Path(lesson_path).name
        
        logger.info(f"Analyzing jokbo '{jokbo_filename}' with lesson '{lesson_filename}'")
        
        # Build prompt
        prompt = self.build_prompt(jokbo_filename)
        
        if preloaded_lesson_file:
            # Use pre-uploaded lesson file
            response_text = self._analyze_with_preloaded_lesson(
                prompt, jokbo_path, preloaded_lesson_file, jokbo_filename
            )
        else:
            # Upload both files
            response_text = self._analyze_with_uploads(
                prompt, jokbo_path, lesson_path, jokbo_filename, lesson_filename
            )
        
        # Save debug response
        self.save_debug_response(response_text, jokbo_filename, lesson_filename)
        
        # Parse response
        result = self.parse_and_validate_response(response_text)
        
        # Validate and filter results
        result = self._validate_and_filter_results(result, jokbo_path)
        
        return result
    
    def _analyze_with_uploads(self, prompt: str, jokbo_path: str, lesson_path: str,
                             jokbo_filename: str, lesson_filename: str) -> str:
        """Analyze with uploading both files."""
        # Delete existing files first
        logger.info("Cleaning up existing uploaded files...")
        self.file_manager.delete_all_uploaded_files()
        
        # Upload and analyze
        files_to_upload = [
            (lesson_path, f"강의자료_{lesson_filename}"),
            (jokbo_path, f"족보_{jokbo_filename}")
        ]
        
        try:
            response_text = self.upload_and_analyze(files_to_upload, prompt)
            
            # Clean up except center file
            self.file_manager.cleanup_except_center_file(f"강의자료_{lesson_filename}")
            
            return response_text
            
        except Exception as e:
            logger.error(f"Analysis failed: {str(e)}")
            raise PDFProcessorError(f"Failed to analyze PDFs: {str(e)}")
    
    def _analyze_with_preloaded_lesson(self, prompt: str, jokbo_path: str,
                                      lesson_file: Any, jokbo_filename: str) -> str:
        """Analyze with pre-uploaded lesson file."""
        # Upload only jokbo
        jokbo_file = self.api_client.upload_file(jokbo_path, f"족보_{jokbo_filename}")
        self.file_manager.track_file(jokbo_file)
        
        try:
            # Prepare content
            content = [prompt, lesson_file, jokbo_file]
            
            # Generate response
            response = self.api_client.generate_content(content)
            return response.text
            
        finally:
            # Delete jokbo file
            self.file_manager.delete_file_safe(jokbo_file)
    
    def _validate_and_filter_results(self, result: Dict[str, Any], 
                                    jokbo_path: str) -> Dict[str, Any]:
        """Validate and filter analysis results."""
        # Get total pages in jokbo
        with fitz.open(str(jokbo_path)) as pdf:
            total_jokbo_pages = len(pdf)
        
        # Validate and filter results
        if "related_slides" in result:
            for slide in result["related_slides"]:
                if "related_jokbo_questions" in slide:
                    # Import validator to avoid circular imports
                    from validators import PDFValidator
                    
                    slide["related_jokbo_questions"] = PDFValidator.filter_valid_questions(
                        slide["related_jokbo_questions"],
                        total_jokbo_pages,
                        jokbo_path
                    )
        
        return result
    
    def analyze_multiple_jokbos(self, jokbo_paths: List[str], lesson_path: str) -> List[Dict[str, Any]]:
        """
        Analyze multiple jokbos against a single lesson.
        
        Args:
            jokbo_paths: List of jokbo file paths
            lesson_path: Path to lesson file
            
        Returns:
            List of analysis results
        """
        results = []
        lesson_filename = Path(lesson_path).name
        
        # Pre-upload lesson file for efficiency
        logger.info(f"Pre-uploading lesson file: {lesson_filename}")
        lesson_file = self.api_client.upload_file(
            lesson_path, f"강의자료_{lesson_filename}"
        )
        self.file_manager.track_file(lesson_file)
        
        try:
            for jokbo_path in jokbo_paths:
                logger.info(f"Analyzing jokbo: {Path(jokbo_path).name}")
                
                try:
                    result = self.analyze(jokbo_path, lesson_path, lesson_file)
                    results.append(result)
                except Exception as e:
                    logger.error(f"Failed to analyze {jokbo_path}: {str(e)}")
                    results.append({"error": str(e), "jokbo_path": jokbo_path})
                    
        finally:
            # Clean up lesson file
            self.file_manager.delete_file_safe(lesson_file)
        
        return results