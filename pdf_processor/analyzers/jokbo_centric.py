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
        # Delete existing files first (tracked for this client only)
        try:
            key_tag = self.file_manager.api_client._key_tag() if getattr(self.file_manager, 'api_client', None) else None
        except Exception:
            key_tag = None
        if key_tag:
            logger.info(f"Cleaning up existing uploaded files... [key={key_tag}]")
        else:
            logger.info("Cleaning up existing uploaded files...")
        self.file_manager.delete_all_uploaded_files()
        
        # Upload and analyze
        files_to_upload = [
            (jokbo_path, f"족보_{jokbo_filename}"),
            (lesson_path, f"강의자료_{lesson_filename}")
        ]
        
        try:
            response_text = self.upload_and_analyze(files_to_upload, prompt)
            
            # Clean up except center file
            self.file_manager.cleanup_except_center_file(f"족보_{jokbo_filename}")
            
            return response_text
            
        except Exception as e:
            logger.error(f"Analysis failed: {str(e)}")
            raise PDFProcessorError(f"Failed to analyze PDFs: {str(e)}")
    
    def _analyze_with_preloaded_jokbo(self, prompt: str, lesson_path: str,
                                     jokbo_file: Any, lesson_filename: str) -> str:
        """Analyze with pre-uploaded jokbo file."""
        # Upload only lesson
        lesson_file = self.api_client.upload_file(lesson_path, f"강의자료_{lesson_filename}")
        self.file_manager.track_file(lesson_file)
        
        try:
            # Prepare content and generate with quality-aware retry
            content = [prompt, jokbo_file, lesson_file]
            response_text = self._generate_with_quality_retry(content)
            return response_text
            
        finally:
            # Delete lesson file
            self.file_manager.delete_file_safe(lesson_file)
    
    def _post_process_results(self, result: Dict[str, Any],
                            chunk_info: Optional[Tuple[int, int]] = None,
                            context_lesson_path: Optional[str] = None) -> Dict[str, Any]:
        """Post-process analysis results.

        Applies chunk page offset only to slides that belong to the current lesson
        and clamps to the lesson's total pages to avoid runaway indices when
        responses mix references across lessons.
        """
        if not (chunk_info and isinstance(result, dict) and "jokbo_pages" in result):
            return result

        start_page, _ = chunk_info
        offset = max(0, int(start_page) - 1)

        # Prepare normalized expected lesson filename for comparison
        expected_name_norm = None
        total_pages = None
        try:
            from pathlib import Path as _P
            if context_lesson_path:
                expected_name = _P(context_lesson_path).name
                # Strip common AI-added prefixes and normalize for robust match
                import re as _re
                expected_name_norm = _re.sub(r"^(강의자료|강의|lesson|lecture)[\s_\-]+", "", expected_name, flags=_re.IGNORECASE).strip().lower()
        except Exception:
            expected_name_norm = None

        # Lazy-load total pages for clamping if we have a known lesson path
        def _get_total_pages() -> int:
            nonlocal total_pages
            if total_pages is None:
                try:
                    from ..pdf.operations import PDFOperations as _PDFOps
                    total_pages = int(_PDFOps.get_page_count(context_lesson_path)) if context_lesson_path else 0
                except Exception:
                    total_pages = 0
            return total_pages or 0

        import re as _re
        for page_info in (result.get("jokbo_pages") or []):
            for question in (page_info.get("questions") or []):
                for slide in (question.get("related_lesson_slides") or []):
                    if not isinstance(slide, dict):
                        continue
                    if "lesson_page" not in slide:
                        continue
                    # Decide whether this slide belongs to the current lesson
                    try:
                        lf_raw = (slide.get("lesson_filename") or "").strip()
                        lf_norm = _re.sub(r"^(강의자료|강의|lesson|lecture)[\s_\-]+", "", lf_raw, flags=_re.IGNORECASE).strip().lower()
                    except Exception:
                        lf_norm = ""

                    belongs_here = False
                    if expected_name_norm:
                        # Apply offset only if filename is empty/unknown or matches current lesson
                        belongs_here = (lf_norm == "" or lf_norm == expected_name_norm)
                    else:
                        # If we don't know expected filename, conservatively apply offset
                        belongs_here = True

                    if not belongs_here:
                        continue

                    # Apply offset
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
        
        # Pre-upload jokbo file for efficiency
        logger.info(f"Pre-uploading jokbo file: {jokbo_filename}")
        jokbo_file = self.api_client.upload_file(jokbo_path, f"족보_{jokbo_filename}")
        self.file_manager.track_file(jokbo_file)
        
        all_connections = {}  # {question_id: {question_data, connections}}
        lesson_results = []
        
        try:
            for idx, lesson_path in enumerate(lesson_paths):
                logger.info(f"Analyzing lesson {idx+1}/{len(lesson_paths)}: {Path(lesson_path).name}")
                
                try:
                    # Perform analysis (may chunk internally)
                    result = self.analyze(lesson_path, jokbo_path, jokbo_file)
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
                    
        finally:
            # Clean up jokbo file
            self.file_manager.delete_file_safe(jokbo_file)
        
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
                    
                    # Add related slides
                    for slide in question.get("related_lesson_slides", []):
                        all_connections[question_id]["connections"].append(slide)
        
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
            filtered_connections = self.filter_connections(connections, min_score=getattr(self, 'min_relevance_score', 70))
            
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
