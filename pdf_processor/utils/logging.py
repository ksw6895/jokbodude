"""
Centralized logging configuration for PDF processor.
"""

import logging
from pathlib import Path
from datetime import datetime
from typing import Optional


def get_logger(name: str, level: Optional[int] = None) -> logging.Logger:
    """
    Get a configured logger instance.
    
    Args:
        name: Logger name (usually __name__)
        level: Optional logging level
        
    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)
    
    # Only configure if not already configured
    if not logger.handlers:
        # Set level
        if level is None:
            level = logging.INFO
        logger.setLevel(level)
        
        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        
        # Format
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        console_handler.setFormatter(formatter)
        
        logger.addHandler(console_handler)
        
        # Prevent propagation to root logger
        logger.propagate = False
    
    return logger


def setup_file_logging(log_dir: str = "logs", log_file: Optional[str] = None) -> None:
    """
    Set up file logging for all loggers.
    
    Args:
        log_dir: Directory to store log files
        log_file: Optional specific log file name
    """
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    
    if log_file is None:
        log_file = f"pdf_processor_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    
    file_path = log_path / log_file
    
    # Configure root logger
    root_logger = logging.getLogger()
    
    # File handler
    file_handler = logging.FileHandler(file_path, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    
    # Detailed format for file
    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(file_formatter)
    
    root_logger.addHandler(file_handler)