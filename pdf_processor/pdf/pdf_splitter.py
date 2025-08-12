"""PDF splitting functionality for chunk processing"""

from pathlib import Path
from typing import List, Tuple
from processing_config import ProcessingConfig
from .pdf_handler import PDFHandler


class PDFSplitter:
    """Handles splitting PDFs into chunks for processing"""
    
    def __init__(self):
        """Initialize PDF splitter"""
        self.pdf_handler = PDFHandler()
    
    def split_pdf_for_analysis(
        self, 
        pdf_path: str, 
        max_pages: int = ProcessingConfig.DEFAULT_CHUNK_SIZE
    ) -> List[Tuple[str, int, int]]:
        """Split PDF into smaller chunks for analysis
        
        Args:
            pdf_path: Path to the PDF file
            max_pages: Maximum pages per chunk
            
        Returns:
            List of tuples containing (chunk_path, start_page, end_page)
        """
        total_pages = self.pdf_handler.get_pdf_page_count(pdf_path)
        
        # If PDF is small enough, return as single chunk
        if total_pages <= max_pages:
            return [(pdf_path, 1, total_pages)]
        
        # Split into chunks
        chunks = []
        for start_page in range(1, total_pages + 1, max_pages):
            end_page = min(start_page + max_pages - 1, total_pages)
            chunk_path = self.pdf_handler.extract_pdf_pages(pdf_path, start_page, end_page)
            chunks.append((chunk_path, start_page, end_page))
            print(f"  청크 생성: 페이지 {start_page}-{end_page} ({chunk_path})")
        
        return chunks