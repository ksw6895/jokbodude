"""Thread-safe PDF caching functionality"""

import threading
from typing import Dict, Optional
import pymupdf as fitz


class PDFCache:
    """Thread-safe cache for PDF documents"""
    
    def __init__(self):
        """Initialize PDF cache with thread lock"""
        self._cache: Dict[str, fitz.Document] = {}
        self._lock = threading.Lock()
    
    def get_pdf(self, pdf_path: str) -> fitz.Document:
        """Get PDF document from cache or open new one
        
        Args:
            pdf_path: Path to PDF file
            
        Returns:
            PyMuPDF document object
        """
        with self._lock:
            if pdf_path not in self._cache:
                self._cache[pdf_path] = fitz.open(pdf_path)
            return self._cache[pdf_path]
    
    def close_pdf(self, pdf_path: str):
        """Close and remove PDF from cache
        
        Args:
            pdf_path: Path to PDF file
        """
        with self._lock:
            if pdf_path in self._cache:
                self._cache[pdf_path].close()
                del self._cache[pdf_path]
    
    def close_all(self):
        """Close all cached PDFs"""
        with self._lock:
            for doc in self._cache.values():
                doc.close()
            self._cache.clear()
    
    def __del__(self):
        """Clean up all PDFs when cache is destroyed"""
        self.close_all()