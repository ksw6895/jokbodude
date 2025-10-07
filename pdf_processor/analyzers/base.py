"""
Base analyzer class for PDF analysis strategies.
Provides common functionality for different analysis modes.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
import json
from datetime import datetime

from ..api.client import GeminiAPIClient
from ..api.file_manager import FileManager
from ..pdf.operations import PDFOperations
from ..parsers.response_parser import ResponseParser
from ..parsers.result_merger import ResultMerger
from ..utils.billing import BillingConverter
from ..utils.logging import get_logger
from ..utils.exceptions import PDFProcessorError, ContentGenerationError, InsufficientTokensError

logger = get_logger(__name__)


class BaseAnalyzer(ABC):
    """Abstract base class for PDF analyzers."""
    
    def __init__(self, api_client: GeminiAPIClient, file_manager: FileManager, 
                 session_id: str, debug_dir: Path):
        """
        Initialize the analyzer.
        
        Args:
            api_client: Gemini API client instance
            file_manager: File manager instance
            session_id: Session identifier
            debug_dir: Directory for debug outputs
        """
        self.api_client = api_client
        self.file_manager = file_manager
        self.session_id = session_id
        self.debug_dir = debug_dir
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        # User-configurable minimum relevance score (0..110). Default 80.
        self.min_relevance_score: int = 80
        # Multi-API paths should perform a single model call per failover attempt.
        # Single-key paths may allow limited local retries.
        self.prefer_single_attempt: bool = False
        # Lazily cached StorageManager for hot cancel/progress paths
        self._sm = None
        # Latest usage metadata from Gemini (for billing conversion)
        self._last_usage_metadata: Optional[Any] = None
        # Streaming usage accounting state
        self._streaming_gemini_tokens: int = 0
        self._streaming_jd_total: int = 0
        self._user_id: Optional[str] = None
        self._user_id_loaded: bool = False

    def _sm_cached(self):
        """Lazily create and cache a StorageManager for this analyzer instance."""
        try:
            if self._sm is None:
                from storage_manager import StorageManager  # local import to avoid cycles
                try:
                    self._sm = StorageManager()
                except Exception:
                    self._sm = None
            return self._sm
        except Exception:
            return None

    def _resolve_user_id(self) -> Optional[str]:
        """Retrieve the user identifier associated with the current job."""

        if self._user_id_loaded:
            return self._user_id

        sm = self._sm_cached()
        user_id: Optional[str] = None
        if sm is not None:
            try:
                metadata = sm.get_job_metadata(self.session_id)
                if isinstance(metadata, dict):
                    raw = metadata.get("user_id")
                    if raw:
                        user_id = str(raw)
            except Exception:
                user_id = None
        self._user_id = user_id
        self._user_id_loaded = True
        return self._user_id

    def _handle_stream_chunk(self, payload: Dict[str, Any]) -> None:
        """Process streaming chunk metadata to update token accounting."""

        if not isinstance(payload, dict):
            return

        usage_metadata = payload.get("usage_metadata")
        if usage_metadata is not None:
            self._last_usage_metadata = usage_metadata

        gemini_total = payload.get("cumulative_gemini_tokens")
        try:
            gemini_total_int = int(gemini_total) if gemini_total is not None else None
        except Exception:
            gemini_total_int = None

        chunk_text = payload.get("text") or ""

        self._apply_streaming_usage(gemini_total_int, chunk_text, usage_metadata)

    def _apply_streaming_usage(
        self,
        gemini_total: Optional[int],
        chunk_text: str,
        usage_metadata: Any,
    ) -> None:
        """Update Redis with streaming usage and trigger JD token charges."""

        sm = self._sm_cached()
        if sm is None:
            return

        total_tokens = gemini_total if gemini_total is not None and gemini_total > 0 else None
        if total_tokens is None:
            estimated = self._estimate_tokens_from_text(chunk_text)
            if estimated <= 0:
                return
            total_tokens = self._streaming_gemini_tokens + estimated

        if total_tokens < self._streaming_gemini_tokens:
            total_tokens = self._streaming_gemini_tokens

        self._streaming_gemini_tokens = total_tokens

        model_name = getattr(self.api_client, "model_name", None)
        jd_total = BillingConverter.api_to_jd(
            api_total=total_tokens,
            model=model_name,
            usage_details=usage_metadata,
        )

        if jd_total < self._streaming_jd_total:
            jd_total = self._streaming_jd_total

        delta = jd_total - self._streaming_jd_total
        debited = 0
        if delta > 0:
            debited = self._charge_streaming_tokens(delta)
            self._streaming_jd_total += delta

        try:
            sm.record_token_usage(
                self.session_id,
                delta,
                gemini_tokens_total=self._streaming_gemini_tokens,
                jd_tokens_total=self._streaming_jd_total,
                debited_delta=debited,
            )
        except Exception as exc:
            logger.warning(
                "Failed to persist streaming token usage for job %s: %s",
                self.session_id,
                exc,
            )

    def _charge_streaming_tokens(self, delta: int) -> int:
        """Attempt to deduct JD tokens for the current streaming increment."""

        if delta <= 0:
            return 0

        sm = self._sm_cached()
        if sm is None:
            return 0

        user_id = self._resolve_user_id()
        if not user_id:
            return 0

        try:
            if sm.consume_user_tokens(user_id, delta):
                return delta
        except Exception as exc:
            logger.error(
                "Failed to consume streaming JD tokens for job %s: %s",
                self.session_id,
                exc,
            )
            raise

        # Token balance exhausted – request cancellation and surface the error.
        try:
            sm.request_cancel(self.session_id)
        except Exception:
            pass
        try:
            progress = (sm.get_progress(self.session_id) or {}).get("progress", 0)
        except Exception:
            progress = 0
        try:
            sm.update_progress(
                self.session_id,
                int(progress or 0),
                "토큰 잔액 부족으로 작업이 중지되었습니다",
            )
        except Exception:
            pass

        raise InsufficientTokensError("JD token balance exhausted during streaming")

    @staticmethod
    def _estimate_tokens_from_text(chunk_text: str) -> int:
        """Rudimentary token estimation when metadata is unavailable."""

        if not chunk_text:
            return 0
        try:
            approx = max(1, int(len(chunk_text) / 4))
        except Exception:
            approx = 1
        return approx

        
    @abstractmethod
    def get_mode(self) -> str:
        """Get the analyzer mode name."""
        pass
    
    @abstractmethod
    def build_prompt(self, *args, **kwargs) -> str:
        """Build the analysis prompt."""
        pass
    
    @abstractmethod
    def analyze(self, *args, **kwargs) -> Dict[str, Any]:
        """Perform the analysis."""
        pass
    
    def save_debug_response(self, response_text: str, *file_identifiers: str) -> None:
        """
        Save API response for debugging.
        
        Args:
            response_text: The API response text
            file_identifiers: File names or identifiers for the debug filename
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        mode = self.get_mode()
        
        # Create filename from identifiers
        identifiers_str = "_".join(str(f).replace("/", "_").replace("\\", "_") 
                                  for f in file_identifiers)
        filename = f"{timestamp}_{mode}_{identifiers_str}_response.json"
        filepath = self.debug_dir / filename
        
        debug_data = {
            "timestamp": timestamp,
            "mode": mode,
            "session_id": self.session_id,
            "files": list(file_identifiers),
            "response": response_text,
            "response_length": len(response_text)
        }
        
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(debug_data, f, ensure_ascii=False, indent=2)
            logger.debug(f"Debug response saved to {filepath}")
        except Exception as e:
            logger.error(f"Failed to save debug response: {str(e)}")
    
    def process_with_chunks(self, pdf_path: str, analysis_func: callable, 
                          max_pages: int = 30) -> Dict[str, Any]:
        """
        Process a PDF in chunks if necessary.
        
        Args:
            pdf_path: Path to the PDF
            analysis_func: Function to call for analysis
            max_pages: Maximum pages per chunk
            
        Returns:
            Merged analysis results
        """
        chunks = PDFOperations.split_pdf_for_chunks(pdf_path, max_pages)
        
        if len(chunks) == 1:
            # Single chunk, process normally
            return analysis_func(pdf_path)
        
        # Process multiple chunks
        logger.info(f"Processing {len(chunks)} chunks for {Path(pdf_path).name}")
        chunk_results = []
        
        for i, (path, start_page, end_page) in enumerate(chunks):
            # Cooperative cancellation check between chunks
            try:
                from ..utils.exceptions import CancelledError
                sm = self._sm_cached()
                if sm and sm.is_cancelled(self.session_id):
                    raise CancelledError("cancelled")
            except CancelledError:
                raise
            except Exception:
                pass
            logger.info(f"Processing chunk {i+1}/{len(chunks)}: pages {start_page}-{end_page}")
            
            # Extract chunk to temporary file
            chunk_path = PDFOperations.extract_pages(path, start_page, end_page)
            
            try:
                # Analyze chunk
                result = analysis_func(chunk_path, chunk_info=(start_page, end_page))
                chunk_results.append(result)
            finally:
                # Clean up temporary file
                Path(chunk_path).unlink(missing_ok=True)
        
        # Merge results
        return ResultMerger.merge_chunk_results(chunk_results, self.get_mode())
    
    def upload_and_analyze(self, files_to_upload: List[Tuple[str, str]], 
                          prompt: str) -> str:
        """
        Upload files and perform analysis with quality-aware retry.
        
        Args:
            files_to_upload: List of (file_path, display_name) tuples
            prompt: Analysis prompt
            
        Returns:
            API response text
        """
        uploaded_files = []

        # Upload files for this call and return raw response.
        # Simpler, safer policy: always delete all uploads after generation
        # (no center-file retention). Each call starts with a clean slate via
        # pre-upload purge at the analyzer-level.
        try:
            # Cooperative cancel before any network I/O
            try:
                from ..utils.exceptions import CancelledError
                sm = self._sm_cached()
                if sm and sm.is_cancelled(self.session_id):
                    raise CancelledError("cancelled")
            except CancelledError:
                raise
            except Exception:
                pass

            for file_path, display_name in files_to_upload:
                # Check cancellation between uploads as well
                try:
                    from ..utils.exceptions import CancelledError
                    sm = self._sm_cached()
                    if sm and sm.is_cancelled(self.session_id):
                        raise CancelledError("cancelled")
                except CancelledError:
                    raise
                except Exception:
                    pass
                uploaded_file = self.api_client.upload_file(file_path, display_name)
                uploaded_files.append(uploaded_file)
                self.file_manager.track_file(uploaded_file)

            # Final cooperative cancel before generation
            try:
                from ..utils.exceptions import CancelledError
                sm = self._sm_cached()
                if sm and sm.is_cancelled(self.session_id):
                    raise CancelledError("cancelled")
            except CancelledError:
                raise
            except Exception:
                pass

            content = [prompt] + uploaded_files
            response_text, api_usage_tokens = self._generate_with_quality_retry(content)
            try:
                sm = self._sm_cached()
                if sm and api_usage_tokens > 0:
                    usage_details = getattr(self, "_last_usage_metadata", None)
                    model_name = getattr(self.api_client, "model_name", None)
                    jd_tokens = BillingConverter.api_to_jd(
                        api_total=api_usage_tokens,
                        model=model_name,
                        usage_details=usage_details,
                    )
                    if jd_tokens > 0:
                        pending = max(0, jd_tokens - self._streaming_jd_total)
                        self._streaming_jd_total = max(self._streaming_jd_total, jd_tokens)
                        self._streaming_gemini_tokens = max(self._streaming_gemini_tokens, api_usage_tokens)
                        sm.record_token_usage(
                            self.session_id,
                            pending,
                            gemini_tokens_total=self._streaming_gemini_tokens,
                            jd_tokens_total=self._streaming_jd_total,
                            debited_delta=0,
                        )
            except Exception as e:
                logger.warning(
                    f"Failed to record JD token usage for job {self.session_id}: {e}"
                )
            return response_text
        finally:
            for file in uploaded_files:
                self.file_manager.delete_file_safe(file)
    
    def parse_and_validate_response(self, response_text: str) -> Dict[str, Any]:
        """
        Parse and validate API response.
        
        Args:
            response_text: Raw API response text
            
        Returns:
            Parsed response dictionary
        """
        # Parse response
        result = ResponseParser.parse_response(response_text, self.get_mode())
        # Log a compact summary including the API key tag used for this call
        try:
            key_tag = getattr(self.api_client, '_key_tag', lambda: 'k?:***????')()
            mode = self.get_mode()
            if mode == 'jokbo-centric':
                pages = result.get('jokbo_pages') or []
                total_q = 0
                for p in pages:
                    try:
                        total_q += len((p or {}).get('questions', []) or [])
                    except Exception:
                        pass
                logger.info(f"Result summary [{key_tag}]: {len(pages)} pages, {total_q} questions")
            elif mode == 'lesson-centric':
                slides = result.get('related_slides') or []
                total_q = 0
                for s in slides:
                    try:
                        total_q += len((s or {}).get('related_jokbo_questions', []) or [])
                    except Exception:
                        pass
                logger.info(f"Result summary [{key_tag}]: {len(slides)} slides, {total_q} related questions")
            elif mode == 'partial-jokbo':
                qs = result.get('questions') or []
                logger.info(f"Result summary [{key_tag}]: {len(qs)} partial questions")
        except Exception:
            pass
        
        # Validate structure
        if not ResponseParser.validate_response_structure(result, self.get_mode()):
            raise PDFProcessorError(f"Invalid response structure for {self.get_mode()} mode")
        
        return result
    
    def filter_connections(self, connections: List[Dict[str, Any]], 
                         min_score: int = 80, max_connections: int = 2) -> List[Dict[str, Any]]:
        """
        Filter connections by relevance score.
        
        Args:
            connections: List of connections
            min_score: Minimum score threshold
            max_connections: Maximum connections to keep
            
        Returns:
            Filtered connections
        """
        # Use explicit min_score if passed, otherwise fall back to instance threshold
        effective_min = min_score if min_score is not None else getattr(self, 'min_relevance_score', 80)
        return ResultMerger.filter_connections_by_score(
            connections, effective_min, max_connections
        )

    def set_relevance_threshold(self, score: int) -> None:
        """Set the analyzer's minimum relevance threshold (clamped 0..110)."""
        try:
            v = int(score)
        except Exception:
            v = 80
        v = max(0, min(v, 110))
        self.min_relevance_score = v

    # --------------------
    # Internal helpers
    # --------------------
    def _is_empty_result(self, data: Dict[str, Any], mode: str) -> bool:
        """Check if the parsed result is semantically empty for the given mode."""
        try:
            if mode == "jokbo-centric":
                pages = data.get("jokbo_pages") or []
                if not isinstance(pages, list) or not pages:
                    return True
                total_q = 0
                for p in pages:
                    total_q += len((p or {}).get("questions", []) or [])
                return total_q == 0
            elif mode == "partial-jokbo":
                qs = data.get("questions") or []
                if not isinstance(qs, list) or not qs:
                    return True
                # If present but all entries are malformed, treat as empty
                try:
                    valid = 0
                    for q in qs:
                        ps = int((q or {}).get("page_start") or 0)
                        if ps > 0:
                            valid += 1
                    return valid == 0
                except Exception:
                    return False
            else:
                slides = data.get("related_slides") or []
                if not isinstance(slides, list) or not slides:
                    return True
                total_q = 0
                for s in slides:
                    total_q += len((s or {}).get("related_jokbo_questions", []) or [])
                return total_q == 0
        except Exception:
            # If anything is off, don't incorrectly treat as empty
            return False

    def _generate_with_quality_retry(
        self, content: List[Any], retries: int = 2
    ) -> Tuple[str, int]:
        """
        Generate content and retry if output looks suspicious per parser heuristics.
        Retries are performed with the same API key and same uploaded files.
        """
        last_error: Exception | None = None
        mode = self.get_mode()
        attempts = 1 if getattr(self, "prefer_single_attempt", False) else max(1, retries + 1)
        # Reset streaming accounting state for this generation run
        self._streaming_gemini_tokens = 0
        self._streaming_jd_total = 0
        for attempt in range(1, attempts + 1):
            try:
                from ..utils.exceptions import CancelledError

                sm = self._sm_cached()
                if sm and sm.is_cancelled(self.session_id):
                    raise CancelledError("cancelled")
            except CancelledError:
                raise
            except Exception:
                pass
            try:
                response, api_usage_tokens = self.api_client.generate_content(
                    content,
                    max_retries=1,
                    backoff_factor=1,
                    stream_handler=self._handle_stream_chunk,
                )
                try:
                    self._last_usage_metadata = getattr(response, "usage_metadata", None)
                except Exception:
                    self._last_usage_metadata = None
                text = response.text
                try:
                    parsed = ResponseParser.parse_response(text, mode)
                    if self._is_empty_result(parsed, mode):
                        logger.warning(
                            f"Empty {mode} result detected; treating as valid with no matches"
                        )
                        try:
                            normalized = json.dumps(parsed, ensure_ascii=False)
                            return normalized, api_usage_tokens
                        except Exception:
                            return text, api_usage_tokens
                    if ResponseParser.is_result_suspicious(parsed, mode):
                        logger.warning(
                            f"Suspicious {mode} result detected (attempt {attempt}/{attempts}); retrying..."
                        )
                        if attempt < attempts:
                            continue
                        raise ContentGenerationError("Suspicious content after retries")
                except Exception as pe:
                    last_error = pe
                    logger.warning(f"Parsing/quality check failed: {pe}")
                    if attempt < attempts:
                        continue
                    raise
                return text, api_usage_tokens
            except Exception as e:
                self._last_usage_metadata = None
                last_error = e
                msg = str(e)
                logger.error(f"Generation failed on attempt {attempt}/{attempts}: {msg}")
                if "Prompt blocked:" in msg:
                    raise ContentGenerationError(msg)
                if attempt < attempts:
                    continue
                raise ContentGenerationError(msg)
        if last_error:
            raise last_error
        raise ContentGenerationError("Unknown generation error")
