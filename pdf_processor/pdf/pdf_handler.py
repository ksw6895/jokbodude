"""Core PDF handling functionality"""

from pathlib import Path
from typing import Dict, Optional
import pymupdf as fitz
from validators import PDFValidator


class PDFHandler:
    """Handles basic PDF operations"""
    
    def __init__(self):
        """Initialize PDF handler with page count cache"""
        self.pdf_page_counts: Dict[str, int] = {}
    
    def get_pdf_page_count(self, pdf_path: str) -> int:
        """Get total page count of a PDF file (cached)
        
        Args:
            pdf_path: Path to the PDF file
            
        Returns:
            Total number of pages in the PDF
        """
        if pdf_path not in self.pdf_page_counts:
            self.pdf_page_counts[pdf_path] = PDFValidator.get_pdf_page_count(pdf_path)
        return self.pdf_page_counts[pdf_path]
    
    def validate_and_adjust_page_number(
        self, 
        page_num: int, 
        start_page: int, 
        end_page: int,
        total_pages: int, 
        chunk_path: str
    ) -> Optional[int]:
        """Validate and adjust page number with retry logic
        
        Args:
            page_num: Page number to validate
            start_page: Start page of chunk
            end_page: End page of chunk
            total_pages: Total pages in original PDF
            chunk_path: Path to chunk file
            
        Returns:
            Adjusted page number or None if invalid
        """
        return PDFValidator.validate_and_adjust_page_number(
            page_num, start_page, end_page, total_pages, chunk_path
        )
    
    def extract_pdf_pages(self, pdf_path: str, start_page: int, end_page: int) -> str:
        """Extract specific pages from PDF and save to temporary file
        
        Args:
            pdf_path: Path to source PDF
            start_page: First page to extract (1-based)
            end_page: Last page to extract (1-based)
            
        Returns:
            Path to temporary PDF file containing extracted pages
        """
        import tempfile
        import os
        
        try:
            doc = fitz.open(pdf_path)
            output_doc = fitz.open()
            
            # Convert to 0-based indexing for PyMuPDF
            for page_num in range(start_page - 1, min(end_page, len(doc))):
                output_doc.insert_pdf(doc, from_page=page_num, to_page=page_num)
            
            # Create temporary file
            temp_fd, temp_path = tempfile.mkstemp(suffix='.pdf')
            os.close(temp_fd)
            
            output_doc.save(temp_path)
            output_doc.close()
            doc.close()
            
            return temp_path
            
        except Exception as e:
            from error_handler import ErrorHandler
            ErrorHandler.handle_file_operation_error(e, pdf_path, "extract pages")
            raise