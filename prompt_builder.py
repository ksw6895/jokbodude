"""
Centralized prompt building for PDF processing system
"""
from constants import (
    COMMON_PROMPT_INTRO, 
    LESSON_CENTRIC_TASK, 
    LESSON_CENTRIC_OUTPUT_FORMAT,
    JOKBO_CENTRIC_TASK,
    JOKBO_CENTRIC_OUTPUT_FORMAT,
    COMMON_WARNINGS,
    RELEVANCE_CRITERIA
)


class PromptBuilder:
    """Builds prompts for different analysis modes"""
    
    @staticmethod
    def build_lesson_centric_prompt(jokbo_filename: str) -> str:
        """Build prompt for lesson-centric analysis
        
        Args:
            jokbo_filename: Name of the jokbo file being analyzed
            
        Returns:
            Complete prompt string
        """
        intro = COMMON_PROMPT_INTRO.format(
            first_file_desc="강의자료 PDF (참고용)",
            second_file_desc=f'족보 PDF "{jokbo_filename}" (분석 대상)'
        )
        output_format = LESSON_CENTRIC_OUTPUT_FORMAT.format(jokbo_filename=jokbo_filename)
        return f"{intro}\n\n{LESSON_CENTRIC_TASK}\n\n{COMMON_WARNINGS}\n\n{RELEVANCE_CRITERIA}\n\n{output_format}"
    
    @staticmethod
    def build_jokbo_centric_prompt(jokbo_filename: str, lesson_filename: str) -> str:
        """Build prompt for jokbo-centric analysis
        
        Args:
            jokbo_filename: Name of the jokbo file
            lesson_filename: Name of the lesson file being analyzed
            
        Returns:
            Complete prompt string
        """
        intro = f"""당신은 병리학 교수입니다. 하나의 족보(기출문제) PDF와 하나의 강의자료 PDF를 비교 분석합니다.

중요: 족보 파일명은 반드시 "{jokbo_filename}"을 그대로 사용하세요.
중요: 강의자료 파일명은 반드시 "{lesson_filename}"을 그대로 사용하세요."""
        
        output_format = JOKBO_CENTRIC_OUTPUT_FORMAT.format(
            jokbo_filename=jokbo_filename,
            lesson_filename=lesson_filename
        )
        return f"{intro}\n\n{JOKBO_CENTRIC_TASK}\n\n{COMMON_WARNINGS}\n\n{output_format}"
    
    @staticmethod
    def build_jokbo_centric_chunk_prompt(jokbo_filename: str, lesson_filename: str, 
                                       start_page: int, end_page: int) -> str:
        """Build prompt for jokbo-centric chunk analysis
        
        Args:
            jokbo_filename: Name of the jokbo file
            lesson_filename: Name of the original lesson file
            start_page: Starting page of the chunk
            end_page: Ending page of the chunk
            
        Returns:
            Complete prompt string
        """
        intro = f"""당신은 병리학 교수입니다. 하나의 족보(기출문제) PDF와 하나의 강의자료 PDF를 비교 분석합니다.

중요: 족보 파일명은 반드시 "{jokbo_filename}"을 그대로 사용하세요.
중요: 강의자료 파일명은 반드시 "{lesson_filename}"을 그대로 사용하세요."""
        
        output_format = JOKBO_CENTRIC_OUTPUT_FORMAT.format(
            jokbo_filename=jokbo_filename,
            lesson_filename=lesson_filename
        )
        return f"{intro}\n\n{JOKBO_CENTRIC_TASK}\n\n{COMMON_WARNINGS}\n\n{output_format}"