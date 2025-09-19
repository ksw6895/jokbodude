"""Top-level namespace for the PDF processor package."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

__version__ = "2.1.0"
__all__ = [
    "PDFProcessor",
    "ProblemSnipper",
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
    "SessionError",
]


def __getattr__(name: str) -> Any:  # pragma: no cover - thin import shim
    if name == "PDFProcessor":
        return import_module("pdf_processor.core.processor").PDFProcessor
    if name == "ProblemSnipper":
        return import_module("pdf_processor.core.problem_snipper").ProblemSnipper
    if name == "MultiAPIManager":
        return import_module("pdf_processor.api.multi_api_manager").MultiAPIManager
    if name in {"get_logger", "setup_file_logging"}:
        module = import_module("pdf_processor.utils.logging")
        return getattr(module, name)
    if name == "ProcessingConfig":
        return import_module("pdf_processor.utils.config").ProcessingConfig
    if name in {
        "PDFProcessorError",
        "APIError",
        "FileUploadError",
        "ContentGenerationError",
        "PDFParsingError",
        "JSONParsingError",
        "ValidationError",
        "ChunkProcessingError",
        "SessionError",
    }:
        module = import_module("pdf_processor.utils.exceptions")
        return getattr(module, name)
    raise AttributeError(name)


if TYPE_CHECKING:  # pragma: no cover - hints only
    from pdf_processor.core.processor import PDFProcessor as _PDFProcessor
    from pdf_processor.core.problem_snipper import ProblemSnipper as _ProblemSnipper
    from pdf_processor.api.multi_api_manager import MultiAPIManager as _MultiAPIManager
    from pdf_processor.utils.logging import get_logger, setup_file_logging
    from pdf_processor.utils.config import ProcessingConfig
    from pdf_processor.utils.exceptions import (
        PDFProcessorError,
        APIError,
        FileUploadError,
        ContentGenerationError,
        PDFParsingError,
        JSONParsingError,
        ValidationError,
        ChunkProcessingError,
        SessionError,
    )
