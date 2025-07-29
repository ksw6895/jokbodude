"""
Validation utilities for PDF processing
"""
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path
import pymupdf as fitz


class PDFValidator:
    """Handles all PDF validation logic"""
    
    @staticmethod
    def validate_page_number(page_num: int, total_pages: int, filename: str = "") -> bool:
        """Validate if a page number is within valid range
        
        Args:
            page_num: Page number to validate (1-based)
            total_pages: Total number of pages in PDF
            filename: Optional filename for error messages
            
        Returns:
            True if valid, False otherwise
        """
        if page_num < 1 or page_num > total_pages:
            if filename:
                print(f"Warning: Page {page_num} does not exist in {filename} (total: {total_pages} pages)")
            else:
                print(f"Warning: Invalid page number {page_num} (total: {total_pages} pages)")
            return False
        return True
    
    @staticmethod
    def validate_and_adjust_page_number(page_num: int, start_page: int, end_page: int, 
                                      total_pages: int, context: str = "") -> Optional[int]:
        """Validate and adjust page number with retry logic
        
        Args:
            page_num: The page number to validate/adjust
            start_page: Start page of the chunk
            end_page: End page of the chunk
            total_pages: Total pages in the PDF
            context: Optional context for debugging
            
        Returns:
            Adjusted page number or None if invalid
        """
        chunk_page_count = end_page - start_page + 1
        
        # Case 1: Page number is within chunk range (1 to chunk_page_count)
        # This is likely a chunk-relative page number
        if 1 <= page_num <= chunk_page_count:
            adjusted = page_num + (start_page - 1)
            print(f"  페이지 조정: 청크 상대값 {page_num} → 절대값 {adjusted} (청크 p{start_page}-{end_page})")
            return adjusted
        
        # Case 2: Page number is within the actual chunk's absolute range
        # This is likely already an absolute page number
        if start_page <= page_num <= end_page:
            print(f"  페이지 유지: 절대값 {page_num} (청크 범위 p{start_page}-{end_page} 내)")
            return page_num
        
        # Case 3: Page number is within total PDF range but outside chunk
        # This might be a mistaken reference or API error
        if 1 <= page_num <= total_pages:
            print(f"  경고: 페이지 {page_num}은 청크 p{start_page}-{end_page} 범위 밖이지만 PDF 전체 범위 내 - 재분석 필요")
            return None
        
        # Case 4: Page number is completely invalid
        print(f"  오류: 잘못된 페이지 번호 {page_num} (PDF 전체 {total_pages}페이지 초과) - 재분석 필요")
        return None
    
    @staticmethod
    def filter_valid_questions(questions: List[Dict[str, Any]], total_pages: int, 
                             jokbo_filename: str) -> List[Dict[str, Any]]:
        """Filter out questions with invalid page numbers
        
        Args:
            questions: List of question dictionaries
            total_pages: Total pages in the jokbo PDF
            jokbo_filename: Jokbo filename for error messages
            
        Returns:
            List of valid questions
        """
        valid_questions = []
        for question in questions:
            # Force correct filename
            question["jokbo_filename"] = jokbo_filename
            
            # Validate page number
            jokbo_page = question.get("jokbo_page", 0)
            if jokbo_page < 1 or jokbo_page > total_pages:
                print(f"  경고: 잘못된 페이지 번호 감지 - 문제 {question.get('question_number', '?')}번, 페이지 {jokbo_page} (족보 총 {total_pages}페이지)")
                print(f"  → 이 문제는 강의자료에 포함된 문제일 가능성이 높습니다. 제외합니다.")
                continue
            
            valid_questions.append(question)
        
        return valid_questions
    
    @staticmethod
    def get_pdf_page_count(pdf_path: str) -> int:
        """Get total page count of a PDF file
        
        Args:
            pdf_path: Path to the PDF file
            
        Returns:
            Total number of pages in the PDF
        """
        with fitz.open(pdf_path) as doc:
            return len(doc)
    
    @staticmethod
    def validate_chunk_boundaries(start_page: int, end_page: int, total_pages: int) -> Tuple[int, int]:
        """Validate and adjust chunk boundaries
        
        Args:
            start_page: Requested start page (1-based)
            end_page: Requested end page (1-based)
            total_pages: Total pages in PDF
            
        Returns:
            Tuple of (adjusted_start, adjusted_end)
        """
        # Ensure start page is valid
        start_page = max(1, min(start_page, total_pages))
        
        # Ensure end page is valid and >= start page
        end_page = max(start_page, min(end_page, total_pages))
        
        return start_page, end_page