"""
Multi-API analyzer wrapper that provides failover support for analyzers.
"""

from typing import Dict, Any, List, Optional, Union
from pathlib import Path
import json

from .base import BaseAnalyzer
from .lesson_centric import LessonCentricAnalyzer
from .jokbo_centric import JokboCentricAnalyzer
from .partial_jokbo import PartialJokboAnalyzer
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
        # Optional: relevance threshold to propagate to created analyzers
        self.min_relevance_score: Optional[int] = None

    def set_relevance_threshold(self, score: Optional[int]) -> None:
        """Set a relevance threshold that will be applied to analyzers created by this wrapper."""
        try:
            if score is None:
                self.min_relevance_score = None
            else:
                self.min_relevance_score = max(0, min(int(score), 110))
        except Exception:
            self.min_relevance_score = None
        
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
            # Propagate threshold if configured
            try:
                if self.min_relevance_score is not None:
                    analyzer.set_relevance_threshold(self.min_relevance_score)
            except Exception:
                pass
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
                try:
                    if self.min_relevance_score is not None:
                        analyzer.set_relevance_threshold(self.min_relevance_score)
                except Exception:
                    pass
                result = analyzer.analyze(jokbo_path, lesson_path)
                # Nothing to normalize for lesson-centric here
                return result
        else:  # jokbo-centric
            def task_operation(file_pair, api_client, model):
                # file_pair ordering: (lesson_path, jokbo_path)
                lesson_path, jokbo_path = file_pair
                fm = FileManager(api_client)
                analyzer = JokboCentricAnalyzer(
                    api_client, fm, self.session_id, self.debug_dir
                )
                result = analyzer.analyze(lesson_path, jokbo_path)
                # Normalize related slide filenames to the actual lesson filename
                # to avoid tmp/prefixed names that break downstream PDF assembly.
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
                    # Best-effort normalization; do not fail task on structure variance
                    pass
                return result
        
        # Distribute tasks across APIs
        # Determine workers: default to using all API keys up to number of tasks
        if max_workers is None:
            try:
                capacity = len(self.api_manager.api_keys) * max(1, int(getattr(self.api_manager, 'per_key_limit', 1)))
                max_workers = min(len(file_pairs), max(1, capacity))
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

    def analyze_partial_jokbo(self, jokbo_path: str, lesson_paths: List[str]) -> Dict[str, Any]:
        """Run partial-jokbo analysis with failover across API keys (one key per attempt)."""
        def operation(api_client, model):
            fm = FileManager(api_client)
            analyzer = PartialJokboAnalyzer(api_client, fm, self.session_id, self.debug_dir)
            return analyzer.analyze(jokbo_path, lesson_paths or [])
        try:
            res = self.api_manager.execute_with_failover(operation, max_retries=max(1, len(self.api_manager.api_keys)))
            return res if isinstance(res, dict) else {}
        except Exception as e:
            logger.error(f"Partial-jokbo failed: {e}")
            return {"error": str(e)}

    def analyze_partial_multiple(self, jokbo_paths: List[str], lesson_paths: List[str],
                                 parallel: bool = True, max_workers: Optional[int] = None) -> List[Dict[str, Any]]:
        """Distribute partial-jokbo across jokbo files using multiple API keys."""
        tasks = list(jokbo_paths or [])
        # Determine how many chunk units each jokbo completion should represent
        try:
            from ..pdf.operations import PDFOperations as _PDFOps
            lesson_chunks = 0
            for lp in (lesson_paths or []):
                try:
                    lesson_chunks += len(_PDFOps.split_pdf_for_chunks(lp))
                except Exception:
                    lesson_chunks += 1
            if lesson_chunks <= 0:
                lesson_chunks = 1
        except Exception:
            lesson_chunks = 1
        def task_operation(jp, api_client, model):
            fm = FileManager(api_client)
            analyzer = PartialJokboAnalyzer(api_client, fm, self.session_id, self.debug_dir)
            return analyzer.analyze(jp, lesson_paths or [])
        # determine workers similar to other paths
        if max_workers is None:
            try:
                capacity = len(self.api_manager.api_keys) * max(1, int(getattr(self.api_manager, 'per_key_limit', 1)))
                max_workers = min(len(tasks), max(1, capacity))
            except Exception:
                max_workers = min(len(tasks), 3) or 1
        # Progress callback per completed jokbo
        def _on_progress(jp):
            try:
                from storage_manager import StorageManager
                from pathlib import Path as _P
                name = None
                try:
                    name = _P(str(jp)).name
                except Exception:
                    name = None
                StorageManager().increment_chunk(self.session_id, int(lesson_chunks),
                    f"파일 완료: {name}" if name else None)
            except Exception:
                pass
        return self.api_manager.distribute_tasks(
            tasks, task_operation, parallel=parallel, max_workers=max_workers, on_progress=_on_progress
        )
    
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

        # Best-effort resume: if this session already has saved results for some
        # chunk indices, preload them and do not resubmit those tasks.
        preload_results: dict[int, dict] = {}
        try:
            from pathlib import Path as _P
            base = f"{mode}-{_P(file_path).stem}"
            cdir = _P("output/temp/sessions") / self.session_id / "chunks" / base
            if cdir.exists():
                for i in range(len(tasks)):
                    p = cdir / f"chunk_{i:03d}.json"
                    if not p.exists():
                        continue
                    try:
                        import json as _json
                        with open(p, "r", encoding="utf-8") as f:
                            payload = _json.load(f)
                        res = payload.get("result") if isinstance(payload, dict) else None
                        if isinstance(res, dict):
                            preload_results[i] = res
                    except Exception:
                        continue
            # Filter out preloaded indices from the task list
            if preload_results:
                tasks = [(i, t) for (i, t) in tasks if i not in preload_results]
        except Exception:
            # Resume is best-effort; continue normally on any error
            pass
        
        # Determine a suitable level of parallelism
        try:
            capacity = len(self.api_manager.api_keys) * max(1, int(getattr(self.api_manager, 'per_key_limit', 1)))
            max_workers = min(len(tasks), max(1, capacity))
        except Exception:
            max_workers = min(len(tasks), 3)
        
        # Define how to process a single chunk with a specific API client
        def operation(task, api_client, model):
            # Cancel-aware: if the session/job was cancelled, bail out early
            try:
                from storage_manager import StorageManager as _SM
                if _SM().is_cancelled(self.session_id):
                    raise PDFProcessorError("cancelled")
            except PDFProcessorError:
                raise
            except Exception:
                pass
            idx, (chunk_path, start_page, end_page) = task
            # Optional purge per chunk (disabled by default)
            try:
                import os
                _purge = str(os.getenv("GEMINI_PURGE_BEFORE_UPLOAD", "false")).strip().lower() in ("1","true","t","y","yes")
            except Exception:
                _purge = False
            if _purge:
                try:
                    from ..api.upload_cleanup import purge_key_files
                    purge_key_files(
                        api_client,
                        delete_prefixes=[],
                        keep_display_names=set(),
                        delete_all=True,
                        log_context=f"{mode}_chunk_preupload#{idx}"
                    )
                except Exception:
                    logger.info("Chunk preupload purge skipped due to error; continuing")
            if mode == "lesson-centric":
                fm = FileManager(api_client)
                analyzer = LessonCentricAnalyzer(
                    api_client, fm, self.session_id, self.debug_dir
                )
                try:
                    if self.min_relevance_score is not None:
                        analyzer.set_relevance_threshold(self.min_relevance_score)
                except Exception:
                    pass
                # Pass chunk_info so the analyzer can offset lesson_page correctly
                result = analyzer.analyze(center_file_path, chunk_path, None, chunk_info=(start_page, end_page))
            else:
                fm = FileManager(api_client)
                analyzer = JokboCentricAnalyzer(
                    api_client, fm, self.session_id, self.debug_dir
                )
                # Pass the original lesson file path so the analyzer can
                # offset and clamp pages correctly across chunks
                result = analyzer.analyze(
                    chunk_path, center_file_path, preloaded_jokbo_file=None,
                    chunk_info=(start_page, end_page),
                    original_lesson_path=file_path
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
        ordered_results = [None] * (len(chunks))
        # Place preloaded results first
        for idx, res in preload_results.items():
            if 0 <= idx < len(ordered_results):
                ordered_results[idx] = res
        # Fill with newly computed results
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
                    # Preserve which chunk failed for potential adaptive retry
                    try:
                        _chunk_info = chunks[idx]
                    except Exception:
                        _chunk_info = None
                    ordered_results[idx] = {"error": entry.get("error", "Unknown error"), "_chunk": _chunk_info}

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

        # Persist each chunk's result to disk (after normalization) with deterministic filenames
        # for audit/optional re-merge. Saving normalized results ensures downstream consumers
        # do not see temp chunk names.
        try:
            base = f"{mode}-{Path(file_path).stem}"
            chunk_dir = Path("output/temp/sessions") / self.session_id / "chunks" / base
            chunk_dir.mkdir(parents=True, exist_ok=True)
            for i, res in enumerate(ordered_results):
                out = {
                    "session_id": self.session_id,
                    "mode": mode,
                    "file": str(file_path),
                    "center_file": str(center_file_path),
                    "chunk_index": i,
                    "chunk_info": list(chunks[i][1:]) if (i < len(chunks)) else None,
                    "result": res,
                }
                with open(chunk_dir / f"chunk_{i:03d}.json", "w", encoding="utf-8") as f:
                    json.dump(out, f, ensure_ascii=False, indent=2)
        except Exception:
            # Best effort persistence; do not fail analysis if disk is unavailable
            pass

        # Replace any missing entries with error placeholders
        for i in range(len(ordered_results)):
            if ordered_results[i] is None:
                ordered_results[i] = {"error": "No result"}
        
        # Adaptive retry: split failed chunks once and try again cycling through all keys.
        # This helps with token limits on larger spans. However, if failures are due to
        # prompt blocking, splitting will not help. In that case, skip adaptive retry.
        try:
            from ..pdf.operations import PDFOperations as _PDFOps
            from ..parsers.result_merger import ResultMerger as _RM
            failed_indices = [i for i, r in enumerate(ordered_results) if isinstance(r, dict) and r.get("error")]
            # If all failed errors indicate prompt blocking, skip adaptive retry entirely
            try:
                _all_blocked = False
                if failed_indices:
                    _all_blocked = all(
                        isinstance(ordered_results[i], dict)
                        and isinstance(ordered_results[i].get("error"), str)
                        and ("prompt blocked" in ordered_results[i]["error"].lower())
                        for i in failed_indices
                    )
                if _all_blocked:
                    logger.info("All failed chunks were prompt-blocked; skipping adaptive split retries")
                    raise StopIteration  # short-circuit adaptive retry block
            except Exception:
                # If detection fails, proceed with best-effort adaptive retry below
                pass
            for i in failed_indices:
                try:
                    chunk_path, start_page, end_page = chunks[i]
                except Exception:
                    continue
                # Only split when there is more than one page
                try:
                    if int(end_page) <= int(start_page):
                        continue
                except Exception:
                    continue
                # Perform adaptive analyze on two halves and merge
                try:
                    mid = (int(start_page) + int(end_page)) // 2
                    left_path = _PDFOps.extract_pages(file_path, int(start_page), int(mid))
                    right_path = _PDFOps.extract_pages(file_path, int(mid) + 1, int(end_page))
                    left_res = self._analyze_chunk_adaptive(mode, file_path, center_file_path, (left_path, int(start_page), int(mid)))
                    right_res = self._analyze_chunk_adaptive(mode, file_path, center_file_path, (right_path, int(mid)+1, int(end_page)))
                    merged = _RM.merge_chunk_results([left_res if isinstance(left_res, dict) else {}, right_res if isinstance(right_res, dict) else {}], mode)
                    ordered_results[i] = merged if isinstance(merged, dict) else {"error": "merge_failed"}
                except Exception:
                    # keep original error if adaptive path also fails
                    pass
                finally:
                    try:
                        Path(left_path).unlink(missing_ok=True)
                    except Exception:
                        pass
                    try:
                        Path(right_path).unlink(missing_ok=True)
                    except Exception:
                        pass
        except StopIteration:
            # Intentional early exit from adaptive retry when all failures are prompt-blocked
            pass
        except Exception:
            # Best-effort; ignore adaptive retry failures
            pass
        
        # Optional: attempt a deterministic merge from persisted files, if all
        # chunk files exist. Fallback to in-memory ordered_results if any missing.
        merged: Optional[Dict[str, Any]] = None
        try:
            from ..parsers.result_merger import ResultMerger
            all_paths = []
            for i in range(len(tasks)):
                p = (Path("output/temp/sessions") / self.session_id / "chunks" / base / f"chunk_{i:03d}.json")
                if not p.exists():
                    all_paths = []
                    break
                all_paths.append(p)
            if all_paths:
                loaded_results: List[Dict[str, Any]] = []
                for p in all_paths:
                    try:
                        with open(p, "r", encoding="utf-8") as f:
                            payload = json.load(f)
                        res = payload.get("result") if isinstance(payload, dict) else None
                        # Re-apply filename normalization defensively on loaded chunk results
                        if isinstance(res, dict) and mode == "jokbo-centric":
                            try:
                                orig = Path(file_path).name
                                for page in (res.get("jokbo_pages") or []):
                                    for q in (page.get("questions") or []):
                                        for slide in (q.get("related_lesson_slides") or []):
                                            if isinstance(slide, dict):
                                                slide["lesson_filename"] = orig
                            except Exception:
                                pass
                        loaded_results.append(res if isinstance(res, dict) else (res or {}))
                    except Exception:
                        loaded_results.append({"error": "load_failed"})
                merged = ResultMerger.merge_chunk_results(loaded_results, mode)
        except Exception:
            merged = None

        from ..parsers.result_merger import ResultMerger
        return merged if isinstance(merged, dict) and merged else ResultMerger.merge_chunk_results(ordered_results, mode)

    def _analyze_chunk_adaptive(self, mode: str, file_path: str, center_file_path: str, chunk: tuple) -> Dict[str, Any]:
        """Analyze a specific chunk with failover across all keys; if it fails and spans multiple pages,
        split once more (recursively limited to two halves). Returns a (possibly empty) result dict.
        """
        (chunk_path, start_page, end_page) = chunk
        def _op(api_client, model):
            fm = FileManager(api_client)
            if mode == "lesson-centric":
                analyzer = LessonCentricAnalyzer(api_client, fm, self.session_id, self.debug_dir)
                # propagate threshold
                try:
                    if self.min_relevance_score is not None:
                        analyzer.set_relevance_threshold(self.min_relevance_score)
                except Exception:
                    pass
                return analyzer.analyze(center_file_path, chunk_path, None, chunk_info=(start_page, end_page))
            else:
                analyzer = JokboCentricAnalyzer(api_client, fm, self.session_id, self.debug_dir)
                res = analyzer.analyze(chunk_path, center_file_path, preloaded_jokbo_file=None, chunk_info=(start_page, end_page), original_lesson_path=file_path)
                # Normalize filenames back to original lesson (avoid tmp)
                try:
                    orig = Path(file_path).name
                    if isinstance(res, dict):
                        for page in (res.get("jokbo_pages") or []):
                            for q in (page.get("questions") or []):
                                for slide in (q.get("related_lesson_slides") or []):
                                    if isinstance(slide, dict):
                                        slide["lesson_filename"] = orig
                except Exception:
                    pass
                return res
        # Try each key once by setting retries to number of keys
        try:
            res = self.api_manager.execute_with_failover(_op, max_retries=max(1, len(self.api_manager.api_keys)))
            return res if isinstance(res, dict) else {}
        except Exception:
            # If still fails and multi-page, split into two halves and merge
            try:
                if int(end_page) > int(start_page):
                    from ..pdf.operations import PDFOperations as _PDFOps
                    from ..parsers.result_merger import ResultMerger as _RM
                    mid = (int(start_page) + int(end_page)) // 2
                    left_path = _PDFOps.extract_pages(file_path, int(start_page), int(mid))
                    right_path = _PDFOps.extract_pages(file_path, int(mid) + 1, int(end_page))
                    left_res = self._analyze_chunk_adaptive(mode, file_path, center_file_path, (left_path, int(start_page), int(mid)))
                    right_res = self._analyze_chunk_adaptive(mode, file_path, center_file_path, (right_path, int(mid)+1, int(end_page)))
                    merged = _RM.merge_chunk_results([left_res if isinstance(left_res, dict) else {}, right_res if isinstance(right_res, dict) else {}], mode)
                    try:
                        Path(left_path).unlink(missing_ok=True)
                        Path(right_path).unlink(missing_ok=True)
                    except Exception:
                        pass
                    return merged
            except Exception:
                pass
            return {"error": "adaptive_failed"}
    
    def get_api_status(self) -> Dict[str, Any]:
        """Get the status of all API keys."""
        return self.api_manager.get_status_report()
