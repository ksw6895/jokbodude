"""
Configuration utilities for PDF processor.
"""

import os
from typing import Optional

from ..utils.logging import get_logger

logger = get_logger(__name__)


class ProcessingConfig:
    """Configuration constants for processing."""
    
    # Chunk processing
    DEFAULT_CHUNK_SIZE = int(os.environ.get('MAX_PAGES_PER_CHUNK', '30'))
    
    # Parallel processing
    DEFAULT_THREAD_WORKERS = 3
    
    # API retry
    MAX_RETRIES = 3
    BACKOFF_FACTOR = 2
    
    # Connection filtering
    MIN_RELEVANCE_SCORE = 50
    MAX_CONNECTIONS_PER_QUESTION = 2
    
    @classmethod
    def get_chunk_size(cls) -> int:
        """Get configured chunk size."""
        return cls.DEFAULT_CHUNK_SIZE
    
    @classmethod
    def configure_chunk_size(cls, size: int) -> None:
        """Update chunk size configuration."""
        cls.DEFAULT_CHUNK_SIZE = size
        logger.info(f"Updated chunk size to {size} pages")
