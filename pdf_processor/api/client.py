"""
Gemini API client for handling API interactions.
Provides a clean interface for API operations with retry logic and error handling.

Concurrency note:
- We now avoid the process-global `genai.configure` and do NOT use a global lock.
- Each instance creates its own `genai.Client(api_key=...)` and uses client-scoped
  services (files/models) so calls can run truly concurrently, even for the same key
  when `GEMINI_PER_KEY_CONCURRENCY` > 1.
"""

import concurrent.futures as _fut
import importlib.util
import os
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple

_GENAI_SPEC = importlib.util.find_spec("google.genai")
if _GENAI_SPEC is not None:
    from google import genai  # google-genai unified SDK
    from google.genai import types as genai_types
else:  # pragma: no cover - executed only when dependency is missing
    class _MissingClient:
        """Placeholder that raises a helpful error when instantiated."""

        def __init__(self, *args, **kwargs):
            raise ModuleNotFoundError(
                "google-genai is required to use Gemini features. "
                "Install the 'google-genai' package to enable API access."
            )

    class _UploadFileConfig:
        """Lightweight stand-in for UploadFileConfig used in tests."""

        def __init__(self, *args, **kwargs):
            self.display_name = kwargs.get("display_name")
            self.mime_type = kwargs.get("mime_type")

    genai = SimpleNamespace(Client=_MissingClient)
    genai_types = SimpleNamespace(UploadFileConfig=_UploadFileConfig)
from ..utils.exceptions import (
    APIError,
    FileUploadError,
    ContentGenerationError,
    CancelledError,
    InsufficientTokensError,
)
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
                    or "gemini-flash-latest"
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
                    or "gemini-flash-latest"
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
            self.model_name = self.model_name or "gemini-flash-latest"
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
            # Prefer centralized builder to honor HTTP options/timeouts
            try:
                from config import build_client as _build_client  # local import to avoid cycles
                self._client = _build_client(self.api_key)
            except Exception:
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
            # Hard timeout for the initial upload call
            try:
                upload_timeout = max(10, int(os.getenv("GENAI_UPLOAD_TIMEOUT_SECS", "120")))
            except Exception:
                upload_timeout = 120
            _executor = _fut.ThreadPoolExecutor(max_workers=1)
            _timed_out = False
            try:
                fut = _executor.submit(self._client.files.upload, file=file_path, config=upload_cfg)
                uploaded_file = fut.result(timeout=upload_timeout)
            except _fut.TimeoutError:
                _timed_out = True
                raise FileUploadError(f"Upload timed out after {upload_timeout}s: {display_name}")
            finally:
                # If timed out, don't wait on the worker thread
                try:
                    _executor.shutdown(wait=(not _timed_out), cancel_futures=True)
                except Exception:
                    pass

            # Wait until file is ACTIVE per new SDK semantics with bounded wait
            try:
                activation_timeout = max(10, int(os.getenv("GENAI_UPLOAD_ACTIVATION_TIMEOUT_SECS", "300")))
            except Exception:
                activation_timeout = 250
            deadline = time.time() + activation_timeout
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
                if time.time() > deadline:
                    raise FileUploadError(f"File activation timed out after {activation_timeout}s: {display_name}")
                logger.debug(f"Processing {display_name} (state={state_name or 'UNKNOWN'})... [key={self._key_tag()}]")
                time.sleep(2)
                # Bound the get() call too
                _executor2 = _fut.ThreadPoolExecutor(max_workers=1)
                _t2_to = False
                try:
                    fut2 = _executor2.submit(self._client.files.get, name=uploaded_file.name)
                    uploaded_file = fut2.result(timeout=15)
                except _fut.TimeoutError:
                    _t2_to = True
                    # If a single poll blocks, retry next loop iteration
                    continue
                finally:
                    try:
                        _executor2.shutdown(wait=(not _t2_to), cancel_futures=True)
                    except Exception:
                        pass
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
        stream_handler: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Tuple[Any, int]:
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
        # Clamp to provided max_retries attempts in total.
        total_attempts = max(1, int(max_retries))
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
                model_name = self.model_name or "gemini-flash-latest"
                # New SDK prefers `config=`; add compatibility fallback to `generation_config=`
                gen_kwargs: Dict[str, Any] = {
                    "model": model_name,
                    "contents": content,
                }
                if final_gen_cfg:
                    gen_kwargs["config"] = final_gen_cfg
                if self.safety_settings:
                    gen_kwargs["safety_settings"] = self.safety_settings

                # Attempt streaming mode when requested and supported; fall back to normal calls.
                response = None
                api_usage_tokens = 0
                if callable(stream_handler):
                    try:
                        streamed = self._try_stream_generate_content(gen_kwargs, stream_handler)
                    except Exception as stream_exc:
                        # Propagate cancellation/insufficient token errors directly so that callers
                        # can handle them without retrying additional chunks.
                        if isinstance(stream_exc, (CancelledError, )):
                            raise
                        if isinstance(stream_exc, InsufficientTokensError):
                            raise
                        logger.warning(
                            "Streaming generate_content failed (%s); falling back to non-stream mode [key=%s]",
                            stream_exc,
                            self._key_tag(),
                        )
                    else:
                        if streamed is not None:
                            response, api_usage_tokens = streamed

                if response is None:
                    # Wrap the call with a hard timeout to avoid indefinite hangs
                    try:
                        try:
                            req_timeout = max(10, int(os.getenv("GENAI_REQUEST_TIMEOUT_SECS", "480")))
                        except Exception:
                            req_timeout = 480
                        _executor = _fut.ThreadPoolExecutor(max_workers=1)
                        _timed_out = False
                        try:
                            fut = _executor.submit(self._client.models.generate_content, **gen_kwargs)
                            response = fut.result(timeout=req_timeout)
                        except _fut.TimeoutError:
                            _timed_out = True
                            raise ContentGenerationError(f"Generation timed out after {req_timeout}s")
                        finally:
                            try:
                                _executor.shutdown(wait=(not _timed_out), cancel_futures=True)
                            except Exception:
                                pass
                    except TypeError as te:
                        msg = str(te).lower()
                        if "unexpected keyword" in msg and "config" in msg:
                            try:
                                if final_gen_cfg:
                                    gen_kwargs.pop("config", None)
                                    gen_kwargs["generation_config"] = final_gen_cfg
                                try:
                                    req_timeout = max(10, int(os.getenv("GENAI_REQUEST_TIMEOUT_SECS", "480")))
                                except Exception:
                                    req_timeout = 480
                                _executor = _fut.ThreadPoolExecutor(max_workers=1)
                                _timed_out2 = False
                                try:
                                    fut = _executor.submit(self._client.models.generate_content, **gen_kwargs)
                                    response = fut.result(timeout=req_timeout)
                                except _fut.TimeoutError:
                                    _timed_out2 = True
                                    raise ContentGenerationError(f"Generation timed out after {req_timeout}s")
                                finally:
                                    try:
                                        _executor.shutdown(wait=(not _timed_out2), cancel_futures=True)
                                    except Exception:
                                        pass
                            except TypeError as te2:
                                msg2 = str(te2).lower()
                                if "unexpected keyword" in msg2 and "safety_settings" in msg2:
                                    gen_kwargs.pop("safety_settings", None)
                                    try:
                                        req_timeout = max(10, int(os.getenv("GENAI_REQUEST_TIMEOUT_SECS", "480")))
                                    except Exception:
                                        req_timeout = 480
                                    _executor = _fut.ThreadPoolExecutor(max_workers=1)
                                    _timed_out3 = False
                                    try:
                                        fut = _executor.submit(self._client.models.generate_content, **gen_kwargs)
                                        response = fut.result(timeout=req_timeout)
                                    except _fut.TimeoutError:
                                        _timed_out3 = True
                                        raise ContentGenerationError(f"Generation timed out after {req_timeout}s")
                                    finally:
                                        try:
                                            _executor.shutdown(wait=(not _timed_out3), cancel_futures=True)
                                        except Exception:
                                            pass
                                else:
                                    raise
                        elif "unexpected keyword" in msg and "safety_settings" in msg:
                            gen_kwargs.pop("safety_settings", None)
                            try:
                                req_timeout = max(10, int(os.getenv("GENAI_REQUEST_TIMEOUT_SECS", "480")))
                            except Exception:
                                req_timeout = 480
                            _executor = _fut.ThreadPoolExecutor(max_workers=1)
                            _timed_out4 = False
                            try:
                                fut = _executor.submit(self._client.models.generate_content, **gen_kwargs)
                                response = fut.result(timeout=req_timeout)
                            except _fut.TimeoutError:
                                _timed_out4 = True
                                raise ContentGenerationError(f"Generation timed out after {req_timeout}s")
                            finally:
                                try:
                                    _executor.shutdown(wait=(not _timed_out4), cancel_futures=True)
                                except Exception:
                                    pass
                        else:
                            raise
                
                # Blocked prompt handling (avoid touching response.text/parts when blocked)
                try:
                    pf = getattr(response, "prompt_feedback", None)
                    block_reason = getattr(pf, "block_reason", None) if pf is not None else None
                except Exception:
                    pf, block_reason = None, None
                if block_reason:
                    # Immediately surface as error; outer layers decide on further retries
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
                
                api_usage_tokens = 0
                if api_usage_tokens <= 0:
                    try:
                        usage = getattr(response, "usage_metadata", None)
                        if usage is not None:
                            api_usage_tokens = int(getattr(usage, "total_token_count", 0) or 0)
                    except Exception:
                        api_usage_tokens = 0

                return response, api_usage_tokens
                
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

    def _try_stream_generate_content(
        self,
        gen_kwargs: Dict[str, Any],
        stream_handler: Callable[[Dict[str, Any]], None],
    ) -> Optional[Tuple[Any, int]]:
        """Attempt to stream Gemini responses and invoke callbacks per chunk."""

        if self._client is None:
            return None

        responses_iface = getattr(self._client, "responses", None)
        if responses_iface is None:
            return None
        stream_iface = getattr(getattr(responses_iface, "stream", None), "generate_content", None)
        if not callable(stream_iface):
            return None

        aggregated_text: List[str] = []
        last_usage = None
        cumulative_tokens = 0
        chunk_index = 0

        with stream_iface(**gen_kwargs) as stream:
            for chunk in stream:
                chunk_index += 1
                chunk_text = self._extract_stream_text(chunk)
                if chunk_text:
                    aggregated_text.append(chunk_text)
                usage_metadata = getattr(chunk, "usage_metadata", None)
                if usage_metadata is not None:
                    last_usage = usage_metadata
                    try:
                        cumulative_tokens = int(
                            getattr(usage_metadata, "total_token_count", cumulative_tokens) or cumulative_tokens
                        )
                    except Exception:
                        pass
                event = {
                    "chunk_index": chunk_index,
                    "text": chunk_text or "",
                    "usage_metadata": usage_metadata,
                    "cumulative_gemini_tokens": cumulative_tokens or None,
                }
                stream_handler(event)

            response = stream.get_final_response()

        if response is None:
            return None

        try:
            resp_text = getattr(response, "text", None)
        except Exception:
            resp_text = None
        if (resp_text is None or len(resp_text) == 0) and aggregated_text:
            try:
                setattr(response, "text", "".join(aggregated_text))
            except Exception:
                pass

        final_usage = getattr(response, "usage_metadata", None) or last_usage
        if final_usage is not None:
            try:
                final_total = int(getattr(final_usage, "total_token_count", cumulative_tokens) or cumulative_tokens)
            except Exception:
                final_total = cumulative_tokens
            if final_total > cumulative_tokens:
                cumulative_tokens = final_total
                event = {
                    "chunk_index": chunk_index + 1,
                    "text": "",
                    "usage_metadata": final_usage,
                    "cumulative_gemini_tokens": cumulative_tokens,
                }
                stream_handler(event)
            if getattr(response, "usage_metadata", None) is None:
                try:
                    setattr(response, "usage_metadata", final_usage)
                except Exception:
                    pass

        return response, int(cumulative_tokens)

    @staticmethod
    def _extract_stream_text(chunk: Any) -> str:
        """Extract text from a streaming chunk with graceful fallbacks."""

        try:
            text = getattr(chunk, "text", None)
            if text:
                return str(text)
        except Exception:
            pass

        parts = None
        try:
            parts = getattr(chunk, "parts", None)
            if parts is None:
                parts = getattr(getattr(chunk, "content", None), "parts", None)
        except Exception:
            parts = None

        texts: List[str] = []
        if parts:
            for part in parts:
                try:
                    p_text = getattr(part, "text", None)
                except Exception:
                    p_text = None
                if p_text:
                    texts.append(str(p_text))
        return "".join(texts)

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
