"""
Thread-safe PDF caching module.
Provides efficient caching of PDF objects to avoid repeated file operations.
"""

import threading
from typing import Dict, Optional, Any
from pathlib import Path
import pymupdf as fitz
import weakref

from ..utils.logging import get_logger

logger = get_logger(__name__)


class PDFCache:
    """Thread-safe cache for PDF objects."""
    
    def __init__(self):
        """Initialize the PDF cache."""
        self._cache: Dict[str, Any] = {}
        self._page_counts: Dict[str, int] = {}
        self._lock = threading.Lock()
        self._finalizer = weakref.finalize(self, self._cleanup_all)
        
    def get_pdf(self, pdf_path: str) -> Any:
        """
        Get a cached PDF object or create a new one.
        
        Args:
            pdf_path: Path to the PDF file
            
        Returns:
            PDF object (fitz.Document)
        """
        pdf_path = str(Path(pdf_path).resolve())
        
        with self._lock:
            if pdf_path not in self._cache:
                logger.debug(f"Opening new PDF: {pdf_path}")
                try:
                    pdf = fitz.open(pdf_path)
                    self._cache[pdf_path] = pdf
                    self._page_counts[pdf_path] = len(pdf)
                except Exception as e:
                    logger.error(f"Failed to open PDF {pdf_path}: {str(e)}")
                    raise
            else:
                logger.debug(f"Using cached PDF: {pdf_path}")
                
            return self._cache[pdf_path]
    
    def get_page_count(self, pdf_path: str) -> int:
        """
        Get cached page count for a PDF.
        
        Args:
            pdf_path: Path to the PDF file
            
        Returns:
            Number of pages in the PDF
        """
        pdf_path = str(Path(pdf_path).resolve())
        
        with self._lock:
            if pdf_path not in self._page_counts:
                # If not in cache, open PDF to get page count
                pdf = self.get_pdf(pdf_path)
                self._page_counts[pdf_path] = len(pdf)
                
            return self._page_counts[pdf_path]
    
    def close_pdf(self, pdf_path: str) -> None:
        """
        Close and remove a PDF from cache.
        
        Args:
            pdf_path: Path to the PDF file
        """
        pdf_path = str(Path(pdf_path).resolve())
        
        with self._lock:
            if pdf_path in self._cache:
                try:
                    self._cache[pdf_path].close()
                    logger.debug(f"Closed PDF: {pdf_path}")
                except Exception as e:
                    logger.warning(f"Error closing PDF {pdf_path}: {str(e)}")
                finally:
                    del self._cache[pdf_path]
                    self._page_counts.pop(pdf_path, None)
    
    def clear(self) -> None:
        """Clear all cached PDFs."""
        with self._lock:
            self._cleanup_all()
            self._cache.clear()
            self._page_counts.clear()
    
    def _cleanup_all(self) -> None:
        """Internal method to close all PDFs."""
        for pdf_path, pdf in list(self._cache.items()):
            try:
                pdf.close()
                logger.debug(f"Cleaned up PDF: {pdf_path}")
            except Exception as e:
                logger.warning(f"Error during cleanup of {pdf_path}: {str(e)}")
    
    def __contains__(self, pdf_path: str) -> bool:
        """Check if a PDF is in cache."""
        pdf_path = str(Path(pdf_path).resolve())
        with self._lock:
            return pdf_path in self._cache
    
    def __len__(self) -> int:
        """Get number of cached PDFs."""
        with self._lock:
            return len(self._cache)
    
    def get_cache_info(self) -> Dict[str, Any]:
        """Get cache statistics."""
        with self._lock:
            return {
                "cached_pdfs": len(self._cache),
                "total_pages": sum(self._page_counts.values()),
                "pdf_paths": list(self._cache.keys())
            }


# Global cache instance
_global_cache: Optional[PDFCache] = None
_cache_lock = threading.Lock()


def get_global_cache() -> PDFCache:
    """
    Get the global PDF cache instance.
    
    Returns:
        Global PDFCache instance
    """
    global _global_cache
    
    with _cache_lock:
        if _global_cache is None:
            _global_cache = PDFCache()
            logger.info("Created global PDF cache")
            
    return _global_cache


def clear_global_cache() -> None:
    """Clear the global PDF cache."""
    global _global_cache
    
    with _cache_lock:
        if _global_cache is not None:
            _global_cache.clear()
            logger.info("Cleared global PDF cache")