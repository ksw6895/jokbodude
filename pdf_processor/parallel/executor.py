"""
Parallel processing executor for PDF analysis.
Handles concurrent processing with thread pool management.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, List, Callable, Optional
from datetime import datetime
import threading

from ..utils.logging import get_logger

logger = get_logger(__name__)

# Try to import tqdm for progress bars
try:
    from tqdm import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False
    logger.info("tqdm not available, progress bars disabled")


class ParallelExecutor:
    """Manages parallel execution of analysis tasks."""
    
    def __init__(self, max_workers: int = 3):
        """
        Initialize the parallel executor.
        
        Args:
            max_workers: Maximum number of worker threads
        """
        self.max_workers = max_workers
        self._lock = threading.Lock()
        
    def execute_parallel(self, task_func: Callable, tasks: List[Any], 
                        task_name: str = "Processing",
                        show_progress: bool = True) -> List[Dict[str, Any]]:
        """
        Execute tasks in parallel.
        
        Args:
            task_func: Function to execute for each task
            tasks: List of task arguments
            task_name: Name for progress display
            show_progress: Whether to show progress bar
            
        Returns:
            List of results in order
        """
        results = [None] * len(tasks)
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all tasks
            future_to_index = {}
            
            for idx, task in enumerate(tasks):
                future = executor.submit(self._execute_task, task_func, task, idx)
                future_to_index[future] = idx
            
            # Process completed tasks
            if show_progress and TQDM_AVAILABLE:
                futures = tqdm(
                    as_completed(future_to_index),
                    total=len(tasks),
                    desc=task_name
                )
            else:
                futures = as_completed(future_to_index)
            
            for future in futures:
                idx = future_to_index[future]
                
                try:
                    result = future.result()
                    results[idx] = result
                except Exception as e:
                    logger.error(f"Task {idx} failed: {str(e)}")
                    results[idx] = {"error": str(e), "task_index": idx}
        
        return results
    
    def _execute_task(self, task_func: Callable, task: Any, idx: int) -> Dict[str, Any]:
        """
        Execute a single task with error handling.
        
        Args:
            task_func: Function to execute
            task: Task arguments
            idx: Task index
            
        Returns:
            Task result
        """
        thread_id = threading.current_thread().name
        start_time = datetime.now()
        
        logger.debug(f"Thread {thread_id}: Starting task {idx}")
        
        try:
            # Execute task
            if isinstance(task, (list, tuple)):
                result = task_func(*task)
            elif isinstance(task, dict):
                result = task_func(**task)
            else:
                result = task_func(task)
            
            elapsed = (datetime.now() - start_time).total_seconds()
            logger.debug(f"Thread {thread_id}: Completed task {idx} in {elapsed:.2f}s")
            
            return result
            
        except Exception as e:
            elapsed = (datetime.now() - start_time).total_seconds()
            logger.error(f"Thread {thread_id}: Task {idx} failed after {elapsed:.2f}s: {str(e)}")
            raise
    
    def map_reduce(self, map_func: Callable, reduce_func: Callable,
                  tasks: List[Any], initial_value: Any = None) -> Any:
        """
        Execute map-reduce pattern in parallel.
        
        Args:
            map_func: Function to map over tasks
            reduce_func: Function to reduce results
            tasks: List of tasks
            initial_value: Initial value for reduction
            
        Returns:
            Reduced result
        """
        # Map phase
        mapped_results = self.execute_parallel(map_func, tasks, "Mapping")
        
        # Filter out errors
        valid_results = [r for r in mapped_results if not isinstance(r, dict) or "error" not in r]
        
        # Reduce phase
        if not valid_results:
            return initial_value
        
        result = initial_value if initial_value is not None else valid_results[0]
        start_idx = 0 if initial_value is not None else 1
        
        for r in valid_results[start_idx:]:
            result = reduce_func(result, r)
        
        return result
    
    def execute_with_retry(self, task_func: Callable, task: Any,
                          max_retries: int = 3) -> Dict[str, Any]:
        """
        Execute a task with retry logic.
        
        Args:
            task_func: Function to execute
            task: Task arguments
            max_retries: Maximum retry attempts
            
        Returns:
            Task result
        """
        last_error = None
        
        for attempt in range(max_retries):
            try:
                return self._execute_task(task_func, task, 0)
            except Exception as e:
                last_error = e
                logger.warning(f"Attempt {attempt + 1}/{max_retries} failed: {str(e)}")
                
                if attempt < max_retries - 1:
                    # Exponential backoff
                    import time
                    time.sleep(2 ** attempt)
        
        # All attempts failed
        return {"error": str(last_error), "max_retries_exceeded": True}
    
    def batch_process(self, items: List[Any], batch_size: int,
                     process_func: Callable) -> List[Any]:
        """
        Process items in batches.
        
        Args:
            items: Items to process
            batch_size: Items per batch
            process_func: Function to process a batch
            
        Returns:
            List of batch results
        """
        batches = []
        
        # Create batches
        for i in range(0, len(items), batch_size):
            batch = items[i:i + batch_size]
            batches.append(batch)
        
        # Process batches in parallel
        return self.execute_parallel(process_func, batches, "Processing batches")