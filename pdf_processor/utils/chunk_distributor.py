"""
Chunk Distributor for Multi-API Processing

Distributes PDF chunks across multiple API keys for parallel processing
with load balancing and failure recovery support.
"""

from typing import List, Dict, Any, Tuple, Optional
from dataclasses import dataclass
from datetime import datetime
import threading
from queue import Queue, Empty
import time


@dataclass
class ChunkTask:
    """Represents a chunk processing task"""
    chunk_id: str
    chunk_path: str
    start_page: int
    end_page: int
    lesson_filename: str
    chunk_index: int
    total_chunks: int
    retry_count: int = 0
    max_retries: int = 3
    assigned_api: Optional[str] = None
    status: str = "pending"  # pending, processing, completed, failed
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class ChunkDistributor:
    """Distributes chunks across available API keys"""
    
    def __init__(self, api_manager):
        """
        Initialize chunk distributor
        
        Args:
            api_manager: APIKeyManager instance
        """
        self.api_manager = api_manager
        self.chunk_queue = Queue()
        self.failed_chunks = Queue()
        self.completed_chunks = []
        self.processing_chunks = {}
        self.lock = threading.Lock()
        
        # Track API-specific uploads
        self.api_uploads: Dict[str, List[Any]] = {}
        
        # Statistics
        self.stats = {
            'total_chunks': 0,
            'completed': 0,
            'failed': 0,
            'retried': 0,
            'redistributed': 0
        }
    
    def add_chunks(self, chunks: List[Tuple], lesson_filename: str):
        """
        Add chunks to the processing queue
        
        Args:
            chunks: List of (chunk_path, start_page, end_page) tuples
            lesson_filename: Original lesson filename
        """
        self.stats['total_chunks'] = len(chunks)
        
        for idx, (chunk_path, start_page, end_page) in enumerate(chunks):
            chunk_task = ChunkTask(
                chunk_id=f"{lesson_filename}_chunk_{idx}",
                chunk_path=chunk_path,
                start_page=start_page,
                end_page=end_page,
                lesson_filename=lesson_filename,
                chunk_index=idx,
                total_chunks=len(chunks)
            )
            self.chunk_queue.put(chunk_task)
            print(f"  Added chunk {idx+1}/{len(chunks)} to queue: pages {start_page}-{end_page}")
    
    def get_next_chunk(self) -> Optional[ChunkTask]:
        """
        Get next chunk from queue
        
        Returns:
            ChunkTask or None if queue is empty
        """
        try:
            # First try failed chunks that need redistribution
            if not self.failed_chunks.empty():
                chunk = self.failed_chunks.get_nowait()
                self.stats['redistributed'] += 1
                print(f"  📋 Redistributing failed chunk: {chunk.chunk_id}")
                return chunk
            
            # Then get new chunks
            return self.chunk_queue.get_nowait()
        except Empty:
            return None
    
    def assign_chunk_to_api(self, chunk: ChunkTask, api_key: str) -> bool:
        """
        Assign a chunk to a specific API key
        
        Args:
            chunk: ChunkTask to assign
            api_key: API key to assign to
            
        Returns:
            True if assignment successful
        """
        with self.lock:
            # Check if API is still available
            if self.api_manager.get_available_count() == 0:
                print(f"  ⚠️ No available APIs for chunk {chunk.chunk_id}")
                return False
            
            chunk.assigned_api = api_key
            chunk.status = "processing"
            self.processing_chunks[chunk.chunk_id] = chunk
            
            # Track uploads per API
            if api_key not in self.api_uploads:
                self.api_uploads[api_key] = []
            
            print(f"  📌 Assigned chunk {chunk.chunk_id} to API ...{api_key[-4:]}")
            return True
    
    def mark_chunk_completed(self, chunk: ChunkTask, result: Dict[str, Any]):
        """
        Mark a chunk as successfully completed
        
        Args:
            chunk: Completed chunk
            result: Processing result
        """
        with self.lock:
            chunk.status = "completed"
            chunk.result = result
            
            if chunk.chunk_id in self.processing_chunks:
                del self.processing_chunks[chunk.chunk_id]
            
            self.completed_chunks.append(chunk)
            self.stats['completed'] += 1
            
            print(f"  ✅ Chunk {chunk.chunk_id} completed ({self.stats['completed']}/{self.stats['total_chunks']})")
    
    def mark_chunk_failed(self, chunk: ChunkTask, error: str, api_key: str):
        """
        Mark a chunk as failed and handle retry/redistribution
        
        Args:
            chunk: Failed chunk
            error: Error message
            api_key: API key that failed
        """
        with self.lock:
            chunk.error = error
            chunk.retry_count += 1
            
            if chunk.chunk_id in self.processing_chunks:
                del self.processing_chunks[chunk.chunk_id]
            
            # Check if we should retry
            if chunk.retry_count < chunk.max_retries:
                chunk.status = "pending"
                chunk.assigned_api = None
                self.failed_chunks.put(chunk)
                self.stats['retried'] += 1
                print(f"  🔄 Chunk {chunk.chunk_id} failed (attempt {chunk.retry_count}/{chunk.max_retries}), queuing for retry")
                
                # Mark API failure
                is_empty = "empty response" in error.lower()
                is_rate_limit = "rate" in error.lower() or "429" in error
                self.api_manager.mark_failure(api_key, is_rate_limit=is_rate_limit, is_empty_response=is_empty)
            else:
                chunk.status = "failed"
                self.stats['failed'] += 1
                print(f"  ❌ Chunk {chunk.chunk_id} permanently failed after {chunk.max_retries} attempts")
    
    def redistribute_failed_chunks(self) -> int:
        """
        Redistribute all failed chunks to available APIs
        
        Returns:
            Number of chunks redistributed
        """
        redistributed = 0
        temp_queue = []
        
        # Get all failed chunks
        while not self.failed_chunks.empty():
            try:
                chunk = self.failed_chunks.get_nowait()
                temp_queue.append(chunk)
            except Empty:
                break
        
        # Try to redistribute
        for chunk in temp_queue:
            api_key = self.api_manager.get_next_api()
            if api_key:
                chunk.assigned_api = None
                chunk.status = "pending"
                self.chunk_queue.put(chunk)
                redistributed += 1
                print(f"  ♻️ Redistributed chunk {chunk.chunk_id}")
            else:
                # No API available, put back in failed queue
                self.failed_chunks.put(chunk)
        
        return redistributed
    
    def get_progress(self) -> Dict[str, Any]:
        """
        Get current processing progress
        
        Returns:
            Progress statistics
        """
        with self.lock:
            pending = self.chunk_queue.qsize()
            failed = self.failed_chunks.qsize()
            processing = len(self.processing_chunks)
            
            return {
                'total': self.stats['total_chunks'],
                'completed': self.stats['completed'],
                'failed': self.stats['failed'],
                'pending': pending,
                'processing': processing,
                'retried': self.stats['retried'],
                'redistributed': self.stats['redistributed'],
                'progress_pct': (self.stats['completed'] / self.stats['total_chunks'] * 100) if self.stats['total_chunks'] > 0 else 0
            }
    
    def wait_for_available_api(self, timeout: int = 60) -> Optional[str]:
        """
        Wait for an available API with timeout
        
        Args:
            timeout: Maximum wait time in seconds
            
        Returns:
            Available API key or None
        """
        start_time = time.time()
        wait_interval = 5  # Check every 5 seconds
        
        while time.time() - start_time < timeout:
            api_key = self.api_manager.get_next_api()
            if api_key:
                return api_key
            
            # Show waiting status
            remaining = int(timeout - (time.time() - start_time))
            print(f"  ⏳ Waiting for available API... ({remaining}s remaining)")
            time.sleep(wait_interval)
        
        return None
    
    def cleanup_api_uploads(self, api_key: str):
        """
        Clean up files uploaded by a specific API
        
        Args:
            api_key: API key whose uploads to clean
        """
        if api_key in self.api_uploads:
            for file_ref in self.api_uploads[api_key]:
                try:
                    # Cleanup logic here - depends on file manager implementation
                    pass
                except Exception as e:
                    print(f"  Warning: Failed to cleanup file for API ...{api_key[-4:]}: {e}")
            
            self.api_uploads[api_key].clear()
    
    def get_completed_results(self) -> List[Dict[str, Any]]:
        """
        Get all completed chunk results
        
        Returns:
            List of completed results
        """
        with self.lock:
            return [chunk.result for chunk in self.completed_chunks if chunk.result]
    
    def has_pending_work(self) -> bool:
        """
        Check if there are pending or processing chunks
        
        Returns:
            True if work remains
        """
        with self.lock:
            return (
                not self.chunk_queue.empty() or 
                not self.failed_chunks.empty() or 
                len(self.processing_chunks) > 0
            )