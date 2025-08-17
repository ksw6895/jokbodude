"""
Multi-API manager for handling multiple Gemini API keys.
Provides load balancing, failure tracking, and automatic failover.
"""

import time
import random
from typing import List, Dict, Any, Optional, Callable
from datetime import datetime, timedelta
import threading
import google.generativeai as genai

from .client import GeminiAPIClient
from ..utils.logging import get_logger
from ..utils.exceptions import APIError, ContentGenerationError

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
        
    def record_failure(self, error: str):
        """Record a failed API call."""
        self.total_requests += 1
        self.total_failures += 1
        self.consecutive_failures += 1
        self.last_used = datetime.now()
        self.last_error = error
        
        # Apply cooldown after consecutive failures
        if self.consecutive_failures >= 3:
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
        
        # Create API clients for each key
        self.api_clients = []
        self.models = []
        
        for i, api_key in enumerate(api_keys):
            # Configure API for this key
            genai.configure(api_key=api_key)
            
            # Create model
            model = genai.GenerativeModel(**model_config)
            self.models.append(model)
            
            # Create API client
            client = GeminiAPIClient(model, api_key, key_index=i)
            self.api_clients.append(client)
            
        safe_ids = [f"k{i}:***{(k or '')[-4:] if k else '????'}" for i, k in enumerate(api_keys)]
        logger.info(f"Initialized MultiAPIManager with {len(api_keys)} API keys: {', '.join(safe_ids)}")
    
    def get_next_available_api(self) -> Optional[int]:
        """
        Get the next available API index using round-robin with availability checking.
        
        Returns:
            API index or None if no APIs available
        """
        with self._lock:
            # Try to find an available API starting from current index
            attempts = 0
            while attempts < len(self.api_keys):
                # Check if current API is available
                if self.api_statuses[self.current_index].check_availability():
                    selected_index = self.current_index
                    # Move to next index for next call
                    self.current_index = (self.current_index + 1) % len(self.api_keys)
                    return selected_index
                
                # Try next API
                self.current_index = (self.current_index + 1) % len(self.api_keys)
                attempts += 1
            
            # No available APIs
            return None
    
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
    
    def execute_with_failover(self, operation: Callable, max_retries: int = 3) -> Any:
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
        errors = []
        total_attempts = 0
        
        while total_attempts < max_retries:
            # Get next available API
            api_index = self.get_next_available_api()
            
            if api_index is None:
                # No available APIs, wait and retry
                logger.warning("No available APIs, waiting 30 seconds...")
                time.sleep(30)
                total_attempts += 1
                continue
            
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
                
                return result
                
            except Exception as e:
                error_msg = str(e)
                logger.error(f"API key {api_index} (***{key_suffix}) failed: {error_msg}")
                
                # Record failure
                status.record_failure(error_msg)
                errors.append(f"API {api_index}: {error_msg}")
                
                # Check for specific errors that should trigger immediate failover
                if "429" in error_msg or "quota" in error_msg.lower():
                    logger.warning(f"API key {api_index} (***{key_suffix}) hit quota limit")
                elif "403" in error_msg:
                    logger.warning(f"API key {api_index} (***{key_suffix}) has permission issues")
                
                total_attempts += 1
        
        # All attempts failed
        raise APIError(f"All API attempts failed after {total_attempts} tries. Errors: {'; '.join(errors)}")
    
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
                        parallel: bool = True, max_workers: int = 3,
                        on_progress: Optional[Callable[[Any], None]] = None) -> List[Any]:
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
                safe_workers = max(1, min(max_workers or 1, len(self.api_keys), len(tasks)))
            except Exception:
                safe_workers = max_workers or 1
            with ThreadPoolExecutor(max_workers=safe_workers) as executor:
                future_to_task = {}
                
                for task in tasks:
                    # Submit task with failover
                    future = executor.submit(
                        self.execute_with_failover,
                        lambda api_client, model: operation(task, api_client, model)
                    )
                    future_to_task[future] = task
                
                # Collect results
                for future in as_completed(future_to_task):
                    task = future_to_task[future]
                    try:
                        result = future.result()
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
        else:
            # Sequential processing
            for task in tasks:
                try:
                    result = self.execute_with_failover(
                        lambda api_client, model: operation(task, api_client, model)
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
