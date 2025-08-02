"""
Custom exceptions for the PDF processor module.
"""


class PDFProcessorError(Exception):
    """Base exception for PDF processor errors."""
    pass


class APIError(PDFProcessorError):
    """Base exception for API-related errors."""
    pass


class FileUploadError(APIError):
    """Raised when file upload fails."""
    pass


class ContentGenerationError(APIError):
    """Raised when content generation fails."""
    pass


class FileNotFoundError(PDFProcessorError):
    """Raised when a required file is not found."""
    pass


class PDFParsingError(PDFProcessorError):
    """Raised when PDF parsing fails."""
    pass


class JSONParsingError(PDFProcessorError):
    """Raised when JSON parsing fails."""
    pass


class ValidationError(PDFProcessorError):
    """Raised when validation fails."""
    pass


class ChunkProcessingError(PDFProcessorError):
    """Raised when chunk processing fails."""
    pass


class SessionError(PDFProcessorError):
    """Raised when session-related operations fail."""
    pass