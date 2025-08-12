"""Gemini API client for content generation"""

import time
from typing import Any, Dict, Optional
import google.generativeai as genai
from processing_config import ProcessingConfig
from pdf_processor.utils.retry_handler import RetryHandler


class GeminiClient:
    """Client for interacting with Gemini API"""
    
    def __init__(self, model):
        """Initialize Gemini client with model
        
        Args:
            model: Configured Gemini model instance
        """
        self.model = model
        self.retry_handler = RetryHandler()
    
    def generate_content_with_retry(
        self, 
        content: Any,
        max_retries: int = ProcessingConfig.MAX_RETRIES,
        backoff_factor: float = ProcessingConfig.BACKOFF_FACTOR
    ) -> Any:
        """Generate content with retry logic
        
        Args:
            content: Content to send to Gemini
            max_retries: Maximum retry attempts
            backoff_factor: Exponential backoff factor
            
        Returns:
            Response from Gemini API
        """
        return self.retry_handler.execute_with_retry(
            self.model.generate_content,
            content,
            max_retries,
            backoff_factor
        )
    
    def upload_file(self, file_path: str, display_name: Optional[str] = None):
        """Upload file to Gemini API
        
        Args:
            file_path: Path to file to upload
            display_name: Optional display name
            
        Returns:
            Uploaded file object
        """
        from pathlib import Path
        
        if display_name is None:
            display_name = Path(file_path).name
        
        print(f"파일 업로드 중: {display_name}")
        uploaded_file = genai.upload_file(file_path, display_name=display_name)
        
        # Wait for processing
        while uploaded_file.state.name == "PROCESSING":
            time.sleep(1)
            uploaded_file = genai.get_file(uploaded_file.name)
        
        if uploaded_file.state.name == "FAILED":
            raise ValueError(f"파일 업로드 실패: {display_name}")
        
        print(f"파일 업로드 완료: {display_name}")
        return uploaded_file
    
    @staticmethod
    def delete_file(file):
        """Delete file from Gemini
        
        Args:
            file: File object to delete
        """
        try:
            genai.delete_file(file.name)
            print(f"  파일 삭제됨: {file.display_name}")
        except Exception:
            print(f"  파일 삭제 실패 (이미 삭제됨): {file.display_name}")