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
        # Do not bind a shared FileManager across different API keys.
        # We'll create a per-client FileManager in each operation call.
        self.file_manager = None
        
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
            # Create analyzer with specific API client and a file manager bound to it
            fm = FileManager(api_client)
            analyzer = LessonCentricAnalyzer(
                api_client, fm, self.session_id, self.debug_dir
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
            # Create analyzer with specific API client and a file manager bound to it
            fm = FileManager(api_client)
            analyzer = JokboCentricAnalyzer(
                api_client, fm, self.session_id, self.debug_dir
            )
            return analyzer.analyze(lesson_path, jokbo_path)

        # Run once against the selected API key (with failover)
        result = self.api_manager.execute_with_failover(operation)

        # Normalize lesson filenames in related slides to the original lesson filename.
        # This avoids temporary/AI-facing display names like "강의자료_<name>.pdf" breaking PDF assembly.
        try:
            from pathlib import Path as _P
            original_lesson_filename = _P(lesson_path).name
            if isinstance(result, dict):
                for page in (result.get("jokbo_pages") or []):
                    for q in (page.get("questions") or []):
                        for slide in (q.get("related_lesson_slides") or []):
                            if isinstance(slide, dict):
                                slide["lesson_filename"] = original_lesson_filename
        except Exception:
            # Best-effort normalization; continue even if structure differs
            pass

        return result
    
    def analyze_multiple_with_distribution(self, mode: str, file_pairs: List[tuple],
                                         parallel: bool = True, max_workers: Optional[int] = None) -> List[Dict[str, Any]]:
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
                fm = FileManager(api_client)
                analyzer = LessonCentricAnalyzer(
                    api_client, fm, self.session_id, self.debug_dir
                )
                return analyzer.analyze(jokbo_path, lesson_path)
        else:  # jokbo-centric
            def task_operation(file_pair, api_client, model):
                lesson_path, jokbo_path = file_pair
                fm = FileManager(api_client)
                analyzer = JokboCentricAnalyzer(
                    api_client, fm, self.session_id, self.debug_dir
                )
                return analyzer.analyze(lesson_path, jokbo_path)
        
        # Distribute tasks across APIs
        # Determine workers: default to using all API keys up to number of tasks
        if max_workers is None:
            try:
                max_workers = min(len(file_pairs), len(self.api_manager.api_keys)) or 1
            except Exception:
                max_workers = min(len(file_pairs), 3) or 1
        # Increment progress for each completed non-chunk task to keep totals consistent
        def _on_progress(_task):
            try:
                from storage_manager import StorageManager
                StorageManager().increment_chunk(self.session_id, 1)
            except Exception:
                pass

        results = self.api_manager.distribute_tasks(
            file_pairs,
            task_operation,
            parallel=parallel,
            max_workers=max_workers,
            on_progress=_on_progress,
        )
        
        return results
    
    def analyze_with_chunk_retry(self, mode: str, file_path: str, 
                               center_file_path: str, chunks: List[tuple]) -> Dict[str, Any]:
        """
        Analyze chunks in parallel across multiple API keys with automatic failover.
        
        Args:
            mode: 'lesson-centric' or 'jokbo-centric'
            file_path: Path to file being analyzed in chunks
            center_file_path: Path to center file
            chunks: List of (chunk_path, start_page, end_page) tuples
            
        Returns:
            Merged analysis results
        """
        # Prepare tasks as (index, (chunk_path, start_page, end_page)) tuples
        tasks = [(i, chunk_info) for i, chunk_info in enumerate(chunks)]
        
        # Determine a suitable level of parallelism
        try:
            max_workers = min(len(tasks), len(self.api_manager.api_keys))
            if max_workers <= 0:
                max_workers = 1
        except Exception:
            max_workers = min(len(tasks), 3)
        
        # Define how to process a single chunk with a specific API client
        def operation(task, api_client, model):
            idx, (chunk_path, start_page, end_page) = task
            if mode == "lesson-centric":
                fm = FileManager(api_client)
                analyzer = LessonCentricAnalyzer(
                    api_client, fm, self.session_id, self.debug_dir
                )
                result = analyzer.analyze(center_file_path, chunk_path)
            else:
                fm = FileManager(api_client)
                analyzer = JokboCentricAnalyzer(
                    api_client, fm, self.session_id, self.debug_dir
                )
                result = analyzer.analyze(
                    chunk_path, center_file_path, preloaded_jokbo_file=None,
                    chunk_info=(start_page, end_page)
                )
            return (idx, result)
        
        # Distribute chunk tasks across APIs in parallel with failover
        # Progress callback: increment chunk completion for this session/job
        def _on_progress(_task):
            try:
                from storage_manager import StorageManager
                StorageManager().increment_chunk(self.session_id, 1)
            except Exception:
                pass

        results_raw = self.api_manager.distribute_tasks(
            tasks, operation, parallel=True, max_workers=max_workers, on_progress=_on_progress
        )
        
        # Collect results back into original order
        ordered_results = [None] * len(tasks)
        for entry in results_raw:
            if isinstance(entry, tuple) and len(entry) == 2:
                idx, result = entry
                if 0 <= idx < len(ordered_results):
                    ordered_results[idx] = result
            elif isinstance(entry, dict) and "task" in entry:
                try:
                    idx = entry["task"][0]
                except Exception:
                    idx = None
                if idx is not None and 0 <= idx < len(ordered_results):
                    ordered_results[idx] = {"error": entry.get("error", "Unknown error")}

        # Normalize lesson filenames when chunking is orchestrated externally (multi-API path).
        # For jokbo-centric mode, ensure related_lesson_slides[].lesson_filename shows the original
        # lesson PDF name instead of temporary chunk filenames (e.g., tmp*.pdf).
        if mode == "jokbo-centric":
            try:
                original_lesson_filename = Path(file_path).name
                for res in ordered_results:
                    if not isinstance(res, dict):
                        continue
                    for page in (res.get("jokbo_pages") or []):
                        for q in (page.get("questions") or []):
                            for slide in (q.get("related_lesson_slides") or []):
                                if isinstance(slide, dict):
                                    slide["lesson_filename"] = original_lesson_filename
            except Exception:
                # Best-effort normalization; continue if structure differs
                pass

        # Replace any missing entries with error placeholders
        for i in range(len(ordered_results)):
            if ordered_results[i] is None:
                ordered_results[i] = {"error": "No result"}
        
        from ..parsers.result_merger import ResultMerger
        return ResultMerger.merge_chunk_results(ordered_results, mode)
    
    def get_api_status(self) -> Dict[str, Any]:
        """Get the status of all API keys."""
        return self.api_manager.get_status_report()
