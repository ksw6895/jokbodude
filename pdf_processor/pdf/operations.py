"""
PDF manipulation operations module.
Handles PDF reading, splitting, page extraction, and other PDF-related operations.
"""

import tempfile
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any
import pymupdf as fitz

from ..utils.logging import get_logger
from ..utils.exceptions import PDFParsingError, FileNotFoundError

logger = get_logger(__name__)


class PDFOperations:
    """Handles all PDF manipulation operations."""
    
    @staticmethod
    def get_page_count(pdf_path: str) -> int:
        """
        Get the total number of pages in a PDF file.
        
        Args:
            pdf_path: Path to the PDF file
            
        Returns:
            Total number of pages
            
        Raises:
            FileNotFoundError: If PDF file doesn't exist
            PDFParsingError: If PDF cannot be opened
        """
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")
            
        try:
            with fitz.open(str(pdf_path)) as pdf:
                return len(pdf)
        except Exception as e:
            logger.error(f"Failed to open PDF {pdf_path}: {str(e)}")
            raise PDFParsingError(f"Cannot open PDF file: {str(e)}")
    
    @staticmethod
    def split_pdf_for_chunks(pdf_path: str, max_pages: int = 40) -> List[Tuple[str, int, int]]:
        """
        Split PDF into chunks for processing.
        
        Args:
            pdf_path: Path to the PDF file
            max_pages: Maximum pages per chunk
            
        Returns:
            List of tuples (pdf_path, start_page, end_page)
        """
        pdf_path = Path(pdf_path)
        total_pages = PDFOperations.get_page_count(str(pdf_path))
        
        if total_pages <= max_pages:
            # Small enough to process as one chunk
            logger.debug(f"PDF {pdf_path.name} fits in single chunk ({total_pages} pages)")
            return [(str(pdf_path), 1, total_pages)]
        
        # Split into chunks
        chunks = []
        for start in range(0, total_pages, max_pages):
            end = min(start + max_pages, total_pages)
            chunks.append((str(pdf_path), start + 1, end))
        
        logger.info(f"Split {pdf_path.name} into {len(chunks)} chunks ({total_pages} pages total)")
        return chunks
    
    @staticmethod
    def extract_pages(pdf_path: str, start_page: int, end_page: int, 
                     output_path: Optional[str] = None) -> str:
        """
        Extract specific pages from a PDF and save to a new file.
        
        Args:
            pdf_path: Path to source PDF
            start_page: First page to extract (1-based)
            end_page: Last page to extract (1-based)
            output_path: Optional output path (creates temp file if not provided)
            
        Returns:
            Path to the extracted PDF file
            
        Raises:
            PDFParsingError: If extraction fails
        """
        try:
            with fitz.open(str(pdf_path)) as src_pdf:
                # Validate page numbers
                total_pages = len(src_pdf)
                if start_page < 1 or start_page > total_pages:
                    raise ValueError(f"Invalid start page {start_page} (total pages: {total_pages})")
                if end_page < start_page or end_page > total_pages:
                    raise ValueError(f"Invalid end page {end_page} (total pages: {total_pages})")
                
                # Create new PDF with selected pages
                output = fitz.open()
                
                # Convert to 0-based indexing
                for page_num in range(start_page - 1, end_page):
                    output.insert_pdf(src_pdf, from_page=page_num, to_page=page_num)
                
                # Determine output path
                if output_path is None:
                    temp_file = tempfile.NamedTemporaryFile(suffix='.pdf', delete=False)
                    output_path = temp_file.name
                
                # Save the extracted pages
                output.save(output_path)
                output.close()
                
                logger.debug(f"Extracted pages {start_page}-{end_page} from {pdf_path} to {output_path}")
                return output_path
                
        except Exception as e:
            logger.error(f"Failed to extract pages from {pdf_path}: {str(e)}")
            raise PDFParsingError(f"Page extraction failed: {str(e)}")
    
    @staticmethod
    def get_page_text(pdf_path: str, page_num: int) -> str:
        """
        Extract text from a specific page.
        
        Args:
            pdf_path: Path to PDF file
            page_num: Page number (1-based)
            
        Returns:
            Text content of the page
        """
        try:
            with fitz.open(str(pdf_path)) as pdf:
                if page_num < 1 or page_num > len(pdf):
                    raise ValueError(f"Invalid page number {page_num}")
                    
                page = pdf[page_num - 1]  # Convert to 0-based
                return page.get_text()
                
        except Exception as e:
            logger.error(f"Failed to extract text from page {page_num}: {str(e)}")
            return ""
    
    @staticmethod
    def merge_pdfs(pdf_paths: List[str], output_path: str) -> None:
        """
        Merge multiple PDFs into one.
        
        Args:
            pdf_paths: List of PDF file paths to merge
            output_path: Path for the merged PDF
        """
        try:
            output = fitz.open()
            
            for pdf_path in pdf_paths:
                with fitz.open(str(pdf_path)) as pdf:
                    output.insert_pdf(pdf)
            
            output.save(output_path)
            output.close()
            
            logger.info(f"Merged {len(pdf_paths)} PDFs into {output_path}")
            
        except Exception as e:
            logger.error(f"Failed to merge PDFs: {str(e)}")
            raise PDFParsingError(f"PDF merge failed: {str(e)}")
    
    @staticmethod
    def validate_pdf(pdf_path: str) -> bool:
        """
        Validate if a file is a valid PDF.
        
        Args:
            pdf_path: Path to the PDF file
            
        Returns:
            True if valid PDF, False otherwise
        """
        try:
            with fitz.open(str(pdf_path)) as pdf:
                # Try to access first page to ensure it's readable
                if len(pdf) > 0:
                    _ = pdf[0]
                return True
        except:
            return False
    
    @staticmethod
    def get_page_metadata(pdf_path: str, page_num: int) -> Dict[str, Any]:
        """
        Get metadata for a specific page.
        
        Args:
            pdf_path: Path to PDF file
            page_num: Page number (1-based)
            
        Returns:
            Dictionary containing page metadata
        """
        try:
            with fitz.open(str(pdf_path)) as pdf:
                if page_num < 1 or page_num > len(pdf):
                    raise ValueError(f"Invalid page number {page_num}")
                
                page = pdf[page_num - 1]
                
                return {
                    "page_number": page_num,
                    "width": page.rect.width,
                    "height": page.rect.height,
                    "rotation": page.rotation,
                    "has_text": len(page.get_text()) > 0,
                    "text_length": len(page.get_text())
                }
                
        except Exception as e:
            logger.error(f"Failed to get page metadata: {str(e)}")
            return {}