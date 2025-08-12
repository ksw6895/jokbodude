"""
Failed Chunk Retrier for Multi-API Processing

Handles intelligent retry logic for failed chunks with different API keys,
implementing exponential backoff and failure categorization.
"""

from typing import Dict, Any, List, Optional, Callable
from datetime import datetime, timedelta
import time
import threading
from enum import Enum


class FailureType(Enum):
    """Types of API failures"""
    RATE_LIMIT = "rate_limit"
    EMPTY_RESPONSE = "empty_response"
    TIMEOUT = "timeout"
    PARSE_ERROR = "parse_error"
    API_ERROR = "api_error"
    UNKNOWN = "unknown"


class RetryStrategy:
    """Retry strategy configuration"""
    
    def __init__(self, 
                 max_retries: int = 3,
                 base_delay: int = 5,
                 max_delay: int = 60,
                 exponential_base: float = 2.0):
        """
        Initialize retry strategy
        
        Args:
            max_retries: Maximum number of retry attempts
            base_delay: Base delay in seconds between retries
            max_delay: Maximum delay in seconds
            exponential_base: Base for exponential backoff
        """
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
    
    def get_delay(self, attempt: int) -> int:
        """
        Calculate delay for given attempt number
        
        Args:
            attempt: Attempt number (0-based)
            
        Returns:
            Delay in seconds
        """
        delay = min(
            self.base_delay * (self.exponential_base ** attempt),
            self.max_delay
        )
        return int(delay)


class FailedChunkRetrier:
    """Manages retry logic for failed chunks"""
    
    def __init__(self, api_manager, chunk_distributor):
        """
        Initialize failed chunk retrier
        
        Args:
            api_manager: APIKeyManager instance
            chunk_distributor: ChunkDistributor instance
        """
        self.api_manager = api_manager
        self.chunk_distributor = chunk_distributor
        self.retry_strategy = RetryStrategy()
        
        # Track retry history
        self.retry_history: Dict[str, List[Dict]] = {}
        self.lock = threading.Lock()
        
        # Failure categorization
        self.failure_patterns = {
            FailureType.RATE_LIMIT: ["rate limit", "429", "quota exceeded", "too many requests"],
            FailureType.EMPTY_RESPONSE: ["empty response", "no content", "0 bytes", "blank response"],
            FailureType.TIMEOUT: ["timeout", "timed out", "deadline exceeded"],
            FailureType.PARSE_ERROR: ["json", "parse", "decode", "invalid format"],
            FailureType.API_ERROR: ["api error", "500", "502", "503", "504", "service unavailable"]
        }
    
    def categorize_failure(self, error_message: str) -> FailureType:
        """
        Categorize failure based on error message
        
        Args:
            error_message: Error message string
            
        Returns:
            FailureType enum
        """
        error_lower = error_message.lower()
        
        for failure_type, patterns in self.failure_patterns.items():
            for pattern in patterns:
                if pattern in error_lower:
                    return failure_type
        
        return FailureType.UNKNOWN
    
    def should_retry(self, chunk_id: str, error_message: str, attempt: int) -> bool:
        """
        Determine if a chunk should be retried
        
        Args:
            chunk_id: Chunk identifier
            error_message: Error message
            attempt: Current attempt number
            
        Returns:
            True if should retry
        """
        if attempt >= self.retry_strategy.max_retries:
            return False
        
        failure_type = self.categorize_failure(error_message)
        
        # Always retry rate limits and timeouts
        if failure_type in [FailureType.RATE_LIMIT, FailureType.TIMEOUT]:
            return True
        
        # Retry empty responses up to 2 times
        if failure_type == FailureType.EMPTY_RESPONSE and attempt < 2:
            return True
        
        # Retry API errors
        if failure_type == FailureType.API_ERROR:
            return True
        
        # Don't retry parse errors after first attempt
        if failure_type == FailureType.PARSE_ERROR and attempt > 0:
            return False
        
        # Default: retry unknown errors
        return True
    
    def record_failure(self, chunk_id: str, api_key: str, error_message: str):
        """
        Record a failure for tracking
        
        Args:
            chunk_id: Chunk identifier
            api_key: API key that failed
            error_message: Error message
        """
        with self.lock:
            if chunk_id not in self.retry_history:
                self.retry_history[chunk_id] = []
            
            failure_type = self.categorize_failure(error_message)
            
            self.retry_history[chunk_id].append({
                'timestamp': datetime.now(),
                'api_key': api_key,
                'error': error_message,
                'failure_type': failure_type.value
            })
            
            print(f"  📊 Recorded {failure_type.value} failure for chunk {chunk_id}")
    
    def get_retry_delay(self, chunk_id: str) -> int:
        """
        Get appropriate retry delay for a chunk
        
        Args:
            chunk_id: Chunk identifier
            
        Returns:
            Delay in seconds
        """
        with self.lock:
            if chunk_id not in self.retry_history:
                return self.retry_strategy.base_delay
            
            attempt = len(self.retry_history[chunk_id])
            delay = self.retry_strategy.get_delay(attempt)
            
            # Adjust delay based on last failure type
            if self.retry_history[chunk_id]:
                last_failure = self.retry_history[chunk_id][-1]
                failure_type = FailureType(last_failure['failure_type'])
                
                if failure_type == FailureType.RATE_LIMIT:
                    delay = max(delay, 30)  # Minimum 30s for rate limits
                elif failure_type == FailureType.EMPTY_RESPONSE:
                    delay = max(delay, 10)  # Minimum 10s for empty responses
            
            return delay
    
    def select_retry_api(self, chunk_id: str, excluded_apis: List[str]) -> Optional[str]:
        """
        Select an API for retry, avoiding recently failed ones
        
        Args:
            chunk_id: Chunk identifier
            excluded_apis: APIs to exclude from selection
            
        Returns:
            Selected API key or None
        """
        # Get APIs that haven't been tried for this chunk
        tried_apis = set()
        if chunk_id in self.retry_history:
            tried_apis = {record['api_key'] for record in self.retry_history[chunk_id]}
        
        # Prefer APIs that haven't been tried
        available_apis = []
        for _ in range(len(self.api_manager.api_keys)):
            api_key = self.api_manager.get_next_api()
            if api_key and api_key not in excluded_apis:
                if api_key not in tried_apis:
                    # Prefer untried API
                    return api_key
                available_apis.append(api_key)
        
        # If all APIs have been tried, use any available one
        if available_apis:
            return available_apis[0]
        
        return None
    
    def retry_chunk(self, chunk, process_func: Callable) -> Optional[Dict[str, Any]]:
        """
        Retry a failed chunk with intelligent API selection
        
        Args:
            chunk: ChunkTask to retry
            process_func: Function to process the chunk
            
        Returns:
            Result if successful, None if failed
        """
        chunk_id = chunk.chunk_id
        
        # Check if we should retry
        if not self.should_retry(chunk_id, chunk.error or "", chunk.retry_count):
            print(f"  ⛔ Chunk {chunk_id} exceeded retry limit or not retryable")
            return None
        
        # Get retry delay
        delay = self.get_retry_delay(chunk_id)
        print(f"  ⏱️ Waiting {delay}s before retrying chunk {chunk_id}")
        time.sleep(delay)
        
        # Select API for retry
        excluded_apis = []
        if chunk.assigned_api:
            excluded_apis.append(chunk.assigned_api)
        
        api_key = self.select_retry_api(chunk_id, excluded_apis)
        if not api_key:
            # Wait for API to become available
            print(f"  ⏳ No API available for chunk {chunk_id}, waiting...")
            api_key = self.chunk_distributor.wait_for_available_api(timeout=120)
            if not api_key:
                print(f"  ❌ No API available after waiting for chunk {chunk_id}")
                return None
        
        print(f"  🔁 Retrying chunk {chunk_id} with API ...{api_key[-4:]} (attempt {chunk.retry_count + 1})")
        
        try:
            # Process with new API
            result = process_func(chunk, api_key)
            
            if result and "error" not in result:
                # Success!
                self.api_manager.mark_success(api_key)
                print(f"  ✅ Chunk {chunk_id} succeeded on retry with API ...{api_key[-4:]}")
                return result
            else:
                # Failed again
                error = result.get("error", "Unknown error") if result else "No result"
                self.record_failure(chunk_id, api_key, error)
                
                # Categorize and mark API failure
                failure_type = self.categorize_failure(error)
                self.api_manager.mark_failure(
                    api_key,
                    is_rate_limit=(failure_type == FailureType.RATE_LIMIT),
                    is_empty_response=(failure_type == FailureType.EMPTY_RESPONSE)
                )
                
                return None
                
        except Exception as e:
            error_msg = str(e)
            self.record_failure(chunk_id, api_key, error_msg)
            self.api_manager.mark_failure(api_key)
            print(f"  ❌ Exception during retry of chunk {chunk_id}: {error_msg}")
            return None
    
    def get_retry_statistics(self) -> Dict[str, Any]:
        """
        Get retry statistics
        
        Returns:
            Statistics dictionary
        """
        with self.lock:
            total_retries = sum(len(history) for history in self.retry_history.values())
            
            # Count by failure type
            failure_counts = {}
            for history_list in self.retry_history.values():
                for record in history_list:
                    failure_type = record['failure_type']
                    failure_counts[failure_type] = failure_counts.get(failure_type, 0) + 1
            
            return {
                'total_retried_chunks': len(self.retry_history),
                'total_retry_attempts': total_retries,
                'failure_types': failure_counts,
                'chunks_with_multiple_retries': sum(1 for h in self.retry_history.values() if len(h) > 1)
            }
    
    def clear_history(self):
        """Clear retry history"""
        with self.lock:
            self.retry_history.clear()