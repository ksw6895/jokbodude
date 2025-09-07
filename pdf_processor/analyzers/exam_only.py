"""
Exam-only analyzer.

Takes a jokbo (exam) PDF chunk and produces per-question answers with
explanations and short background knowledge. This mode does not relate
to lesson slides; it is explanation-focused.
"""

from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path

from .base import BaseAnalyzer
from ..utils.logging import get_logger

logger = get_logger(__name__)


class ExamOnlyAnalyzer(BaseAnalyzer):
    """Analyzer for the exam-only mode."""

    def get_mode(self) -> str:
        return "exam-only"

    def build_prompt(self, jokbo_filename: str, q_start: int, q_end: int) -> str:
        """Build the exam-only prompt for a given question range.

        Args:
            jokbo_filename: The base jokbo filename (for context only)
            q_start: inclusive start question number
            q_end: inclusive end question number
        """
        from constants import (
            EXAM_ONLY_TASK,
            EXAM_ONLY_OUTPUT_FORMAT,
        )

        prompt = f"""
다음은 족보 PDF 일부({q_start}~{q_end}번)입니다. 원본 파일명: {jokbo_filename}

{EXAM_ONLY_TASK}

{EXAM_ONLY_OUTPUT_FORMAT}
"""
        return prompt.strip()

    def analyze_chunk(
        self,
        jokbo_chunk_path: str,
        jokbo_original_filename: str,
        q_range: Tuple[int, int],
        chunk_info: Optional[Tuple[int, int]] = None,
    ) -> Dict[str, Any]:
        """Analyze a single jokbo chunk covering a question range.

        The model will produce page numbers relative to the uploaded chunk.
        We post-adjust them to the original (full) jokbo using chunk_info.
        """

        q_start, q_end = q_range
        prompt = self.build_prompt(jokbo_original_filename, int(q_start), int(q_end))
        files_to_upload = [(jokbo_chunk_path, f"족보_{Path(jokbo_original_filename).name}_Q{q_start}-{q_end}.pdf")]

        response_text = self.upload_and_analyze(files_to_upload, prompt)

        # Save debug for troubleshooting
        try:
            self.save_debug_response(response_text, Path(jokbo_original_filename).name, f"Q{q_start}-{q_end}")
        except Exception:
            pass

        parsed = self.parse_and_validate_response(response_text)
        return self._post_process_pages(parsed, chunk_info)

    def _post_process_pages(self, result: Dict[str, Any], chunk_info: Optional[Tuple[int, int]]) -> Dict[str, Any]:
        """Offset page_start/next_question_start by chunk start_page - 1 if chunk_info provided."""
        if not (chunk_info and isinstance(result, dict) and isinstance(result.get("questions"), list)):
            return result
        try:
            start_page, end_page = chunk_info
            start_page = int(start_page)
            end_page = int(end_page)
        except Exception:
            return result
        offset = max(0, start_page - 1)

        for q in result.get("questions", []):
            if not isinstance(q, dict):
                continue
            try:
                ps = int(q.get("page_start") or 0)
            except Exception:
                ps = 0
            if ps > 0:
                q["page_start"] = ps + offset
            try:
                nqs = q.get("next_question_start")
                if nqs is not None:
                    nqs_i = int(nqs)
                    if nqs_i > 0:
                        q["next_question_start"] = nqs_i + offset
            except Exception:
                pass
        return result

