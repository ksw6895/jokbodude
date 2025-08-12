"""Retry handling utilities for API calls"""

import time
from typing import Any, Callable, Optional
from processing_config import ProcessingConfig


class RetryHandler:
    """Handles retry logic for API calls with exponential backoff"""
    
    @staticmethod
    def execute_with_retry(
        func: Callable,
        content: Any,
        max_retries: int = ProcessingConfig.MAX_RETRIES,
        backoff_factor: float = ProcessingConfig.BACKOFF_FACTOR
    ) -> Any:
        """Execute function with retry logic
        
        Args:
            func: Function to execute
            content: Content to pass to function
            max_retries: Maximum number of retry attempts
            backoff_factor: Exponential backoff factor
            
        Returns:
            Result from successful function execution
            
        Raises:
            Exception: If all retry attempts fail
        """
        last_exception = None
        
        for attempt in range(max_retries):
            try:
                response = func(content)
                
                # Check for empty response
                if hasattr(response, 'text') and not response.text:
                    raise ValueError("Empty response received from API")
                
                return response
                
            except Exception as e:
                last_exception = e
                
                if attempt < max_retries - 1:
                    wait_time = backoff_factor ** attempt
                    print(f"재시도 중... (시도 {attempt + 1}/{max_retries}, {wait_time}초 대기)")
                    time.sleep(wait_time)
                else:
                    print(f"최대 재시도 횟수 도달: {str(e)}")
        
        # If all retries failed, raise the last exception
        if last_exception:
            raise last_exception
        else:
            raise Exception("Unknown error in retry handler")