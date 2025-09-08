"""
Multi-API manager for handling multiple Gemini API keys.
Provides load balancing, failure tracking, and automatic failover.
"""

import time
import os
import random
from typing import List, Dict, Any, Optional, Callable
from datetime import datetime, timedelta
import threading
from google import genai  # google-genai unified SDK

from .client import GeminiAPIClient
from ..utils.logging import get_logger
from ..utils.exceptions import APIError, ContentGenerationError, CancelledError

logger = get_logger(__name__)


class APIKeyStatus:
    """Tracks the status of an individual API key."""
    
    def __init__(self, api_key: str, index: int):
        self.api_key = api_key
        self.index = index
        self.is_available = True
        self.consecutive_failures = 0
        self.total_requests = 0
        self.total_failures = 0
        self.last_used = None
        self.cooldown_until = None
        self.last_error = None
        
    def record_success(self):
        """Record a successful API call."""
        self.total_requests += 1
        self.consecutive_failures = 0
        self.last_used = datetime.now()
        self.last_error = None
        
    def record_failure(self, error: str, *, category: Optional[str] = None,
                       rate_limit_cooldown_secs: int = 30) -> None:
        """Record a failed API call with classification.

        Categories:
        - 'prompt_block': PromptFeedback block (SAFETY/BLOCKLIST/OTHER/PROHIBITED_CONTENT)
        - 'rate_limit': Quota/rate limiting (HTTP 429, resource exhausted)
        - 'auth': Authentication/permission issues (401/403)
        - 'server': Server-side/transient errors (5xx/unavailable)
        - 'network': Client-side connectivity timeouts
        - 'unknown': Everything else
        """
        self.total_requests += 1
        self.total_failures += 1
        self.last_used = datetime.now()
        self.last_error = error

        # Only count towards consecutive failures for issues likely tied to this key
        if category in {"rate_limit", "auth", "server", "unknown"}:
            self.consecutive_failures += 1
        else:
            # For prompt blocks, do not penalize the key with consecutive strikes
            # as the prompt itself likely needs adjustment.
            pass

        # Short, transient cooldown for rate limiting to encourage rotation
        if category == "rate_limit" and rate_limit_cooldown_secs > 0:
            self.cooldown_until = datetime.now() + timedelta(seconds=rate_limit_cooldown_secs)
            self.is_available = False
            logger.warning(
                f"API key {self.index} short cooldown {rate_limit_cooldown_secs}s due to rate limit"
            )

        # Apply longer cooldown only after repeated key-specific failures
        if self.consecutive_failures >= 3 and category in {"rate_limit", "auth", "server", "unknown"}:
            self.cooldown_until = datetime.now() + timedelta(minutes=10)
            self.is_available = False
            logger.warning(f"API key {self.index} entering cooldown until {self.cooldown_until}")
    
    def check_availability(self) -> bool:
        """Check if the API key is available for use."""
        if self.cooldown_until and datetime.now() < self.cooldown_until:
            return False
        
        # Reset availability after cooldown
        if self.cooldown_until and datetime.now() >= self.cooldown_until:
            self.is_available = True
            self.cooldown_until = None
            self.consecutive_failures = 0
            logger.info(f"API key {self.index} cooldown ended")
            
        return self.is_available
    
    def get_success_rate(self) -> float:
        """Get the success rate of this API key."""
        if self.total_requests == 0:
            return 1.0
        return (self.total_requests - self.total_failures) / self.total_requests


class MultiAPIManager:
    """Manages multiple API keys with load balancing and failover."""
    
    def __init__(self, api_keys: List[str], model_config: Dict[str, Any]):
        """
        Initialize the multi-API manager.
        
        Args:
            api_keys: List of Gemini API keys
            model_config: Configuration for the Gemini model
        """
        self.api_keys = api_keys
        self.model_config = model_config
        self.api_statuses = [APIKeyStatus(key, i) for i, key in enumerate(api_keys)]
        self.current_index = 0
        self._lock = threading.Lock()
        # Per-key concurrency limit (allow >1 to enable parallel ops on same key)
        try:
            self.per_key_limit = max(1, int(os.getenv("GEMINI_PER_KEY_CONCURRENCY", "1")))
        except Exception:
            self.per_key_limit = 1
        # Track per-key in-flight counts
        self._in_use_counts: dict[int, int] = {}
        self._cv = threading.Condition(self._lock)
        # Short cooldown applied to a key when rate limited, to encourage rotation
        try:
            self.rate_limit_cooldown_secs = max(0, int(os.getenv("GEMINI_RATE_LIMIT_COOLDOWN_SECS", "30")))
        except Exception:
            self.rate_limit_cooldown_secs = 30
        
        # Create API clients for each key
        self.api_clients = []
        self.models = []  # keep for interface; we store model config dicts

        # Global prompt-block guard within the lifetime of this manager (i.e., per job).
        # Once we detect a prompt block, we will only allow each key to be tried once
        # across all subsequent execute_with_failover() calls, then stop.
        self._block_mode_active: bool = False
        self._global_tried_indices: set[int] = set()
        
        for i, api_key in enumerate(api_keys):
            # Model config dict for this client; keep original config
            cfg = dict(model_config or {})
            self.models.append(cfg)
            # Create API client (maintains its own per-key genai.Client for file ops)
            client = GeminiAPIClient(cfg, api_key, key_index=i)
            self.api_clients.append(client)
            
        safe_ids = [f"k{i}:***{(k or '')[-4:] if k else '????'}" for i, k in enumerate(api_keys)]
        logger.info(f"Initialized MultiAPIManager with {len(api_keys)} API keys: {', '.join(safe_ids)}")

    @staticmethod
    def _categorize_error(error_msg: str) -> str:
        """Best-effort classification for error handling policy."""
        em = (error_msg or "").lower()
        if "prompt blocked" in em or "block_reason" in em:
            return "prompt_block"
        if "429" in em or "rate limit" in em or "too many requests" in em or "quota" in em or "resource has been exhausted" in em:
            return "rate_limit"
        if "403" in em or "forbidden" in em or "permission" in em or "401" in em or "unauthorized" in em:
            return "auth"
        if "5xx" in em or "internal" in em or "unavailable" in em or "deadline" in em or "server" in em:
            return "server"
        if "timeout" in em or "timed out" in em or "connection" in em:
            return "network"
        return "unknown"
    
    def _acquire_api_index(self, wait: bool = True, timeout: Optional[float] = None) -> Optional[int]:
        """
        Acquire an available, not-in-use API index. Optionally wait until one is free.
        Ensures at most one concurrent operation per key to avoid cross-upload interference.
        """
        with self._cv:
            end_time = None if timeout is None else (time.time() + timeout)
            while True:
                # Scan for an available, idle key starting from current_index
                n = len(self.api_keys)
                for _ in range(n):
                    idx = self.current_index
                    self.current_index = (self.current_index + 1) % n
                    status = self.api_statuses[idx]
                    in_use = self._in_use_counts.get(idx, 0)
                    if status.check_availability() and in_use < self.per_key_limit:
                        # reserve a slot
                        self._in_use_counts[idx] = in_use + 1
                        return idx
                # None found
                if not wait:
                    return None
                # Wait for a key to be released or become available
                if timeout is not None:
                    remaining = end_time - time.time()
                    if remaining <= 0:
                        return None
                    self._cv.wait(timeout=remaining)
                else:
                    self._cv.wait(0.5)

    def _acquire_api_index_excluding(
        self,
        exclude: set[int],
        wait: bool = True,
        timeout: Optional[float] = None,
    ) -> Optional[int]:
        """
        Acquire an available API index that is not in the exclude set.
        Mirrors _acquire_api_index but skips indices already tried in this operation.
        """
        with self._cv:
            # If all keys are excluded, waiting won't help; return immediately
            try:
                if len(exclude) >= len(self.api_keys):
                    return None
            except Exception:
                pass
            end_time = None if timeout is None else (time.time() + timeout)
            while True:
                n = len(self.api_keys)
                for _ in range(n):
                    idx = self.current_index
                    self.current_index = (self.current_index + 1) % n
                    if idx in exclude:
                        continue
                    status = self.api_statuses[idx]
                    in_use = self._in_use_counts.get(idx, 0)
                    if status.check_availability() and in_use < self.per_key_limit:
                        self._in_use_counts[idx] = in_use + 1
                        return idx
                if not wait:
                    return None
                if timeout is not None:
                    remaining = end_time - time.time()
                    if remaining <= 0:
                        return None
                    self._cv.wait(timeout=remaining)
                else:
                    self._cv.wait(0.5)
    
    def _release_api_index(self, idx: int) -> None:
        with self._cv:
            cur = self._in_use_counts.get(idx, 0)
            if cur > 1:
                self._in_use_counts[idx] = cur - 1
            elif cur == 1:
                del self._in_use_counts[idx]
            # Wake waiters
            self._cv.notify_all()
    
    def get_best_api(self) -> Optional[int]:
        """
        Get the best performing API based on success rate.
        
        Returns:
            API index or None if no APIs available
        """
        with self._lock:
            available_apis = [
                (i, status) for i, status in enumerate(self.api_statuses)
                if status.check_availability()
            ]
            
            if not available_apis:
                return None
            
            # Sort by success rate (descending) and least recently used
            available_apis.sort(
                key=lambda x: (x[1].get_success_rate(), -x[1].total_requests),
                reverse=True
            )
            
            return available_apis[0][0]
    
    def execute_with_failover(self, operation: Callable, max_retries: Optional[int] = None) -> Any:
        """
        Execute an operation with automatic failover to different API keys.
        
        Args:
            operation: Function that takes (api_client, model) and returns result
            max_retries: Maximum retry attempts across all APIs
            
        Returns:
            Operation result
            
        Raises:
            APIError: If all APIs fail
        """
        errors: list[str] = []
        # Default: clamp to at most 3 tries per operation unless caller overrides
        if max_retries is None:
            max_retries = 3

        # Enforce at-most-once per key, regardless of max_retries value
        max_unique_tries = min(max_retries, len(self.api_keys))
        tried_indices: set[int] = set()
        prompt_block_seen = False

        # If we've previously seen a prompt block in this manager, exclude keys that were
        # already tried during the block window from future attempts.
        with self._lock:
            global_excluded = set(self._global_tried_indices) if self._block_mode_active else set()

        # Fast-fail if block mode is active and all keys are globally excluded
        if self._block_mode_active and len(global_excluded) >= len(self.api_keys):
            raise APIError(
                "Prompt blocked and all API keys already tried for this prompt; aborting without wait"
            )

        while len(tried_indices) < max_unique_tries:
            # Acquire an available, idle API key index that we haven't tried yet
            excluded_now = tried_indices | global_excluded
            # If nothing remains to try, abort immediately (avoid 60s wait loops)
            if len(excluded_now) >= len(self.api_keys):
                logger.warning("All API keys already tried for this prompt; aborting immediately")
                break
            api_index = self._acquire_api_index_excluding(excluded_now, wait=True, timeout=60.0)
            if api_index is None:
                logger.warning("No available APIs after waiting; aborting further retries for this operation")
                break
            
            api_client = self.api_clients[api_index]
            model = self.models[api_index]
            status = self.api_statuses[api_index]
            
            try:
                key_suffix = (self.api_keys[api_index] or "")[-4:] if self.api_keys else "????"
                logger.info(f"Attempting operation with API key {api_index} (***{key_suffix})")
                
                # Execute operation
                result = operation(api_client, model)
                
                # Record success
                status.record_success()
                logger.info(f"Operation successful with API key {api_index} (***{key_suffix})")
                # On success, clear any prompt-block global state to allow future operations normally
                with self._lock:
                    self._block_mode_active = False
                    self._global_tried_indices.clear()
                return result
                
            except Exception as e:
                # Immediate abort on cooperative cancellation
                if isinstance(e, CancelledError):
                    # Do not penalize key or rotate; propagate cancel
                    raise
                error_msg = str(e)
                category = self._categorize_error(error_msg)
                logger.error(f"API key {api_index} (***{key_suffix}) failed: {error_msg}")

                # Record failure with category-aware policy
                status.record_failure(
                    error_msg,
                    category=category,
                    rate_limit_cooldown_secs=self.rate_limit_cooldown_secs,
                )
                errors.append(f"API {api_index}: {error_msg}")

                # Hints in logs
                if category == "rate_limit":
                    logger.warning(f"API key {api_index} (***{key_suffix}) rate limited; rotating to next key")
                elif category == "auth":
                    logger.warning(f"API key {api_index} (***{key_suffix}) permission/auth issue")
                elif category == "prompt_block":
                    logger.info(
                        f"Prompt blocked; not penalizing key {api_index} and not retrying same key for this prompt"
                    )
                    prompt_block_seen = True
                    # Activate global block mode and remember that this key was tried.
                    with self._lock:
                        self._block_mode_active = True
                        self._global_tried_indices.add(api_index)
            finally:
                # Release the API key for other tasks
                self._release_api_index(api_index)

            # Mark this key as tried regardless of error class
            tried_indices.add(api_index)
            # If block mode is active (either from this or a prior attempt), record globally
            with self._lock:
                if self._block_mode_active:
                    self._global_tried_indices.add(api_index)

            # If we've seen a prompt block, continue trying remaining, untried keys once
            # but do NOT start a second pass.
            if prompt_block_seen and len(tried_indices) >= max_unique_tries:
                break

        # All attempts failed or aborted
        raise APIError(
            f"All API attempts failed after {len(tried_indices)} unique tries. Errors: {'; '.join(errors)}"
        )
    
    def get_status_report(self) -> Dict[str, Any]:
        """Get a status report of all API keys."""
        with self._lock:
            report = {
                "total_apis": len(self.api_keys),
                "available_apis": sum(1 for s in self.api_statuses if s.check_availability()),
                "api_details": []
            }
            
            for status in self.api_statuses:
                api_detail = {
                    "index": status.index,
                    "available": status.check_availability(),
                    "total_requests": status.total_requests,
                    "total_failures": status.total_failures,
                    "consecutive_failures": status.consecutive_failures,
                    "success_rate": f"{status.get_success_rate():.2%}",
                    "last_error": status.last_error,
                    "cooldown_until": status.cooldown_until.isoformat() if status.cooldown_until else None
                }
                report["api_details"].append(api_detail)
            
            return report
    
    def reset_api_status(self, api_index: int):
        """Reset the status of a specific API key."""
        with self._lock:
            if 0 <= api_index < len(self.api_statuses):
                status = self.api_statuses[api_index]
                status.consecutive_failures = 0
                status.is_available = True
                status.cooldown_until = None
                logger.info(f"Reset status for API key {api_index}")
    
    def distribute_tasks(self, tasks: List[Any], operation: Callable,
                        parallel: bool = True, max_workers: Optional[int] = None,
                        on_progress: Optional[Callable[[Any], None]] = None,
                        cancel_check: Optional[Callable[[], bool]] = None,
                        max_failover_retries: Optional[int] = 3) -> List[Any]:
        """
        Distribute tasks across multiple API keys.
        
        Args:
            tasks: List of tasks to process
            operation: Function that takes (task, api_client, model) and returns result
            parallel: Whether to process in parallel
            max_workers: Maximum parallel workers
            
        Returns:
            List of results
        """
        results = []
        
        if parallel:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            
            # Clamp workers to number of API keys and number of tasks
            try:
                desired = max_workers if isinstance(max_workers, int) and max_workers > 0 else len(self.api_keys)
                safe_workers = max(1, min(desired, len(self.api_keys), len(tasks)))
            except Exception:
                safe_workers = max_workers or 1

            executor = ThreadPoolExecutor(max_workers=safe_workers)
            try:
                future_to_task: dict = {}
                start_times: dict = {}
                for task in tasks:
                    # Do not submit more tasks if cancelled
                    try:
                        if cancel_check and cancel_check():
                            break
                    except Exception:
                        pass
                    # Submit task with failover (bind task in lambda default)
                    fut = executor.submit(
                        self.execute_with_failover,
                        (lambda api_client, model, _task=task: operation(_task, api_client, model)),
                        max_retries=max_failover_retries
                    )
                    future_to_task[fut] = task
                    start_times[fut] = time.time()

                pending = set(future_to_task.keys())
                while pending:
                    # Early abort if cancellation requested; attempt to cancel pending futures
                    try:
                        if cancel_check and cancel_check():
                            for f in list(pending):
                                try:
                                    f.cancel()
                                except Exception:
                                    pass
                            break
                    except Exception:
                        pass

                    # Gather any completed futures with a short poll
                    done_now = set()
                    try:
                        for f in as_completed(list(pending), timeout=1.0):
                            done_now.add(f)
                    except Exception:
                        done_now = set()

                    for f in done_now:
                        pending.discard(f)
                        task = future_to_task.get(f)
                        try:
                            result = f.result()
                            results.append(result)
                        except Exception as e:
                            logger.error(f"Task failed: {str(e)}")
                            results.append({"error": str(e), "task": task})
                        finally:
                            try:
                                if on_progress and task is not None:
                                    on_progress(task)
                            except Exception:
                                pass

                    # Removed global hard wall-clock timeout. Chunk-level request timeouts apply per call.
            finally:
                # Do not block on any remaining worker threads (if any); cancel queued tasks
                try:
                    executor.shutdown(wait=False, cancel_futures=True)
                except Exception:
                    pass
        else:
            # Sequential processing
            for task in tasks:
                try:
                    if cancel_check and cancel_check():
                        break
                except Exception:
                    pass
                try:
                    result = self.execute_with_failover(
                        lambda api_client, model: operation(task, api_client, model),
                        max_retries=max_failover_retries
                    )
                    results.append(result)
                except Exception as e:
                    logger.error(f"Task failed: {str(e)}")
                    results.append({"error": str(e), "task": task})
                finally:
                    try:
                        if on_progress:
                            on_progress(task)
                    except Exception:
                        pass
        
        return results
