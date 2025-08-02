"""
PDF Processor - Modular PDF analysis system with multi-API support.
"""

from .core.processor import PDFProcessor
from .api.multi_api_manager import MultiAPIManager
from .utils.logging import get_logger, setup_file_logging
from .utils.config import ProcessingConfig
from .utils.exceptions import (
    PDFProcessorError,
    APIError,
    FileUploadError,
    ContentGenerationError,
    PDFParsingError,
    JSONParsingError,
    ValidationError,
    ChunkProcessingError,
    SessionError
)

__version__ = "2.1.0"
__all__ = [
    "PDFProcessor",
    "MultiAPIManager",
    "get_logger",
    "setup_file_logging",
    "ProcessingConfig",
    "PDFProcessorError",
    "APIError",
    "FileUploadError",
    "ContentGenerationError",
    "PDFParsingError",
    "JSONParsingError",
    "ValidationError",
    "ChunkProcessingError",
    "SessionError"
]