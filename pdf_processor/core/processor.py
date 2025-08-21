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

    def set_relevance_threshold(self, score: int) -> None:
        """Set minimum relevance score across analyzers (0..110)."""
        try:
            v = int(score)
        except Exception:
            v = 80
        v = max(0, min(v, 110))
        try:
            self.lesson_analyzer.set_relevance_threshold(v)
        except Exception:
            pass
        try:
            self.jokbo_analyzer.set_relevance_threshold(v)
        except Exception:
            pass
    
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
                                        api_keys: List[str], max_workers: Optional[int] = None) -> Dict[str, Any]:
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
        # Propagate current lesson-centric threshold to multi-API analyzers
        try:
            thr = getattr(self.lesson_analyzer, 'min_relevance_score', None)
            multi_analyzer.set_relevance_threshold(thr)
        except Exception:
            pass
        
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
            # Use all API keys up to number of pairs (or explicit max_workers)
            if isinstance(max_workers, int) and max_workers > 0:
                workers = min(max_workers, len(api_keys), len(file_pairs))
            else:
                workers = min(len(api_keys), len(file_pairs)) if api_keys else min(1, len(file_pairs))
            results = multi_analyzer.analyze_multiple_with_distribution(
                "lesson-centric", file_pairs, parallel=True, max_workers=workers
            )
        
        # Log API status
        status = api_manager.get_status_report()
        logger.info(f"API Status: {status['available_apis']}/{status['total_apis']} available")
        
        # Merge results
        return self._merge_lesson_centric_results(results)
    
    def analyze_jokbo_centric_multi_api(self, lesson_paths: List[str], jokbo_path: str,
                                       api_keys: List[str], max_workers: Optional[int] = None) -> Dict[str, Any]:
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
        
        # Create multi-API analyzer (kept for threshold propagation if needed)
        multi_analyzer = MultiAPIAnalyzer(api_manager, self.session_id, self.debug_dir)

        # Build a global list of chunk tasks across all lessons to maximize key utilization
        from ..pdf.operations import PDFOperations
        global_tasks: List[tuple] = []  # (lesson_idx, lesson_path, chunk_path, start_page, end_page)
        created_chunks: List[Path] = []
        try:
            for lidx, lesson_path in enumerate(lesson_paths):
                chunks = PDFOperations.split_pdf_for_chunks(lesson_path)
                if len(chunks) <= 1:
                    # Extract full as a single chunk for uniform handling
                    _, s, e = chunks[0]
                    cpath = PDFOperations.extract_pages(lesson_path, s, e)
                    created_chunks.append(Path(cpath))
                    global_tasks.append((lidx, lesson_path, cpath, s, e))
                else:
                    logger.info(f"Lesson {Path(lesson_path).name} will be processed in {len(chunks)} chunks")
                    for _, s, e in chunks:
                        cpath = PDFOperations.extract_pages(lesson_path, s, e)
                        created_chunks.append(Path(cpath))
                        global_tasks.append((lidx, lesson_path, cpath, s, e))

            # Determine worker count
            try:
                capacity = len(api_manager.api_keys) * max(1, int(getattr(api_manager, 'per_key_limit', 1)))
                workers = min(len(global_tasks), capacity)
            except Exception:
                workers = min(len(global_tasks), len(api_manager.api_keys) or 1)
            if isinstance(max_workers, int) and max_workers > 0:
                workers = max(1, min(workers, max_workers))

            # Define chunk operation for distribution
            def _op(task, api_client, model):
                lidx, lpath, cpath, start, end = task
                fm = FileManager(api_client)
                analyzer = JokboCentricAnalyzer(api_client, fm, self.session_id, self.debug_dir)
                # Propagate jokbo-centric threshold if configured
                try:
                    thr = getattr(self.jokbo_analyzer, 'min_relevance_score', None)
                    if thr is not None:
                        analyzer.set_relevance_threshold(thr)
                except Exception:
                    pass
                res = analyzer.analyze(
                    cpath, jokbo_path, preloaded_jokbo_file=None,
                    chunk_info=(start, end), original_lesson_path=lpath
                )
                # Normalize slide filenames to original lesson
                try:
                    orig = Path(lpath).name
                    if isinstance(res, dict):
                        for page in (res.get("jokbo_pages") or []):
                            for q in (page.get("questions") or []):
                                for slide in (q.get("related_lesson_slides") or []):
                                    if isinstance(slide, dict):
                                        slide["lesson_filename"] = orig
                except Exception:
                    pass
                return (lidx, res)

            # Progress callback per completed chunk
            def _on_progress(_task):
                try:
                    from storage_manager import StorageManager
                    StorageManager().increment_chunk(self.session_id, 1)
                except Exception:
                    pass

            # Distribute all chunk tasks globally
            raw_results = api_manager.distribute_tasks(
                global_tasks, _op, parallel=True, max_workers=workers, on_progress=_on_progress
            )

            # Group results by lesson and merge per-lesson
            per_lesson: Dict[int, List[Dict[str, Any]]] = {}
            for entry in raw_results:
                if isinstance(entry, tuple) and len(entry) == 2:
                    lidx, res = entry
                    if isinstance(res, dict):
                        per_lesson.setdefault(lidx, []).append(res)
                # Errors are logged inside distribute_tasks; skip here

            from ..parsers.result_merger import ResultMerger as _RM
            results: List[Dict[str, Any]] = []
            for lidx in range(len(lesson_paths)):
                cresults = per_lesson.get(lidx, [])
                if not cresults:
                    results.append({"jokbo_pages": []})
                else:
                    results.append(_RM.merge_chunk_results(cresults, "jokbo-centric"))
        finally:
            # Always clean up temp chunk files
            for p in created_chunks:
                try:
                    p.unlink(missing_ok=True)
                except Exception:
                    pass
        
        # Log API status
        status = api_manager.get_status_report()
        logger.info(f"API Status: {status['available_apis']}/{status['total_apis']} available")
        
        # Merge results
        return self.jokbo_analyzer._merge_lesson_results(results, jokbo_path)
    
    def _process_chunked_lessons_multi_api(self, chunked_lessons: List[tuple],
                                          jokbo_path: str, multi_analyzer: MultiAPIAnalyzer) -> List[Dict[str, Any]]:
        """Process lessons that need chunking with multi-API support.
        Avoids duplicate processing by ensuring each lesson is processed once.
        Increments progress for single-file lessons to keep totals accurate.
        """
        results = []
        processed_chunked_lessons = set()
        
        from ..pdf.operations import PDFOperations
        
        for lesson_path, chunk_info in chunked_lessons:
            if chunk_info is None:
                # Single file – analyze once and count as one unit of progress
                result = multi_analyzer.analyze_jokbo_centric(lesson_path, jokbo_path)
                try:
                    from storage_manager import StorageManager
                    StorageManager().increment_chunk(self.session_id, 1,
                        f"파일 완료: {Path(lesson_path).name}")
                except Exception:
                    pass
                results.append(result)
                continue

            # For chunked lessons, ensure we process each lesson only once
            if lesson_path in processed_chunked_lessons:
                continue
            processed_chunked_lessons.add(lesson_path)

            # Build all chunks once for this lesson
            chunks = PDFOperations.split_pdf_for_chunks(lesson_path)
            chunk_paths = []
            for _, start_page, end_page in chunks:
                chunk_path = PDFOperations.extract_pages(lesson_path, start_page, end_page)
                chunk_paths.append((chunk_path, start_page, end_page))

            try:
                # Analyze chunks with retry on different APIs (progress increments per chunk inside)
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
            "model_name": getattr(self.model, "_model_name", "gemini-2.5-flash"),
            "generation_config": getattr(self.model, "_generation_config", None),
            "safety_settings": getattr(self.model, "_safety_settings", None)
        }
    
    # Utility methods
    def _merge_lesson_centric_results(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Merge lesson-centric results including all eligible questions per slide.

        Behavior:
        - Keep every lesson slide in the final PDF (handled by PDFCreator),
          but only attach the single highest-relevance jokbo question per slide
          in the merged analysis result so insertion is one-per-slide.
        - Across multiple jokbo analyses, we aggregate candidates for each
          lesson page and select the max by `relevance_score` (ties resolved
          by stable order as encountered).
        - Preserve slide-level optional fields (`importance_score`,
          `key_concepts`) from the slide that contributed the chosen question.
        """
        from typing import Tuple

        # page -> list of (question_dict, importance_score, key_concepts)
        candidates_by_page: Dict[int, List[Tuple[Dict[str, Any], Optional[int], List[Any]]]] = {}

        for result in results or []:
            if not isinstance(result, dict) or "error" in result:
                continue
            slides = result.get("related_slides") or []
            if not isinstance(slides, list):
                continue
            for slide in slides:
                if not isinstance(slide, dict):
                    continue
                try:
                    page_num = int(str(slide.get("lesson_page", 0)) or 0)
                except Exception:
                    page_num = 0
                if page_num <= 0:
                    continue
                importance = slide.get("importance_score")
                key_concepts = slide.get("key_concepts") if isinstance(slide.get("key_concepts"), list) else []
                for q in (slide.get("related_jokbo_questions") or []):
                    if not isinstance(q, dict):
                        continue
                    # Normalize score to int; ResponseParser already clamps but be defensive
                    try:
                        score = int(str(q.get("relevance_score", 0)) or 0)
                    except Exception:
                        score = 0
                    # Store question as-is; PDFCreator expects these fields
                    q_copy = dict(q)
                    q_copy["relevance_score"] = score
                    candidates_by_page.setdefault(page_num, []).append((q_copy, importance, key_concepts))

        final_slides: List[Dict[str, Any]] = []
        total_questions = 0
        # Determine threshold from analyzer (defaults to 80 after change)
        try:
            min_thr = int(getattr(self.lesson_analyzer, 'min_relevance_score', 80))
        except Exception:
            min_thr = 80
        # Iterate pages in ascending order for deterministic output
        for page_num in sorted(candidates_by_page.keys()):
            cand_list = candidates_by_page.get(page_num) or []
            if not cand_list:
                continue
            # Include all questions meeting threshold; sort by score desc
            filtered = [q for (q, _imp, _kc) in cand_list if int(q.get("relevance_score", 0)) >= min_thr]
            if not filtered:
                continue
            # Deduplicate on (jokbo_filename, jokbo_page, question_number)
            seen_keys = set()
            uniq: List[Dict[str, Any]] = []
            for q in filtered:
                key = (str(q.get("jokbo_filename") or '').lower(), int(q.get("jokbo_page", 0)), str(q.get("question_number") or ''))
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                uniq.append(q)
            uniq.sort(key=lambda x: int(str(x.get("relevance_score", 0)) or 0), reverse=True)
            # Choose a representative slide-level meta from the best-scoring candidate
            rep_idx = 0
            rep_importance = None
            rep_kc: List[Any] = []
            if cand_list:
                try:
                    rep_idx = max(range(len(cand_list)), key=lambda i: int(cand_list[i][0].get("relevance_score", 0)))
                except Exception:
                    rep_idx = 0
                rep_importance = cand_list[rep_idx][1]
                rep_kc = cand_list[rep_idx][2] or []
            slide_entry: Dict[str, Any] = {
                "lesson_page": page_num,
                "related_jokbo_questions": uniq,
            }
            if rep_importance is not None:
                slide_entry["importance_score"] = rep_importance
            if rep_kc:
                slide_entry["key_concepts"] = rep_kc
            final_slides.append(slide_entry)
            total_questions += len(uniq)

        merged = {"related_slides": final_slides}
        # Attach lightweight summary for downstream display (best-effort)
        try:
            merged["summary"] = {
                "total_related_slides": len(final_slides),
                "total_questions": total_questions,
                "key_topics": [],
            }
        except Exception:
            pass
        return merged
    
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
