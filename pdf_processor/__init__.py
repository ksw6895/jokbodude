"""
PDF Processor - Modular PDF analysis system.
"""

from .core.processor import PDFProcessor
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

__version__ = "2.0.0"
__all__ = [
    "PDFProcessor",
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