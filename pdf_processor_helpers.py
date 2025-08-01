"""
Helper functions for PDF processor to reduce method complexity
"""
from typing import Dict, List, Any, Set, Tuple
import re
from validators import PDFValidator


class PDFProcessorHelpers:
    """Helper methods for PDFProcessor to reduce complexity"""
    
    @staticmethod
    def clean_json_text(response_text: str) -> str:
        """Clean up common JSON errors from Gemini
        
        Args:
            response_text: Raw response text from Gemini
            
        Returns:
            Cleaned text ready for JSON parsing
        """
        # Fix incorrectly quoted keys like "4"번" -> "4번"
        cleaned_text = re.sub(r'"(\d+)"번"', r'"\1번"', response_text)
        return cleaned_text
    
    @staticmethod
    def validate_question_pages(result: Dict[str, Any], start_page: int, end_page: int, 
                               total_pages: int, validator_func) -> Tuple[bool, Set[str]]:
        """Validate page numbers in questions and identify invalid ones
        
        Args:
            result: Result dictionary containing jokbo_pages
            start_page: Start page of chunk
            end_page: End page of chunk
            total_pages: Total pages in the PDF
            validator_func: Function to validate and adjust page numbers
            
        Returns:
            Tuple of (retry_needed, invalid_questions)
        """
        retry_needed = False
        invalid_questions = set()
        
        if "jokbo_pages" in result:
            for page_info in result["jokbo_pages"]:
                for question in page_info.get("questions", []):
                    question_num = question.get("question_number", "Unknown")
                    has_invalid_page = False
                    
                    for slide in question.get("related_lesson_slides", []):
                        if "lesson_page" in slide:
                            page_num = slide["lesson_page"]
                            adjusted_page = validator_func(
                                page_num, start_page, end_page, total_pages, 
                                f"Q{question_num}"
                            )
                            
                            if adjusted_page is None:
                                has_invalid_page = True
                                retry_needed = True
                            else:
                                slide["lesson_page"] = adjusted_page
                    
                    if has_invalid_page:
                        invalid_questions.add(question_num)
        
        return retry_needed, invalid_questions
    
    @staticmethod
    def remove_invalid_questions(result: Dict[str, Any], invalid_questions: Set[str]) -> None:
        """Remove questions with invalid page numbers from result
        
        Args:
            result: Result dictionary to modify
            invalid_questions: Set of question numbers to remove
        """
        if "jokbo_pages" in result and invalid_questions:
            print(f"  경고: 잘못된 페이지 번호 문제 제외: {', '.join(invalid_questions)}")
            for page_info in result["jokbo_pages"]:
                page_info["questions"] = [
                    q for q in page_info.get("questions", [])
                    if q.get("question_number") not in invalid_questions
                ]
    
    @staticmethod
    def build_chunk_prompt(jokbo_filename: str, lesson_filename: str, 
                          start_page: int, end_page: int, 
                          task: str, warnings: str, output_format: str) -> str:
        """Build prompt for chunk analysis
        
        Args:
            jokbo_filename: Name of jokbo file
            lesson_filename: Name of lesson file
            start_page: Start page of chunk
            end_page: End page of chunk
            task: Task description
            warnings: Warning text
            output_format: Output format specification
            
        Returns:
            Complete prompt string
        """
        # Import here to avoid circular dependency
        from prompt_builder import PromptBuilder
        return PromptBuilder.build_jokbo_centric_chunk_prompt(jokbo_filename, lesson_filename, start_page, end_page)