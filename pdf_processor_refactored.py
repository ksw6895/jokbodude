"""
Refactored PDF processor entry point.
This is a temporary file to demonstrate the new modular architecture.
"""

from typing import Dict, Any, List
from pathlib import Path

# Import the new modular components
from pdf_processor.core.processor import PDFProcessor
from pdf_processor.utils.logging import get_logger, setup_file_logging
from pdf_processor.utils.config import ProcessingConfig

# Keep compatibility with existing imports
from config import configure_api
from pdf_creator import PDFCreator
from api_key_manager import MultiAPIManager

logger = get_logger(__name__)


class RefactoredPDFProcessor:
    """
    Wrapper class to maintain compatibility with existing code
    while using the new modular architecture.
    """
    
    def __init__(self, model, session_id=None):
        """Initialize with new modular processor."""
        self.processor = PDFProcessor(model, session_id)
        
        # Expose commonly used attributes for compatibility
        self.session_id = self.processor.session_id
        self.debug_dir = self.processor.debug_dir
        self.file_manager = self.processor.file_manager
        self.api_client = self.processor.api_client
        
    # Delegate methods to new processor
    def analyze_pdfs_for_lesson(self, jokbo_paths: List[str], lesson_path: str) -> Dict[str, Any]:
        """Analyze PDFs for lesson (lesson-centric mode)."""
        return self.processor.analyze_lesson_centric(jokbo_paths, lesson_path)
    
    def analyze_pdfs_for_lesson_parallel(self, jokbo_paths: List[str], lesson_path: str, 
                                        max_workers: int = 3) -> Dict[str, Any]:
        """Analyze PDFs for lesson in parallel."""
        return self.processor.analyze_lesson_centric_parallel(jokbo_paths, lesson_path, max_workers)
    
    def analyze_lessons_for_jokbo(self, lesson_paths: List[str], jokbo_path: str) -> Dict[str, Any]:
        """Analyze lessons for jokbo (jokbo-centric mode)."""
        return self.processor.analyze_jokbo_centric(lesson_paths, jokbo_path)
    
    def analyze_lessons_for_jokbo_parallel(self, lesson_paths: List[str], jokbo_path: str,
                                          max_workers: int = 3) -> Dict[str, Any]:
        """Analyze lessons for jokbo in parallel."""
        return self.processor.analyze_jokbo_centric_parallel(lesson_paths, jokbo_path, max_workers)
    
    # Utility methods for compatibility
    def list_uploaded_files(self):
        """List uploaded files."""
        return self.processor.list_uploaded_files()
    
    def delete_all_uploaded_files(self):
        """Delete all uploaded files."""
        return self.processor.delete_all_uploaded_files()
    
    def get_pdf_page_count(self, pdf_path: str) -> int:
        """Get PDF page count."""
        return self.processor.get_pdf_page_count(pdf_path)
    
    def cleanup_session(self):
        """Clean up session."""
        return self.processor.cleanup_session()
    
    # Multi-API support (placeholder for now)
    def analyze_lessons_for_jokbo_multi_api(self, lesson_paths: List[str], jokbo_path: str,
                                           api_keys: List[str], model_type: str = "pro",
                                           thinking_budget=None) -> Dict[str, Any]:
        """Multi-API analysis - to be implemented."""
        # TODO: Implement multi-API support in the new architecture
        logger.warning("Multi-API support not yet implemented in refactored version")
        return self.analyze_lessons_for_jokbo_parallel(lesson_paths, jokbo_path)


def demonstrate_usage():
    """Demonstrate usage of the refactored processor."""
    # Configure API
    configure_api()
    
    # Set up file logging
    setup_file_logging()
    
    # Create model (placeholder - would come from config)
    import google.generativeai as genai
    from config import get_model_config
    
    model_config = get_model_config("pro")
    model = genai.GenerativeModel(**model_config)
    
    # Create processor
    processor = RefactoredPDFProcessor(model)
    
    print(f"Created refactored processor with session: {processor.session_id}")
    
    # Example usage
    jokbo_dir = Path("jokbo")
    lesson_dir = Path("lesson")
    
    if jokbo_dir.exists() and lesson_dir.exists():
        jokbo_files = list(jokbo_dir.glob("*.pdf"))
        lesson_files = list(lesson_dir.glob("*.pdf"))
        
        if jokbo_files and lesson_files:
            # Lesson-centric example
            print("\nLesson-centric analysis example:")
            result = processor.analyze_pdfs_for_lesson(
                [str(f) for f in jokbo_files[:2]], 
                str(lesson_files[0])
            )
            print(f"Found {len(result.get('related_slides', []))} related slides")
            
            # Jokbo-centric example
            print("\nJokbo-centric analysis example:")
            result = processor.analyze_lessons_for_jokbo(
                [str(f) for f in lesson_files[:2]], 
                str(jokbo_files[0])
            )
            print(f"Found {len(result.get('jokbo_pages', []))} jokbo pages")
    
    # Clean up
    processor.cleanup_session()
    print("\nSession cleaned up")


if __name__ == "__main__":
    demonstrate_usage()