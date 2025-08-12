"""Main PDF processor class that orchestrates all components"""

from pathlib import Path
from typing import Dict, Any, List, Optional

from pdf_processor.utils.session_manager import SessionManager
from pdf_processor.utils.file_manager import FileManagerUtil
from pdf_processor.pdf.pdf_handler import PDFHandler
from pdf_processor.pdf.pdf_splitter import PDFSplitter
from pdf_processor.api.gemini_client import GeminiClient
from pdf_processor.api.response_handler import ResponseHandler
from pdf_processor.parsers.json_parser import JSONParser
from pdf_processor.parsers.result_merger import ResultMerger
from pdf_processor.analyzers.jokbo_analyzer import JokboAnalyzer
from pdf_processor.analyzers.lesson_analyzer import LessonAnalyzer
from pdf_processor.analyzers.parallel_analyzer import ParallelAnalyzer
from processing_config import ProcessingConfig


class PDFProcessor:
    """Main PDF processor that coordinates all analysis operations"""
    
    def __init__(self, model, session_id: Optional[str] = None):
        """Initialize PDF processor with all components
        
        Args:
            model: Configured Gemini model instance
            session_id: Optional session ID for sharing across instances
        """
        self.model = model
        
        # Initialize managers
        self.session_manager = SessionManager(session_id)
        self.file_manager = FileManagerUtil()
        
        # Initialize handlers
        self.pdf_handler = PDFHandler()
        self.pdf_splitter = PDFSplitter()
        self.response_handler = ResponseHandler()
        
        # Initialize API client
        self.gemini_client = GeminiClient(model)
        
        # Initialize parsers
        self.json_parser = JSONParser()
        self.result_merger = ResultMerger()
        
        # Initialize analyzers
        self.jokbo_analyzer = JokboAnalyzer(model, self.file_manager)
        self.lesson_analyzer = LessonAnalyzer(model, self.file_manager)
        self.parallel_analyzer = ParallelAnalyzer(model, self.session_manager.session_id)
        
        # Multi-API processor will be initialized lazily to avoid circular import
        self._multi_api_processor = None
        
        # Expose session_id for compatibility
        self.session_id = self.session_manager.session_id
        self.chunk_results_dir = self.session_manager.chunk_results_dir
        
        # Expose methods for backward compatibility
        self.uploaded_files = self.file_manager.uploaded_files
    
    def __del__(self):
        """Clean up resources when processor is destroyed"""
        # File manager will handle cleanup in its own destructor
        pass
    
    # File management methods (delegate to file_manager)
    def upload_pdf(self, pdf_path: str, display_name: Optional[str] = None):
        """Upload PDF file to Gemini API"""
        return self.file_manager.upload_pdf(pdf_path, display_name)
    
    def list_uploaded_files(self):
        """List all uploaded files"""
        return self.file_manager.list_uploaded_files()
    
    def delete_all_uploaded_files(self):
        """Delete all uploaded files"""
        return self.file_manager.delete_all_uploaded_files()
    
    def delete_file_safe(self, file):
        """Safely delete a file"""
        return self.file_manager.delete_file_safe(file)
    
    def cleanup_except_center_file(self, center_file_display_name: str):
        """Clean up all files except center file"""
        return self.file_manager.cleanup_except_center_file(center_file_display_name)
    
    # PDF handling methods (delegate to pdf_handler)
    def get_pdf_page_count(self, pdf_path: str) -> int:
        """Get PDF page count"""
        return self.pdf_handler.get_pdf_page_count(pdf_path)
    
    def validate_and_adjust_page_number(
        self, page_num: int, start_page: int, end_page: int,
        total_pages: int, chunk_path: str
    ) -> Optional[int]:
        """Validate and adjust page number"""
        return self.pdf_handler.validate_and_adjust_page_number(
            page_num, start_page, end_page, total_pages, chunk_path
        )
    
    def extract_pdf_pages(self, pdf_path: str, start_page: int, end_page: int) -> str:
        """Extract PDF pages"""
        return self.pdf_handler.extract_pdf_pages(pdf_path, start_page, end_page)
    
    def split_pdf_for_analysis(
        self, pdf_path: str, 
        max_pages: int = ProcessingConfig.DEFAULT_CHUNK_SIZE
    ) -> List[tuple]:
        """Split PDF for analysis"""
        return self.pdf_splitter.split_pdf_for_analysis(pdf_path, max_pages)
    
    # API methods (delegate to gemini_client)
    def generate_content_with_retry(
        self, content,
        max_retries: int = ProcessingConfig.MAX_RETRIES,
        backoff_factor: float = ProcessingConfig.BACKOFF_FACTOR
    ):
        """Generate content with retry logic"""
        return self.gemini_client.generate_content_with_retry(
            content, max_retries, backoff_factor
        )
    
    # Response handling methods
    def save_api_response(
        self, response_text: str, jokbo_filename: str,
        lesson_filename: Optional[str] = None, mode: str = "lesson-centric",
        finish_reason: Optional[str] = None, response_metadata: Optional[Dict] = None
    ):
        """Save API response"""
        return self.response_handler.save_api_response(
            response_text, jokbo_filename, lesson_filename,
            mode, finish_reason, response_metadata
        )
    
    def save_chunk_result(
        self, chunk_info: Dict[str, Any], result: Dict[str, Any], temp_dir: Path
    ) -> str:
        """Save chunk result"""
        return self.response_handler.save_chunk_result(chunk_info, result, temp_dir)
    
    def save_lesson_result(
        self, lesson_idx: int, lesson_path: str,
        result: Dict[str, Any], temp_dir: Path
    ) -> str:
        """Save lesson result"""
        return self.response_handler.save_lesson_result(
            lesson_idx, lesson_path, result, temp_dir
        )
    
    # Parsing methods
    def parse_response_json(self, response_text: str, mode: str = "jokbo-centric") -> Dict[str, Any]:
        """Parse response JSON"""
        return self.json_parser.parse_response_json(response_text, mode)
    
    def parse_partial_json(self, response_text: str, mode: str = "jokbo-centric") -> Dict[str, Any]:
        """Parse partial JSON"""
        return self.json_parser.parse_partial_json(response_text, mode)
    
    # Result merging methods
    def _merge_jokbo_centric_results(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Merge jokbo-centric results"""
        return self.result_merger.merge_jokbo_centric_results(results)
    
    def load_and_merge_chunk_results(self, temp_dir: Path) -> Dict[str, Any]:
        """Load and merge chunk results"""
        return self.result_merger.load_and_merge_chunk_results(temp_dir)
    
    def apply_final_filtering_and_sorting(self, all_connections: Dict[str, Any]) -> Dict[str, Any]:
        """Apply final filtering and sorting"""
        return self.result_merger.apply_final_filtering_and_sorting(all_connections)
    
    # Session management methods
    def save_processing_state(self, state_info: Dict[str, Any], state_file: Path):
        """Save processing state"""
        return self.session_manager.save_processing_state(state_info, state_file)
    
    def load_processing_state(self, state_file: Path) -> Optional[Dict[str, Any]]:
        """Load processing state"""
        return self.session_manager.load_processing_state(state_file)
    
    def cleanup_temp_files(self, temp_dir: Path):
        """Clean up temporary files"""
        return self.session_manager.cleanup_temp_files(temp_dir)
    
    # Analysis methods - Lesson-centric
    def analyze_single_jokbo_with_lesson(self, jokbo_path: str, lesson_path: str) -> Dict[str, Any]:
        """Analyze single jokbo with lesson"""
        return self.lesson_analyzer.analyze_single_jokbo_with_lesson(jokbo_path, lesson_path)
    
    def analyze_single_jokbo_with_lesson_preloaded(self, jokbo_path: str, lesson_file) -> Dict[str, Any]:
        """Analyze single jokbo with pre-loaded lesson"""
        return self.lesson_analyzer.analyze_single_jokbo_with_lesson_preloaded(jokbo_path, lesson_file)
    
    def analyze_pdfs_for_lesson(self, jokbo_paths: List[str], lesson_path: str) -> Dict[str, Any]:
        """Analyze PDFs for lesson"""
        return self.lesson_analyzer.analyze_pdfs_for_lesson(jokbo_paths, lesson_path)
    
    # Analysis methods - Jokbo-centric
    def analyze_single_lesson_with_jokbo(self, lesson_path: str, jokbo_path: str) -> Dict[str, Any]:
        """Analyze single lesson with jokbo"""
        # This method handles chunking internally in jokbo_analyzer
        return self.jokbo_analyzer.analyze_lessons_for_jokbo([lesson_path], jokbo_path)
    
    def analyze_single_lesson_with_jokbo_preloaded(self, lesson_path: str, jokbo_file) -> Dict[str, Any]:
        """Analyze single lesson with pre-loaded jokbo"""
        return self.jokbo_analyzer.analyze_single_lesson_with_jokbo_preloaded(lesson_path, jokbo_file)
    
    def analyze_lessons_for_jokbo(self, lesson_paths: List[str], jokbo_path: str) -> Dict[str, Any]:
        """Analyze lessons for jokbo"""
        return self.jokbo_analyzer.analyze_lessons_for_jokbo(lesson_paths, jokbo_path)
    
    # Parallel analysis methods
    def analyze_lessons_for_jokbo_parallel(
        self, lesson_paths: List[str], jokbo_path: str,
        max_workers: int = ProcessingConfig.DEFAULT_THREAD_WORKERS
    ) -> Dict[str, Any]:
        """Analyze lessons in parallel for jokbo"""
        return self.parallel_analyzer.analyze_lessons_for_jokbo_parallel(
            lesson_paths, jokbo_path, max_workers
        )
    
    def analyze_pdfs_for_lesson_parallel(
        self, jokbo_paths: List[str], lesson_path: str,
        max_workers: int = ProcessingConfig.DEFAULT_THREAD_WORKERS
    ) -> Dict[str, Any]:
        """Analyze PDFs in parallel for lesson"""
        return self.parallel_analyzer.analyze_pdfs_for_lesson_parallel(
            jokbo_paths, lesson_path, max_workers
        )
    
    # Multi-API analysis method
    def analyze_lessons_for_jokbo_multi_api(
        self, lesson_paths: List[str], jokbo_path: str,
        api_keys: List[str], model_type: str = "pro",
        thinking_budget: Optional[int] = None
    ) -> Dict[str, Any]:
        """Analyze lessons using multiple API keys"""
        # Lazy import to avoid circular dependency
        if self._multi_api_processor is None:
            from pdf_processor.core.multi_api_processor import MultiAPIProcessor
            self._multi_api_processor = MultiAPIProcessor()
        
        return self._multi_api_processor.analyze_lessons_for_jokbo_multi_api(
            lesson_paths, jokbo_path, api_keys, model_type, thinking_budget
        )
    
    # Backward compatibility methods for chunk processing
    def _analyze_single_lesson_with_jokbo_original(
        self, lesson_path: str, jokbo_path: str
    ) -> Dict[str, Any]:
        """Original method for backward compatibility"""
        # Delegate to jokbo analyzer's internal method
        file_manager = FileManagerUtil()
        jokbo_file = file_manager.upload_pdf(jokbo_path, f"족보_{Path(jokbo_path).name}")
        result = self.jokbo_analyzer._analyze_single_chunk(lesson_path, jokbo_file, Path(lesson_path).name)
        file_manager.delete_file_safe(jokbo_file)
        return result
    
    def _analyze_jokbo_with_lesson_chunk(
        self, jokbo_file, lesson_chunk_path: str,
        start_page: int, end_page: int, lesson_filename: str,
        chunk_idx: int, total_chunks: int
    ) -> Dict[str, Any]:
        """Analyze jokbo with lesson chunk"""
        return self.jokbo_analyzer._analyze_jokbo_with_lesson_chunk_preloaded(
            jokbo_file, lesson_chunk_path, start_page, end_page,
            lesson_filename, chunk_idx, total_chunks
        )
    
    def _analyze_jokbo_with_lesson_chunk_preloaded(
        self, jokbo_file, lesson_chunk_path: str,
        start_page: int, end_page: int, lesson_filename: str,
        chunk_idx: int, total_chunks: int
    ) -> Dict[str, Any]:
        """Analyze jokbo with lesson chunk (pre-loaded)"""
        return self.jokbo_analyzer._analyze_jokbo_with_lesson_chunk_preloaded(
            jokbo_file, lesson_chunk_path, start_page, end_page,
            lesson_filename, chunk_idx, total_chunks
        )