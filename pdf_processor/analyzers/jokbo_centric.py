"""
Jokbo-centric analyzer implementation.
Analyzes jokbo (exam) files against lessons to find source material.
"""

from typing import Dict, Any, List, Tuple, Optional
from pathlib import Path
import json
from datetime import datetime
import pymupdf as fitz

from .base import BaseAnalyzer
from ..utils.logging import get_logger
from ..utils.exceptions import PDFProcessorError

logger = get_logger(__name__)


class JokboCentricAnalyzer(BaseAnalyzer):
    """Analyzer for jokbo-centric mode."""
    
    def get_mode(self) -> str:
        """Get the analyzer mode name."""
        return "jokbo-centric"
    
    def build_prompt(self, lesson_filename: str) -> str:
        """
        Build the jokbo-centric analysis prompt.
        
        Args:
            lesson_filename: Name of the lesson file being analyzed
            
        Returns:
            Complete prompt string
        """
        # Import from constants to avoid circular imports
        from constants import (
            COMMON_PROMPT_INTRO, COMMON_WARNINGS, RELEVANCE_CRITERIA,
            EXPLANATION_GUIDELINES,
            JOKBO_CENTRIC_TASK, JOKBO_CENTRIC_OUTPUT_FORMAT
        )
        
        prompt = f"""
{COMMON_PROMPT_INTRO}

분석 대상 강의자료 파일명: {lesson_filename}

{JOKBO_CENTRIC_TASK}

{COMMON_WARNINGS}

{RELEVANCE_CRITERIA}

{EXPLANATION_GUIDELINES}

{JOKBO_CENTRIC_OUTPUT_FORMAT}
"""
        return prompt.strip()
    
    def analyze(self, lesson_path: str, jokbo_path: str,
                preloaded_jokbo_file: Optional[Any] = None,
                chunk_info: Optional[Tuple[int, int]] = None,
                original_lesson_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Analyze a lesson against a jokbo.
        
        Args:
            lesson_path: Path to lesson PDF
            jokbo_path: Path to jokbo PDF
            preloaded_jokbo_file: Optional pre-uploaded jokbo file
            chunk_info: Optional (start_page, end_page) for chunked processing
            
        Returns:
            Analysis results
        """
        lesson_filename = Path(lesson_path).name
        jokbo_filename = Path(jokbo_path).name
        
        logger.info(f"Analyzing lesson '{lesson_filename}' with jokbo '{jokbo_filename}'")
        
        # Build prompt
        prompt = self.build_prompt(lesson_filename)
        
        # Handle chunked processing
        if self._should_chunk_lesson(lesson_path):
            return self._analyze_with_chunks(
                lesson_path, jokbo_path, preloaded_jokbo_file
            )
        
        # Single file analysis
        if preloaded_jokbo_file:
            response_text = self._analyze_with_preloaded_jokbo(
                prompt, lesson_path, preloaded_jokbo_file, lesson_filename
            )
        else:
            response_text = self._analyze_with_uploads(
                prompt, lesson_path, jokbo_path, lesson_filename, jokbo_filename
            )
        
        # Save debug response
        self.save_debug_response(response_text, lesson_filename, jokbo_filename)
        
        # Parse response
        result = self.parse_and_validate_response(response_text)
        
        # Post-process results with chunk offset and clamping using original lesson path
        # Use the original (full) lesson path if provided; otherwise the current lesson_path
        context_lesson_path = original_lesson_path or lesson_path
        result = self._post_process_results(result, chunk_info, context_lesson_path)
        
        return result
    
    def _should_chunk_lesson(self, lesson_path: str, max_pages: int = 30) -> bool:
        """Check if lesson PDF needs chunking."""
        from ..pdf.operations import PDFOperations
        page_count = PDFOperations.get_page_count(lesson_path)
        return page_count > max_pages
    
    def _analyze_with_chunks(self, lesson_path: str, jokbo_path: str,
                           preloaded_jokbo_file: Optional[Any] = None) -> Dict[str, Any]:
        """Analyze lesson in chunks."""
        from ..pdf.operations import PDFOperations

        chunks = PDFOperations.split_pdf_for_chunks(lesson_path)
        logger.info(f"Processing {len(chunks)} chunks for {Path(lesson_path).name}")

        chunk_results = []

        # Keep original lesson filename for display in results (avoid tmp chunk names)
        original_lesson_filename = Path(lesson_path).name

        for i, (path, start_page, end_page) in enumerate(chunks):
            logger.info(f"Processing chunk {i+1}/{len(chunks)}: pages {start_page}-{end_page}")
            
            # Extract chunk
            chunk_path = PDFOperations.extract_pages(path, start_page, end_page)
            
            try:
                # Analyze chunk
                result = self.analyze(
                    chunk_path, jokbo_path, preloaded_jokbo_file,
                    chunk_info=(start_page, end_page),
                    original_lesson_path=lesson_path
                )
                # Normalize lesson filenames in related slides back to original
                try:
                    for page in result.get("jokbo_pages", []):
                        for q in page.get("questions", []):
                            for slide in q.get("related_lesson_slides", []) or []:
                                if isinstance(slide, dict):
                                    slide["lesson_filename"] = original_lesson_filename
                except Exception:
                    pass
                chunk_results.append(result)
                # Update chunk progress
                try:
                    from storage_manager import StorageManager
                    StorageManager().increment_chunk(self.session_id, 1,
                        f"청크 진행: {i+1}/{len(chunks)} ({Path(lesson_path).name})")
                except Exception:
                    pass
            finally:
                # Clean up
                Path(chunk_path).unlink(missing_ok=True)
        
        # Merge results
        from ..parsers.result_merger import ResultMerger
        return ResultMerger.merge_chunk_results(chunk_results, self.get_mode())
    
    def _analyze_with_uploads(self, prompt: str, lesson_path: str, jokbo_path: str,
                             lesson_filename: str, jokbo_filename: str) -> str:
        """Analyze with uploading both files."""
        # Optional purge: disabled by default. Gemini requests only see provided contents.
        # Enable by setting GEMINI_PURGE_BEFORE_UPLOAD=true if you really want a hard reset.
        try:
            import os
            _purge = str(os.getenv("GEMINI_PURGE_BEFORE_UPLOAD", "false")).strip().lower() in ("1","true","t","y","yes")
        except Exception:
            _purge = False
        if _purge:
            try:
                from ..api.upload_cleanup import purge_key_files
                key_tag = self.api_client._key_tag()
                logger.info(f"Purging ALL uploads before analysis [key={key_tag}]")
                purge_key_files(self.api_client, delete_prefixes=[], keep_display_names=set(), delete_all=True, log_context="jokbo_centric_preupload")
            except Exception:
                logger.info("Purge skipped due to error; continuing")
        
        # Upload and analyze
        files_to_upload = [
            (jokbo_path, f"족보_{jokbo_filename}"),
            (lesson_path, f"강의자료_{lesson_filename}")
        ]
        
        try:
            response_text = self.upload_and_analyze(files_to_upload, prompt)
            return response_text
        except Exception as e:
            logger.error(f"Analysis failed: {str(e)}")
            raise PDFProcessorError(f"Failed to analyze PDFs: {str(e)}")
    
    def _analyze_with_preloaded_jokbo(self, prompt: str, lesson_path: str,
                                     jokbo_file: Any, lesson_filename: str) -> str:
        """Analyze with pre-uploaded jokbo file."""
        # Preloaded jokbo path is deprecated for safety (always upload both)
        # Fall back to standard uploads path
        return self._analyze_with_uploads(prompt, lesson_path, jokbo_path, lesson_filename, jokbo_filename)
    
    def _post_process_results(self, result: Dict[str, Any],
                            chunk_info: Optional[Tuple[int, int]] = None,
                            context_lesson_path: Optional[str] = None) -> Dict[str, Any]:
        """Post-process analysis results for chunked lessons.

        Gemini sees chunk pages starting at 1. When a lesson is split into
        chunks (start_page..end_page), we must offset every referenced
        lesson_page by (start_page - 1) to map back to the original PDF.

        We apply the offset to all slides in this chunk result, regardless of
        how the model labeled lesson_filename (which may be a temp chunk name),
        and clamp to the original lesson's page count when available.
        """
        if not (chunk_info and isinstance(result, dict) and "jokbo_pages" in result):
            return result

        start_page, _ = chunk_info
        offset = max(0, int(start_page) - 1)

        # Lazy-load total pages for clamping if we have a known lesson path
        total_pages = None
        def _get_total_pages() -> int:
            nonlocal total_pages
            if total_pages is None:
                try:
                    from ..pdf.operations import PDFOperations as _PDFOps
                    total_pages = int(_PDFOps.get_page_count(context_lesson_path)) if context_lesson_path else 0
                except Exception:
                    total_pages = 0
            return total_pages or 0

        for page_info in (result.get("jokbo_pages") or []):
            for question in (page_info.get("questions") or []):
                for slide in (question.get("related_lesson_slides") or []):
                    if not isinstance(slide, dict):
                        continue
                    try:
                        lp = int(slide.get("lesson_page", 0))
                    except Exception:
                        lp = 0
                    if lp <= 0:
                        continue
                    new_lp = lp + offset
                    # Clamp to document bounds if known
                    tp = _get_total_pages()
                    if tp > 0:
                        new_lp = max(1, min(new_lp, tp))
                    slide["lesson_page"] = new_lp

        return result
    
    def analyze_multiple_lessons(self, lesson_paths: List[str], jokbo_path: str,
                               save_intermediate: bool = True) -> Dict[str, Any]:
        """
        Analyze multiple lessons against a single jokbo.
        
        Args:
            lesson_paths: List of lesson file paths
            jokbo_path: Path to jokbo file
            save_intermediate: Whether to save intermediate results
            
        Returns:
            Merged analysis results
        """
        jokbo_filename = Path(jokbo_path).name
        
        # Session tracking
        session_info = {
            'session_id': self.session_id,
            'mode': 'jokbo-centric',
            'jokbo_path': jokbo_path,
            'lesson_paths': lesson_paths,
            'total_lessons': len(lesson_paths),
            'processed_lessons': 0,
            'status': 'processing',
            'started_at': datetime.now().isoformat()
        }
        
        all_connections = {}  # {question_id: {question_data, connections}}
        lesson_results = []
        
        for idx, lesson_path in enumerate(lesson_paths):
            logger.info(f"Analyzing lesson {idx+1}/{len(lesson_paths)}: {Path(lesson_path).name}")
            
            try:
                # Perform analysis (may chunk internally). Always upload both per-call
                result = self.analyze(lesson_path, jokbo_path, None)
                # If lesson processed without chunking, count as one chunk
                try:
                    from ..pdf.operations import PDFOperations
                    chunks = PDFOperations.split_pdf_for_chunks(lesson_path)
                    if len(chunks) <= 1:
                        from storage_manager import StorageManager
                        StorageManager().increment_chunk(self.session_id, 1,
                            f"파일 완료: {Path(lesson_path).name}")
                except Exception:
                    pass
                
                if save_intermediate:
                    # Save intermediate result
                    self._save_intermediate_result(idx, lesson_path, result)
                
                lesson_results.append(result)
                
                # Update session info
                session_info['processed_lessons'] = idx + 1
                
            except Exception as e:
                logger.error(f"Failed to analyze {lesson_path}: {str(e)}")
                lesson_results.append({"error": str(e), "lesson_path": lesson_path})
        
        # Merge all results
        return self._merge_lesson_results(lesson_results, jokbo_path)
    
    def _save_intermediate_result(self, idx: int, lesson_path: str, result: Dict[str, Any]) -> None:
        """Save intermediate analysis result."""
        chunk_dir = Path(self.debug_dir) / self.session_id / "chunks"
        chunk_dir.mkdir(parents=True, exist_ok=True)
        
        filename = f"lesson_{idx:03d}_{Path(lesson_path).stem}_result.json"
        filepath = chunk_dir / filename
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump({
                'lesson_idx': idx,
                'lesson_path': lesson_path,
                'result': result
            }, f, ensure_ascii=False, indent=2)
        
        logger.debug(f"Saved intermediate result to {filepath}")
        # Optionally mirror to Redis debug storage (best effort)
        try:
            from storage_manager import StorageManager
            StorageManager().store_debug_json(self.session_id, f"jokbo_chunk_{idx:03d}", {
                'lesson_idx': idx,
                'lesson_filename': Path(lesson_path).name,
                'result': result,
            })
        except Exception:
            pass
    
    def _merge_lesson_results(self, results: List[Dict[str, Any]], 
                            jokbo_path: str) -> Dict[str, Any]:
        """Merge results from multiple lessons."""
        all_connections = {}  # {question_id: {question_data, connections}}
        
        # Collect all connections
        for result in results:
            if "error" in result:
                continue
                
            for page_info in result.get("jokbo_pages", []):
                jokbo_page = page_info["jokbo_page"]
                
                for question in page_info.get("questions", []):
                    question_id = f"{jokbo_page}_{question['question_number']}"
                    
                    # Initialize question data if first time
                    if question_id not in all_connections:
                        all_connections[question_id] = {
                            "question_data": {
                                "jokbo_page": jokbo_page,
                                "question_number": question["question_number"],
                                "question_text": question["question_text"],
                                "answer": question["answer"],
                                "explanation": question["explanation"],
                                "wrong_answer_explanations": question.get("wrong_answer_explanations", {}),
                                "question_numbers_on_page": question.get("question_numbers_on_page", [])
                            },
                            "connections": []
                        }
                    
                    # Add related slides, carrying the source QA fields so that
                    # we can keep explanation/answer consistent with the slides
                    # we end up selecting for this question.
                    for slide in question.get("related_lesson_slides", []):
                        try:
                            s = dict(slide)
                        except Exception:
                            s = slide
                        if isinstance(s, dict):
                            s.setdefault("_source_explanation", question.get("explanation"))
                            s.setdefault("_source_answer", question.get("answer"))
                            s.setdefault("_source_question_text", question.get("question_text"))
                            s.setdefault("_source_wrong_answers", question.get("wrong_answer_explanations", {}))
                        all_connections[question_id]["connections"].append(s)

        # Build final result with filtered connections
        final_pages = {}
        
        for question_id, data in all_connections.items():
            question_data = data["question_data"]
            connections = data["connections"]
            jokbo_page = question_data["jokbo_page"]
            
            # Initialize page if needed
            if jokbo_page not in final_pages:
                final_pages[jokbo_page] = {
                    "jokbo_page": jokbo_page,
                    "questions": []
                }
            
            # Filter connections with user-configured threshold
            min_thr = getattr(self, 'min_relevance_score', 80)
            filtered_connections = self.filter_connections(connections, min_score=min_thr)
            try:
                logger.debug(
                    f"merge_jokbo: Q={question_data.get('question_number')} @page={jokbo_page}"
                    f" connections={len(connections)} -> kept={len(filtered_connections)} (min={min_thr})"
                )
                qnums = question_data.get("question_numbers_on_page")
                if qnums:
                    logger.debug(f"merge_jokbo: Q={question_data.get('question_number')} page_qnums={qnums}")
            except Exception:
                pass
            
            # Keep explanation/answer consistent with the selected slides:
            # choose the best slide's originating explanation/answer if present.
            if filtered_connections:
                top = filtered_connections[0]
                if isinstance(top, dict):
                    try:
                        src_expl = top.get("_source_explanation")
                        if src_expl:
                            question_data["explanation"] = src_expl
                        src_ans = top.get("_source_answer")
                        if src_ans:
                            question_data["answer"] = src_ans
                        src_qt = top.get("_source_question_text")
                        if src_qt:
                            question_data["question_text"] = src_qt
                        src_wa = top.get("_source_wrong_answers")
                        if isinstance(src_wa, dict):
                            question_data["wrong_answer_explanations"] = src_wa
                    except Exception:
                        pass

            # Add question with filtered connections
            question_data["related_lesson_slides"] = filtered_connections
            final_pages[jokbo_page]["questions"].append(question_data)
        
        # Convert to list and sort
        result = {
            "jokbo_pages": sorted(final_pages.values(), key=lambda x: int(str(x["jokbo_page"]) or 0))
        }
        
        logger.info(f"Merged results: {len(result['jokbo_pages'])} pages, "
                   f"{sum(len(p['questions']) for p in result['jokbo_pages'])} questions")
        
        return result
