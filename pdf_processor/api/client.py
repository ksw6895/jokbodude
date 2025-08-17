"""
Gemini API client for handling API interactions.
Provides a clean interface for API operations with retry logic and error handling.
"""

import time
from typing import Any, Optional, List, Dict
from datetime import datetime
import google.generativeai as genai
from pathlib import Path

from ..utils.exceptions import APIError, FileUploadError, ContentGenerationError
from ..utils.logging import get_logger

logger = get_logger(__name__)


class GeminiAPIClient:
    """Client for interacting with Google Gemini API."""
    
    def __init__(self, model, api_key: Optional[str] = None):
        """
        Initialize the Gemini API client.
        
        Args:
            model: The Gemini model instance to use
            api_key: Optional API key (if not provided, uses environment variable)
        """
        self.model = model
        self.api_key = api_key
        self._configure_api(api_key)
        
    def _configure_api(self, api_key: Optional[str] = None):
        """Configure the API with the provided or environment API key."""
        if api_key:
            genai.configure(api_key=api_key)

    # A permissive default schema covering both modes to stabilize JSON output
    # without over-constraining generations. Callers may override per-request.
    DEFAULT_RESPONSE_SCHEMA: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "jokbo_pages": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "jokbo_page": {"type": ["integer", "string"]},
                        "questions": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "jokbo_filename": {"type": ["string", "null"]},
                                    "jokbo_page": {"type": ["integer", "string", "null"]},
                                    "jokbo_end_page": {"type": ["integer", "string", "null"]},
                                    "question_number": {"type": ["string", "integer", "null"]},
                                    "question_numbers_on_page": {"type": ["array", "null"]},
                                    "question_text": {"type": ["string", "null"]},
                                    "answer": {"type": ["string", "null"]},
                                    "explanation": {"type": ["string", "null"]},
                                    "wrong_answer_explanations": {"type": ["object", "null"]},
                                    "relevance_score": {"type": ["integer", "string", "null"]},
                                    "relevance_reason": {"type": ["string", "null"]},
                                    "related_lesson_slides": {
                                        "type": ["array", "null"],
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "lesson_filename": {"type": ["string", "null"]},
                                                "lesson_page": {"type": ["integer", "string", "null"]},
                                                "relevance_score": {"type": ["integer", "string", "null"]},
                                                "relevance_reason": {"type": ["string", "null"]},
                                            },
                                            "additionalProperties": True,
                                        },
                                    },
                                },
                                "additionalProperties": True,
                            },
                        },
                    },
                    "additionalProperties": True,
                },
            },
            "related_slides": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "lesson_page": {"type": ["integer", "string"]},
                        "related_jokbo_questions": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "jokbo_filename": {"type": ["string", "null"]},
                                    "jokbo_page": {"type": ["integer", "string", "null"]},
                                    "jokbo_end_page": {"type": ["integer", "string", "null"]},
                                    "question_number": {"type": ["string", "integer", "null"]},
                                    "question_numbers_on_page": {"type": ["array", "null"]},
                                    "question_text": {"type": ["string", "null"]},
                                    "answer": {"type": ["string", "null"]},
                                    "explanation": {"type": ["string", "null"]},
                                    "wrong_answer_explanations": {"type": ["object", "null"]},
                                    "relevance_score": {"type": ["integer", "string", "null"]},
                                    "relevance_reason": {"type": ["string", "null"]},
                                },
                                "additionalProperties": True,
                            },
                        },
                        "importance_score": {"type": ["integer", "string", "null"]},
                        "key_concepts": {"type": ["array", "null"]},
                    },
                    "additionalProperties": True,
                },
            },
        },
        "additionalProperties": True,
    }
            
    def upload_file(self, file_path: str, display_name: Optional[str] = None, 
                   mime_type: str = "application/pdf") -> Any:
        """
        Upload a file to Gemini API.
        
        Args:
            file_path: Path to the file to upload
            display_name: Optional display name for the file
            mime_type: MIME type of the file
            
        Returns:
            Uploaded file object
            
        Raises:
            FileUploadError: If file upload fails
        """
        if display_name is None:
            display_name = Path(file_path).name
            
        try:
            # Ensure correct API key context for this client
            self._configure_api(self.api_key)
            logger.info(f"Uploading file: {display_name}")
            uploaded_file = genai.upload_file(
                path=file_path,
                display_name=display_name,
                mime_type=mime_type
            )
            
            # Wait for file to be processed
            while uploaded_file.state.name == "PROCESSING":
                logger.debug(f"Processing {display_name}...")
                time.sleep(2)
                uploaded_file = genai.get_file(uploaded_file.name)
            
            if uploaded_file.state.name == "FAILED":
                raise FileUploadError(f"File processing failed: {display_name}")
                
            logger.info(f"Successfully uploaded: {display_name}")
            return uploaded_file
            
        except Exception as e:
            logger.error(f"Failed to upload file {display_name}: {str(e)}")
            raise FileUploadError(f"Failed to upload {display_name}: {str(e)}")
    
    def delete_file(self, file: Any, max_retries: int = 3) -> bool:
        """
        Delete a file from Gemini API with retry logic.
        
        Args:
            file: File object to delete
            max_retries: Maximum number of retry attempts
            
        Returns:
            True if deletion successful, False otherwise
        """
        for attempt in range(max_retries):
            try:
                # Ensure correct API key context for this client
                self._configure_api(self.api_key)
                genai.delete_file(file.name)
                logger.info(f"Deleted file: {file.display_name}")
                return True
            except Exception as e:
                logger.warning(f"Failed to delete {file.display_name} (attempt {attempt + 1}/{max_retries}): {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)  # Exponential backoff
        return False
    
    def list_files(self) -> List[Any]:
        """
        List all uploaded files.
        
        Returns:
            List of uploaded files
        """
        try:
            # Ensure correct API key context for this client
            self._configure_api(self.api_key)
            return list(genai.list_files())
        except Exception as e:
            logger.error(f"Failed to list files: {str(e)}")
            return []
    
    def generate_content(
        self,
        content: Any,
        max_retries: int = 3,
        backoff_factor: int = 2,
        *,
        response_mime_type: str = "application/json",
        response_schema: Optional[Dict[str, Any]] = None,
        max_output_tokens: Optional[int] = None,
    ) -> Any:
        """
        Generate content with retry logic and error handling.
        
        Args:
            content: Content to send to the model
            max_retries: Maximum number of retry attempts
            backoff_factor: Exponential backoff factor
            
        Returns:
            Generated response
            
        Raises:
            ContentGenerationError: If content generation fails
        """
        for attempt in range(max_retries):
            try:
                # Ensure correct API key context for this client before generation
                self._configure_api(self.api_key)

                # Enforce JSON mode and allow optional schema/limits per-call
                gen_config: Dict[str, Any] = {"response_mime_type": response_mime_type}
                # Apply caller-provided schema if given; otherwise use a permissive default
                schema_to_use = response_schema or self.DEFAULT_RESPONSE_SCHEMA
                if schema_to_use:
                    gen_config["response_schema"] = schema_to_use
                if isinstance(max_output_tokens, int) and max_output_tokens > 0:
                    gen_config["max_output_tokens"] = max_output_tokens

                response = self.model.generate_content(
                    content,
                    generation_config=gen_config or None,
                )
                
                # Check for empty response
                if not response.text or len(response.text) == 0:
                    logger.warning(f"Empty response received (attempt {attempt + 1}/{max_retries})")
                    if attempt < max_retries - 1:
                        wait_time = backoff_factor ** attempt
                        logger.info(f"Retrying in {wait_time} seconds...")
                        time.sleep(wait_time)
                        continue
                    else:
                        raise ContentGenerationError("Empty response from API")
                
                # Check finish reason
                if response.candidates:
                    finish_reason = str(response.candidates[0].finish_reason)
                    if 'MAX_TOKENS' in finish_reason or finish_reason == '2':
                        logger.warning(f"Response truncated due to token limit (length: {len(response.text)})")
                    elif 'SAFETY' in finish_reason or finish_reason == '3':
                        logger.warning("Response blocked due to safety concerns")
                        if attempt < max_retries - 1:
                            wait_time = backoff_factor ** attempt
                            time.sleep(wait_time)
                            continue
                
                return response
                
            except Exception as e:
                logger.error(f"Content generation failed (attempt {attempt + 1}/{max_retries}): {str(e)}")
                if attempt < max_retries - 1:
                    wait_time = backoff_factor ** attempt
                    logger.info(f"Retrying in {wait_time} seconds...")
                    time.sleep(wait_time)
                else:
                    raise ContentGenerationError(f"Failed to generate content after {max_retries} attempts: {str(e)}")
        
        raise ContentGenerationError("Maximum retries exceeded")
    
    def get_file(self, file_name: str) -> Optional[Any]:
        """
        Get a file by name.
        
        Args:
            file_name: Name of the file to retrieve
            
        Returns:
            File object if found, None otherwise
        """
        try:
            # Ensure correct API key context for this client
            self._configure_api(self.api_key)
            return genai.get_file(file_name)
        except Exception as e:
            logger.error(f"Failed to get file {file_name}: {str(e)}")
            return None
