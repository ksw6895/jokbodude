"""
Centralized configuration for PDF processing system
"""
import os


class ProcessingConfig:
    """Configuration constants for PDF processing"""
    
    # Retry and timeout settings
    MAX_RETRIES = 3
    BACKOFF_FACTOR = 2
    
    # Chunk processing settings
    DEFAULT_CHUNK_SIZE = int(os.getenv('MAX_PAGES_PER_CHUNK', '40'))
    
    # Parallel processing settings
    DEFAULT_THREAD_WORKERS = 3
    
    # API settings
    API_COOLDOWN_MINUTES = 10
    API_CONSECUTIVE_FAILURE_THRESHOLD = 3
    
    # File size limits
    MAX_PDF_SIZE_MB = 100
    MAX_PROCESSING_TIME_SECONDS = 300
    MAX_PAGES_PER_PDF = 1000
    
    # Response validation
    MIN_RESPONSE_LENGTH = 10
    
    # Debug settings
    DEBUG_OUTPUT_ENABLED = True
    
    # Session settings
    DEFAULT_SESSION_KEEP_DAYS = 1
    
    # Token limits (for response truncation warnings)
    MAX_TOKEN_FINISH_REASONS = ['MAX_TOKENS', '2']