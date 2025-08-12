"""PDF Processor Module - Modular PDF processing system for JokboDude"""

from pdf_processor.core.processor import PDFProcessor
from pdf_processor.utils.session_manager import SessionManager
from pdf_processor.utils.file_manager import FileManagerUtil
from pdf_processor.api.gemini_client import GeminiClient

__version__ = "2.0.0"
__all__ = [
    "PDFProcessor",
    "SessionManager", 
    "FileManagerUtil",
    "GeminiClient"
]