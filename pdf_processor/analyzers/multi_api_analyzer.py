"""
Multi-API analyzer wrapper that provides failover support for analyzers.
"""

from typing import Dict, Any, List, Optional, Union
from pathlib import Path

from .base import BaseAnalyzer
from .lesson_centric import LessonCentricAnalyzer
from .jokbo_centric import JokboCentricAnalyzer
from ..api.multi_api_manager import MultiAPIManager
from ..api.file_manager import FileManager
from ..utils.logging import get_logger
from ..utils.exceptions import PDFProcessorError

logger = get_logger(__name__)


class MultiAPIAnalyzer:
    """Wrapper that provides multi-API support for analyzers."""
    
    def __init__(self, api_manager: MultiAPIManager, session_id: str, debug_dir: Path):
        """
        Initialize the multi-API analyzer.
        
        Args:
            api_manager: Multi-API manager instance
            session_id: Session identifier
            debug_dir: Directory for debug outputs
        """
        self.api_manager = api_manager
        self.session_id = session_id
        self.debug_dir = debug_dir
        self.file_manager = FileManager()
        
    def analyze_lesson_centric(self, jokbo_path: str, lesson_path: str) -> Dict[str, Any]:
        """
        Analyze using lesson-centric mode with multi-API support.
        
        Args:
            jokbo_path: Path to jokbo PDF
            lesson_path: Path to lesson PDF
            
        Returns:
            Analysis results
        """
        def operation(api_client, model):
            # Create analyzer with specific API client
            analyzer = LessonCentricAnalyzer(
                api_client, self.file_manager, self.session_id, self.debug_dir
            )
            return analyzer.analyze(jokbo_path, lesson_path)
        
        return self.api_manager.execute_with_failover(operation)
    
    def analyze_jokbo_centric(self, lesson_path: str, jokbo_path: str) -> Dict[str, Any]:
        """
        Analyze using jokbo-centric mode with multi-API support.
        
        Args:
            lesson_path: Path to lesson PDF
            jokbo_path: Path to jokbo PDF
            
        Returns:
            Analysis results
        """
        def operation(api_client, model):
            # Create analyzer with specific API client
            analyzer = JokboCentricAnalyzer(
                api_client, self.file_manager, self.session_id, self.debug_dir
            )
            return analyzer.analyze(lesson_path, jokbo_path)
        
        return self.api_manager.execute_with_failover(operation)
    
    def analyze_multiple_with_distribution(self, mode: str, file_pairs: List[tuple],
                                         parallel: bool = True) -> List[Dict[str, Any]]:
        """
        Analyze multiple file pairs with task distribution across APIs.
        
        Args:
            mode: 'lesson-centric' or 'jokbo-centric'
            file_pairs: List of (file1, file2) tuples
            parallel: Whether to process in parallel
            
        Returns:
            List of analysis results
        """
        if mode == "lesson-centric":
            def task_operation(file_pair, api_client, model):
                jokbo_path, lesson_path = file_pair
                analyzer = LessonCentricAnalyzer(
                    api_client, self.file_manager, self.session_id, self.debug_dir
                )
                return analyzer.analyze(jokbo_path, lesson_path)
        else:  # jokbo-centric
            def task_operation(file_pair, api_client, model):
                lesson_path, jokbo_path = file_pair
                analyzer = JokboCentricAnalyzer(
                    api_client, self.file_manager, self.session_id, self.debug_dir
                )
                return analyzer.analyze(lesson_path, jokbo_path)
        
        # Distribute tasks across APIs
        results = self.api_manager.distribute_tasks(
            file_pairs, task_operation, parallel=parallel
        )
        
        return results
    
    def analyze_with_chunk_retry(self, mode: str, file_path: str, 
                               center_file_path: str, chunks: List[tuple]) -> Dict[str, Any]:
        """
        Analyze chunks with retry on different APIs for failed chunks.
        
        Args:
            mode: 'lesson-centric' or 'jokbo-centric'
            file_path: Path to file being analyzed in chunks
            center_file_path: Path to center file (pre-uploaded)
            chunks: List of (chunk_path, start_page, end_page) tuples
            
        Returns:
            Merged analysis results
        """
        chunk_results = []
        failed_chunks = []
        
        # First pass: try all chunks
        for i, chunk_info in enumerate(chunks):
            chunk_path, start_page, end_page = chunk_info
            
            try:
                if mode == "lesson-centric":
                    result = self.analyze_lesson_centric(chunk_path, center_file_path)
                else:
                    result = self.analyze_jokbo_centric(chunk_path, center_file_path)
                
                chunk_results.append((i, result))
                logger.info(f"Chunk {i+1}/{len(chunks)} successful")
                
            except Exception as e:
                logger.error(f"Chunk {i+1}/{len(chunks)} failed: {str(e)}")
                failed_chunks.append((i, chunk_info))
        
        # Retry failed chunks with different APIs
        if failed_chunks:
            logger.info(f"Retrying {len(failed_chunks)} failed chunks...")
            
            for chunk_idx, chunk_info in failed_chunks:
                chunk_path, start_page, end_page = chunk_info
                
                # Try up to 3 times with different APIs
                for retry in range(3):
                    try:
                        if mode == "lesson-centric":
                            result = self.analyze_lesson_centric(chunk_path, center_file_path)
                        else:
                            result = self.analyze_jokbo_centric(chunk_path, center_file_path)
                        
                        chunk_results.append((chunk_idx, result))
                        logger.info(f"Chunk {chunk_idx+1} succeeded on retry {retry+1}")
                        break
                        
                    except Exception as e:
                        logger.error(f"Chunk {chunk_idx+1} retry {retry+1} failed: {str(e)}")
                        if retry == 2:  # Last retry
                            # Add empty result to maintain order
                            chunk_results.append((chunk_idx, {"error": str(e)}))
        
        # Sort results by chunk index
        chunk_results.sort(key=lambda x: x[0])
        
        # Extract just the results
        sorted_results = [result for _, result in chunk_results]
        
        # Merge results
        from ..parsers.result_merger import ResultMerger
        return ResultMerger.merge_chunk_results(sorted_results, mode)
    
    def get_api_status(self) -> Dict[str, Any]:
        """Get the status of all API keys."""
        return self.api_manager.get_status_report()