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


class ContentQualityError(PDFProcessorError):
    """Raised when content generation succeeds but output content is invalid/low-quality."""
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


class CancelledError(PDFProcessorError):
    """Raised when a job is cancelled by the user.

    This exception is used to cooperatively abort long-running analysis loops
    when a cancellation flag is detected in Redis. Celery tasks can catch this
    exception and mark the task as REVOKED without treating it as a failure.
    """
    pass
