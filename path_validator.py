"""
Path validation utilities to prevent path traversal attacks
"""
from pathlib import Path
from typing import Union


class PathValidator:
    """Validates file paths to prevent security issues"""
    
    @staticmethod
    def validate_safe_path(base_dir: Union[str, Path], filename: str) -> Path:
        """Ensure filename doesn't escape base directory
        
        Args:
            base_dir: Base directory path
            filename: Filename to validate
            
        Returns:
            Full path if valid
            
        Raises:
            ValueError: If path would escape base directory
        """
        base_dir = Path(base_dir).resolve()
        full_path = (base_dir / filename).resolve()
        
        # Check if the resolved path is within the base directory
        try:
            full_path.relative_to(base_dir)
        except ValueError:
            raise ValueError(f"Invalid filename: {filename} - path traversal detected")
        
        return full_path
    
    @staticmethod
    def validate_pdf_filename(filename: str) -> bool:
        """Validate that filename is a valid PDF filename
        
        Args:
            filename: Filename to validate
            
        Returns:
            True if valid PDF filename
        """
        if not filename:
            return False
        
        # Check for path traversal attempts
        if '..' in filename or '/' in filename or '\\' in filename:
            return False
        
        # Check for valid PDF extension
        if not filename.lower().endswith('.pdf'):
            return False
        
        # Check for reasonable filename length
        if len(filename) > 255:
            return False
        
        return True
    
    @staticmethod
    def sanitize_filename(filename: str) -> str:
        """Sanitize filename to remove potentially dangerous characters
        
        Args:
            filename: Original filename
            
        Returns:
            Sanitized filename
        """
        # Remove path separators and parent directory references
        sanitized = filename.replace('/', '_').replace('\\', '_').replace('..', '_')
        
        # Remove other potentially dangerous characters
        dangerous_chars = ['<', '>', ':', '"', '|', '?', '*', '\0']
        for char in dangerous_chars:
            sanitized = sanitized.replace(char, '_')
        
        # Ensure it ends with .pdf if it's supposed to be a PDF
        if filename.lower().endswith('.pdf') and not sanitized.lower().endswith('.pdf'):
            sanitized += '.pdf'
        
        return sanitized