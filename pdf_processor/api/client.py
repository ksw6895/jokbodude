"""
Gemini API client for handling API interactions.
Provides a clean interface for API operations with retry logic and error handling.

Important:
- The google-generativeai SDK uses a global configuration (`genai.configure`).
- In multi-threaded, multi-key scenarios this can cause cross-key 403/400 issues
  if different threads reconfigure the global key concurrently.
- To ensure correctness, we guard each configure+API call with a process-wide lock.
  This may reduce peak parallelism but avoids permission errors.
"""

import time
import threading
from typing import Any, Optional, List, Dict
from datetime import datetime
import google.generativeai as genai
from pathlib import Path

from ..utils.exceptions import APIError, FileUploadError, ContentGenerationError
from ..utils.logging import get_logger

logger = get_logger(__name__)


class GeminiAPIClient:
    """Client for interacting with Google Gemini API."""
    
    # Global lock to prevent cross-thread key reconfiguration during SDK calls
    CONFIG_LOCK = threading.RLock()

    def __init__(self, model, api_key: Optional[str] = None, *, key_index: Optional[int] = None):
        """
        Initialize the Gemini API client.
        
        Args:
            model: The Gemini model instance to use
            api_key: Optional API key (if not provided, uses environment variable)
        """
        self.model = model
        self.api_key = api_key
        self.key_index = key_index
        self._configure_api(api_key)
        
    def _configure_api(self, api_key: Optional[str] = None):
        """Configure the API with the provided or environment API key."""
        if api_key:
            genai.configure(api_key=api_key)

    def _key_tag(self) -> str:
        """Return a safe identifier for the bound API key for logs.
        Example: k2:***abcd (index 2, last 4 chars)
        """
        try:
            suffix = (self.api_key or "")[-4:] if self.api_key else "????"
            idx = self.key_index if self.key_index is not None else "?"
            return f"k{idx}:***{suffix}"
        except Exception:
            return "k?:***????"

    # A permissive default schema covering both modes to stabilize JSON output
    # without over-constraining generations. Callers may override per-request.
    # Use a minimal permissive schema to avoid SDK schema conversion issues.
    # Many versions of the SDK expect a "Schema" proto-like structure rather than
    # full JSON Schema (which supports union types via lists). Keeping this minimal
    # enforces object-only JSON without overconstraining nested properties.
    DEFAULT_RESPONSE_SCHEMA: Dict[str, Any] = {"type": "object"}
            
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
            logger.info(f"Uploading file: {display_name} [key={self._key_tag()}]")
            with self.CONFIG_LOCK:
                self._configure_api(self.api_key)
                uploaded_file = genai.upload_file(
                    path=file_path,
                    display_name=display_name,
                    mime_type=mime_type
                )
            
            # Wait for file to be processed
            while uploaded_file.state.name == "PROCESSING":
                logger.debug(f"Processing {display_name}... [key={self._key_tag()}]")
                time.sleep(2)
                with self.CONFIG_LOCK:
                    self._configure_api(self.api_key)
                    uploaded_file = genai.get_file(uploaded_file.name)
            
            if uploaded_file.state.name == "FAILED":
                raise FileUploadError(f"File processing failed: {display_name}")
                
            logger.info(f"Successfully uploaded: {display_name} [key={self._key_tag()}]")
            return uploaded_file
            
        except Exception as e:
            logger.error(f"Failed to upload file {display_name} [key={self._key_tag()}]: {str(e)}")
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
                with self.CONFIG_LOCK:
                    self._configure_api(self.api_key)
                    genai.delete_file(file.name)
                logger.info(f"Deleted file: {file.display_name} [key={self._key_tag()}]")
                return True
            except Exception as e:
                logger.warning(f"Failed to delete {file.display_name} (attempt {attempt + 1}/{max_retries}) [key={self._key_tag()}]: {str(e)}")
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
            with self.CONFIG_LOCK:
                self._configure_api(self.api_key)
                files = list(genai.list_files())
            return files
        except Exception as e:
            logger.error(f"Failed to list files [key={self._key_tag()}]: {str(e)}")
            return []
    
    def generate_content(
        self,
        content: Any,
        max_retries: int = 3,
        backoff_factor: int = 2,
        *,
        response_mime_type: str = "application/json",
        # NOTE: response_schema intentionally ignored for stability until validated
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
                # Guard the configure + call with a global lock to avoid cross-key races
                with self.CONFIG_LOCK:
                    self._configure_api(self.api_key)

                # Enforce JSON mode and allow optional schema/limits per-call
                gen_config: Dict[str, Any] = {}
                # Enforce JSON MIME type only; avoid response_schema to prevent 400s
                if response_mime_type:
                    gen_config["response_mime_type"] = response_mime_type
                if isinstance(max_output_tokens, int) and max_output_tokens > 0:
                    gen_config["max_output_tokens"] = max_output_tokens

                response = self.model.generate_content(
                    content,
                    generation_config=gen_config or None,
                )
                
                # Blocked prompt handling (avoid touching response.text/parts when blocked)
                try:
                    pf = getattr(response, "prompt_feedback", None)
                    block_reason = getattr(pf, "block_reason", None) if pf is not None else None
                except Exception:
                    pf, block_reason = None, None
                if block_reason:
                    msg = f"Prompt blocked: block_reason={block_reason}"
                    logger.warning(f"{msg} [key={self._key_tag()}]")
                    if attempt < max_retries - 1:
                        wait_time = backoff_factor ** attempt
                        logger.info(f"Retrying in {wait_time} seconds... [key={self._key_tag()}]")
                        time.sleep(wait_time)
                        continue
                    raise ContentGenerationError(msg)

                # Guard against empty candidates before accessing response.text
                try:
                    cand_count = len(getattr(response, "candidates", []) or [])
                except Exception:
                    cand_count = 0

                if cand_count == 0:
                    logger.warning(f"No candidates in response (attempt {attempt + 1}/{max_retries}) [key={self._key_tag()}]")
                    if attempt < max_retries - 1:
                        wait_time = backoff_factor ** attempt
                        logger.info(f"Retrying in {wait_time} seconds... [key={self._key_tag()}]")
                        time.sleep(wait_time)
                        continue
                    raise ContentGenerationError("Empty candidates from API")

                # Check for empty text safely now that candidates exist
                safe_text = None
                try:
                    safe_text = response.text
                except Exception as _e:
                    # Fallback: try to reconstruct minimal text from first candidate parts
                    try:
                        c0 = response.candidates[0]
                        parts = getattr(getattr(c0, "content", None), "parts", None) or []
                        texts = []
                        for p in parts:
                            try:
                                t = getattr(p, "text", None)
                                if t:
                                    texts.append(t)
                            except Exception:
                                pass
                        safe_text = "\n".join(texts) if texts else None
                    except Exception:
                        safe_text = None

                if not safe_text or len(safe_text) == 0:
                    logger.warning(f"Empty response text (attempt {attempt + 1}/{max_retries}) [key={self._key_tag()}]")
                    if attempt < max_retries - 1:
                        wait_time = backoff_factor ** attempt
                        logger.info(f"Retrying in {wait_time} seconds... [key={self._key_tag()}]")
                        time.sleep(wait_time)
                        continue
                    raise ContentGenerationError("Empty response from API")
                
                # Check finish reason
                if response.candidates:
                    finish_reason = str(response.candidates[0].finish_reason)
                    if 'MAX_TOKENS' in finish_reason or finish_reason == '2':
                        logger.warning(f"Response truncated due to token limit (length: {len(response.text)}) [key={self._key_tag()}]")
                    elif 'SAFETY' in finish_reason or finish_reason == '3':
                        logger.warning(f"Response blocked due to safety concerns [key={self._key_tag()}]")
                        if attempt < max_retries - 1:
                            wait_time = backoff_factor ** attempt
                            time.sleep(wait_time)
                            continue
                
                return response
                
            except Exception as e:
                logger.error(f"Content generation failed (attempt {attempt + 1}/{max_retries}) [key={self._key_tag()}]: {str(e)}")
                if attempt < max_retries - 1:
                    wait_time = backoff_factor ** attempt
                    logger.info(f"Retrying in {wait_time} seconds... [key={self._key_tag()}]")
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
            with self.CONFIG_LOCK:
                self._configure_api(self.api_key)
                return genai.get_file(file_name)
        except Exception as e:
            logger.error(f"Failed to get file {file_name} [key={self._key_tag()}]: {str(e)}")
            return None
