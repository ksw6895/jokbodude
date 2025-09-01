"""
Gemini API client for handling API interactions.
Provides a clean interface for API operations with retry logic and error handling.

Concurrency note:
- We now avoid the process-global `genai.configure` and do NOT use a global lock.
- Each instance creates its own `genai.Client(api_key=...)` and uses client-scoped
  services (files/models) so calls can run truly concurrently, even for the same key
  when `GEMINI_PER_KEY_CONCURRENCY` > 1.
"""

import time
from typing import Any, Optional, List, Dict
from datetime import datetime
from google import genai  # google-genai unified SDK
from google.genai import types as genai_types
from pathlib import Path

from ..utils.exceptions import APIError, FileUploadError, ContentGenerationError
from ..utils.logging import get_logger

logger = get_logger(__name__)


class GeminiAPIClient:
    """Client for interacting with Google Gemini API."""
    
    def __init__(self, model, api_key: Optional[str] = None, *, key_index: Optional[int] = None):
        """
        Initialize the Gemini API client.
        
        Args:
            model: The Gemini model instance to use
            api_key: Optional API key (if not provided, uses environment variable)
        """
        # Extract model configuration (name, base generation config, safety)
        self.model_name = None
        self.base_generation_config: Dict[str, Any] | None = None
        self.safety_settings: Any = None
        try:
            if isinstance(model, dict):
                self.model_name = (
                    model.get("model_name")
                    or model.get("_model_name")
                    or "gemini-2.5-flash"
                )
                self.base_generation_config = (
                    model.get("generation_config")
                    or model.get("_generation_config")
                    or None
                )
                self.safety_settings = (
                    model.get("safety_settings")
                    or model.get("_safety_settings")
                    or None
                )
            else:
                self.model_name = (
                    getattr(model, "model_name", None)
                    or getattr(model, "_model_name", None)
                    or "gemini-2.5-flash"
                )
                self.base_generation_config = (
                    getattr(model, "generation_config", None)
                    or getattr(model, "_generation_config", None)
                )
                self.safety_settings = (
                    getattr(model, "safety_settings", None)
                    or getattr(model, "_safety_settings", None)
                )
        except Exception:
            self.model_name = self.model_name or "gemini-2.5-flash"
            self.base_generation_config = self.base_generation_config or None
            self.safety_settings = self.safety_settings or None
        # Bind API key if provided; in single-key mode default to configured key
        if api_key is None:
            try:
                from config import API_KEY as _DEFAULT_KEY  # local import to avoid cycles
            except Exception:
                _DEFAULT_KEY = None
            self.api_key = _DEFAULT_KEY
        else:
            self.api_key = api_key
        self.key_index = key_index
        # Create a per-instance client bound to this API key for thread-safe ops
        try:
            self._client = genai.Client(api_key=self.api_key)
        except Exception as e:
            # Log precise failure reason for easier debugging and keep object usable
            logger.error(f"Failed to initialize genai.Client for key {self._key_tag()}: {e}")
            self._client = None
        
    def _configure_api(self, api_key: Optional[str] = None):
        """Deprecated: no-op (kept for backward compatibility)."""
        return None

    def _key_tag(self) -> str:
        """Return a safe identifier for the bound API key for logs.
        Example: k2:***abcd (index 2, last 4 chars)
        """
        try:
            suffix = (self.api_key or "")[-4:] if self.api_key else "????"
            idx = self.key_index if self.key_index is not None else 0
            # Present indices as 1-based for readability: k1, k2, ...
            tag_idx = (idx + 1) if isinstance(idx, int) else idx
            return f"k{tag_idx}:***{suffix}"
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
            logger.info(f"Uploading file: {display_name} [key={self._key_tag()}]")
            if self._client is None:
                raise FileUploadError("Client not initialized")
            # google-genai (new SDK) upload signature: files.upload(file=..., config=UploadFileConfig(...))
            upload_cfg = genai_types.UploadFileConfig(
                display_name=display_name,
                mime_type=mime_type,
            )
            uploaded_file = self._client.files.upload(
                file=file_path,
                config=upload_cfg,
            )
            
            # Wait until file is ACTIVE per new SDK semantics
            while True:
                try:
                    state = getattr(uploaded_file, "state", None)
                    state_name = getattr(state, "name", None) if state is not None else None
                except Exception:
                    state_name = None
                if state_name == "ACTIVE":
                    break
                if state_name == "FAILED":
                    raise FileUploadError(f"File processing failed: {display_name}")
                logger.debug(f"Processing {display_name} (state={state_name or 'UNKNOWN'})... [key={self._key_tag()}]")
                time.sleep(2)
                uploaded_file = self._client.files.get(name=uploaded_file.name)
                
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
                if self._client is None:
                    raise FileUploadError("Client not initialized")
                self._client.files.delete(name=file.name)
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
            if self._client is None:
                raise FileUploadError("Client not initialized")
            files = list(self._client.files.list())
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
        # Allow up to 2 extra tries specifically for prompt blocks (user request)
        block_bonus_attempts = 2
        total_attempts = max(1, int(max_retries)) + block_bonus_attempts
        for attempt in range(total_attempts):
            try:
                # Enforce JSON mode and allow optional schema/limits per-call
                gen_config: Dict[str, Any] = {}
                if response_mime_type:
                    gen_config["response_mime_type"] = response_mime_type
                if isinstance(max_output_tokens, int) and max_output_tokens > 0:
                    gen_config["max_output_tokens"] = max_output_tokens

                # Merge base generation config with per-call overrides
                final_gen_cfg: Dict[str, Any] = {}
                try:
                    if isinstance(self.base_generation_config, dict):
                        final_gen_cfg.update(self.base_generation_config)
                except Exception:
                    pass
                for k, v in (gen_config or {}).items():
                    final_gen_cfg[k] = v

                if self._client is None:
                    raise ContentGenerationError("Client not initialized")
                model_name = self.model_name or "gemini-2.5-flash"
                # New SDK prefers `config=`; add compatibility fallback to `generation_config=`
                gen_kwargs: Dict[str, Any] = {
                    "model": model_name,
                    "contents": content,
                }
                if final_gen_cfg:
                    gen_kwargs["config"] = final_gen_cfg
                if self.safety_settings:
                    gen_kwargs["safety_settings"] = self.safety_settings

                try:
                    response = self._client.models.generate_content(**gen_kwargs)
                except TypeError as te:
                    msg = str(te).lower()
                    # If this SDK doesn't support `config`, try legacy `generation_config`
                    if "unexpected keyword" in msg and "config" in msg:
                        try:
                            if final_gen_cfg:
                                gen_kwargs.pop("config", None)
                                gen_kwargs["generation_config"] = final_gen_cfg
                            response = self._client.models.generate_content(**gen_kwargs)
                        except TypeError as te2:
                            # If `safety_settings` is also unsupported, drop it and retry once
                            msg2 = str(te2).lower()
                            if "unexpected keyword" in msg2 and "safety_settings" in msg2:
                                gen_kwargs.pop("safety_settings", None)
                                response = self._client.models.generate_content(**gen_kwargs)
                            else:
                                raise
                    # If `safety_settings` alone is unsupported, drop it and retry
                    elif "unexpected keyword" in msg and "safety_settings" in msg:
                        gen_kwargs.pop("safety_settings", None)
                        response = self._client.models.generate_content(**gen_kwargs)
                    else:
                        raise
                
                # Blocked prompt handling (avoid touching response.text/parts when blocked)
                try:
                    pf = getattr(response, "prompt_feedback", None)
                    block_reason = getattr(pf, "block_reason", None) if pf is not None else None
                except Exception:
                    pf, block_reason = None, None
                if block_reason:
                    # Retry up to 2 more times on prompt blocks before surfacing error
                    reason_map = {
                        0: "UNSPECIFIED",
                        1: "SAFETY",
                        2: "OTHER",
                        3: "BLOCKLIST",
                        4: "PROHIBITED_CONTENT",
                        5: "IMAGE_SAFETY",
                    }
                    try:
                        reason_val = int(block_reason)
                    except Exception:
                        # Fallback if enum object doesn't cast nicely
                        try:
                            reason_val = int(str(block_reason))
                        except Exception:
                            reason_val = None
                    reason_name = reason_map.get(reason_val, str(block_reason))
                    msg = f"Prompt blocked: block_reason={block_reason} ({reason_name})"
                    logger.warning(f"{msg} [key={self._key_tag()}]")
                    # If we still have bonus attempts left (specific to blocks), retry
                    # Note: attempt is 0-based; treat last two iterations as bonus for block cases
                    remaining_bonus = max(0, (total_attempts - max(1, int(max_retries))) - max(0, attempt - (max(1, int(max_retries)) - 1)))
                    if attempt < (max(1, int(max_retries)) + block_bonus_attempts - 1):
                        wait_time = 1  # short fixed wait for block retries
                        logger.info(f"Retrying due to prompt block in {wait_time}s... [key={self._key_tag()}]")
                        time.sleep(wait_time)
                        continue
                    # No more retries: surface as error so the manager can rotate keys/log
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
                        # Retry immediately for safety finishes, up to max_retries.
                        logger.warning(f"Response blocked due to safety concerns [key={self._key_tag()}]")
                        if attempt < max_retries - 1:
                            logger.info(f"Retrying immediately due to safety finish (attempt {attempt + 1}/{max_retries}) [key={self._key_tag()}]")
                            continue
                        raise ContentGenerationError("Response blocked due to safety")
                
                return response
                
            except Exception as e:
                err = str(e)
                logger.error(f"Content generation failed (attempt {attempt + 1}/{max_retries}) [key={self._key_tag()}]: {err}")
                # For rate limit/quota errors, fail fast so the multi-key layer can rotate.
                low = err.lower()
                if ("429" in low or "rate limit" in low or "too many requests" in low or
                        "quota" in low or "resource has been exhausted" in low):
                    raise ContentGenerationError(err)
                if attempt < max_retries - 1:
                    wait_time = backoff_factor ** attempt
                    logger.info(f"Retrying in {wait_time} seconds... [key={self._key_tag()}]")
                    time.sleep(wait_time)
                else:
                    raise ContentGenerationError(f"Failed to generate content after {max_retries} attempts: {err}")
        
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
            if self._client is None:
                return None
            return self._client.files.get(name=file_name)
        except Exception as e:
            logger.error(f"Failed to get file {file_name} [key={self._key_tag()}]: {str(e)}")
            return None
