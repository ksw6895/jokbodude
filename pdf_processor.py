"""
PDF Processor - Backward compatibility wrapper

This file maintains backward compatibility with existing code while using the new modular structure.
All functionality has been moved to the pdf_processor package.
"""

# Import the main processor from the new modular structure
from pdf_processor.core.processor import PDFProcessor
from pdf_processor.core.multi_api_processor import MultiAPIProcessor, process_api_chunks_multiprocess

# Export for backward compatibility
__all__ = [
    'PDFProcessor',
    'MultiAPIProcessor',
    'process_api_chunks_multiprocess'
]

# For direct imports (e.g., from pdf_processor import PDFProcessor)
# The imports above already handle this

# Add a deprecation notice for future reference
import warnings

def _show_migration_notice():
    """Show migration notice for developers"""
    warnings.filterwarnings('once')
    warnings.warn(
        "Direct import from pdf_processor.py is deprecated. "
        "Please import from pdf_processor.core.processor instead: "
        "from pdf_processor.core.processor import PDFProcessor",
        DeprecationWarning,
        stacklevel=2
    )

# Only show notice in development mode (can be controlled via environment variable)
import os
if os.getenv('SHOW_DEPRECATION_WARNINGS', 'false').lower() == 'true':
    _show_migration_notice()