"""
Partial Jokbo analyzer.

Generates per-question page ranges and short explanations using Gemini.
"""

from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path

from .base import BaseAnalyzer
from ..utils.logging import get_logger

logger = get_logger(__name__)


class PartialJokboAnalyzer(BaseAnalyzer):
    """Analyzer for partial-jokbo mode."""

    def get_mode(self) -> str:
        return "partial-jokbo"

    def build_prompt(self, jokbo_filename: str, lesson_filenames: List[str]) -> str:
        from constants import (
            COMMON_PROMPT_INTRO,
            COMMON_WARNINGS,
            EXPLANATION_GUIDELINES,
            PARTIAL_JOKBO_TASK,
            PARTIAL_JOKBO_OUTPUT_FORMAT,
        )

        try:
            lessons_str = ", ".join(lesson_filenames)
        except Exception:
            lessons_str = ", ".join(str(x) for x in (lesson_filenames or []))

        prompt = f"""
{COMMON_PROMPT_INTRO}

분석 대상 족보 파일명: {jokbo_filename}
참조 강의자료 파일들: {lessons_str}

{PARTIAL_JOKBO_TASK}

{COMMON_WARNINGS}

{EXPLANATION_GUIDELINES}

{PARTIAL_JOKBO_OUTPUT_FORMAT}
"""
        return prompt.strip()

    def analyze(self, jokbo_path: str, lesson_paths: List[str]) -> Dict[str, Any]:
        """Analyze a single jokbo against multiple lessons to produce page ranges.

        Returns a structure like {"questions": [...]}.
        """
        jokbo_filename = Path(jokbo_path).name
        lesson_filenames = [Path(p).name for p in lesson_paths or []]

        prompt = self.build_prompt(jokbo_filename, lesson_filenames)

        files_to_upload = [(jokbo_path, f"족보_{jokbo_filename}")]
        for lp in lesson_paths or []:
            files_to_upload.append((lp, f"강의자료_{Path(lp).name}"))

        # Upload everything and get raw response text
        response_text = self.upload_and_analyze(files_to_upload, prompt)

        # Save debug for troubleshooting
        try:
            self.save_debug_response(response_text, jokbo_filename, "partial_jokbo")
        except Exception:
            pass

        return self.parse_and_validate_response(response_text)

