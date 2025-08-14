"""
Storage manager for handling file operations across services.
Uses Redis to share file data between web and worker services.
"""

import os
import base64
import json
import redis
import time
import logging
import zlib
from pathlib import Path
from typing import List, Dict, Optional
import hashlib

logger = logging.getLogger(__name__)

class StorageManager:
    """Manages file storage using Redis for inter-service communication"""
    
    def __init__(self, redis_url: str = None):
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.use_local_only = False
        self.local_storage = Path(os.getenv("RENDER_STORAGE_PATH", "/tmp/storage"))
        self.local_storage.mkdir(parents=True, exist_ok=True)
        
        # Try to connect to Redis with retry
        self._init_redis_connection()
    
    def _init_redis_connection(self, max_retries: int = 3):
        """Initialize Redis connection with retry logic"""
        for attempt in range(max_retries):
            try:
                self.redis_client = redis.from_url(self.redis_url)
                self.redis_client.ping()  # Test connection
                logger.info("Redis connection established")
                return
            except redis.ConnectionError as e:
                if attempt == max_retries - 1:
                    logger.error(f"Redis connection failed after {max_retries} attempts, using local storage only")
                    self.use_local_only = True
                    self.redis_client = None
                else:
                    wait_time = 2 ** attempt
                    logger.warning(f"Redis connection attempt {attempt + 1} failed, retrying in {wait_time}s")
                    time.sleep(wait_time)
    
    def _with_retry(self, func, *args, max_retries: int = 3, **kwargs):
        """Execute a function with exponential backoff retry"""
        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except (redis.ConnectionError, redis.TimeoutError) as e:
                if attempt == max_retries - 1:
                    logger.error(f"Operation failed after {max_retries} attempts: {e}")
                    raise
                wait_time = 2 ** attempt
                logger.warning(f"Operation failed, retrying in {wait_time}s: {e}")
                time.sleep(wait_time)
        
    def store_file(self, file_path: Path, job_id: str, file_type: str) -> str:
        """Store a file and make it available across services"""
        # Read file content
        with open(file_path, 'rb') as f:
            content = f.read()
        
        # Generate file key
        file_hash = hashlib.md5(content).hexdigest()[:8]
        file_key = f"file:{job_id}:{file_type}:{file_path.name}:{file_hash}"
        
        # Try compression for large files
        if len(content) > 1024 * 1024:  # > 1MB
            compressed = zlib.compress(content, level=6)
            compression_ratio = len(compressed) / len(content)
            
            if compression_ratio < 0.9:  # 10% or better compression
                logger.info(f"Compressed {file_path.name}: {len(content)} -> {len(compressed)} bytes ({compression_ratio:.1%})")
                content_to_store = compressed
                is_compressed = True
            else:
                content_to_store = content
                is_compressed = False
        else:
            content_to_store = content
            is_compressed = False
        
        # Store in Redis with retry if not in local-only mode
        if not self.use_local_only and self.redis_client:
            try:
                # Store with metadata
                self._with_retry(
                    self.redis_client.hset,
                    file_key,
                    mapping={
                        "data": content_to_store,
                        "compressed": str(is_compressed),
                        "original_size": str(len(content))
                    }
                )
                # Set expiration
                self._with_retry(self.redis_client.expire, file_key, 3600)
            except Exception as e:
                logger.error(f"Failed to store in Redis, falling back to local: {e}")
                self.use_local_only = True
        
        # Always save locally as backup
        local_path = self.local_storage / job_id / file_type / file_path.name
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(content)
        
        return file_key
    
    def get_file(self, file_key: str) -> Optional[bytes]:
        """Retrieve file content from Redis or local storage"""
        if not self.use_local_only and self.redis_client:
            try:
                # Try to get from Redis
                data = self._with_retry(self.redis_client.hgetall, file_key)
                if data:
                    content = data.get(b'data') or data.get('data')
                    compressed = data.get(b'compressed') or data.get('compressed')
                    
                    if content:
                        # Decompress if needed
                        if compressed and (compressed == b'True' or compressed == 'True'):
                            content = zlib.decompress(content)
                        return content
                
                # Fallback to simple get for backward compatibility
                content = self._with_retry(self.redis_client.get, file_key)
                if content:
                    return content
            except Exception as e:
                logger.error(f"Failed to get from Redis: {e}")
        
        # Fallback to local storage
        parts = file_key.split(":")
        if len(parts) >= 4:
            job_id = parts[1]
            file_type = parts[2]
            filename = parts[3]
            local_path = self.local_storage / job_id / file_type / filename
            if local_path.exists():
                return local_path.read_bytes()
        
        return None
    
    def save_file_locally(self, file_key: str, target_path: Path) -> Path:
        """Save a file from Redis to local filesystem"""
        content = self.get_file(file_key)
        if not content:
            raise ValueError(f"File not found in Redis: {file_key}")
        
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(content)
        return target_path
    
    def store_job_metadata(self, job_id: str, metadata: Dict) -> None:
        """Store job metadata in Redis"""
        key = f"job:{job_id}:metadata"
        self.redis_client.setex(
            key,
            3600,  # 1 hour TTL
            json.dumps(metadata)
        )
    
    def get_job_metadata(self, job_id: str) -> Optional[Dict]:
        """Retrieve job metadata from Redis"""
        key = f"job:{job_id}:metadata"
        data = self.redis_client.get(key)
        if data:
            return json.loads(data)
        return None
    
    def store_result(self, job_id: str, result_path: Path) -> str:
        """Store processing result"""
        with open(result_path, 'rb') as f:
            content = f.read()
        
        result_key = f"result:{job_id}:{result_path.name}"
        # Store with longer TTL for results
        self.redis_client.setex(
            result_key,
            7200,  # 2 hours TTL
            content
        )
        return result_key
    
    def get_result(self, result_key: str) -> Optional[bytes]:
        """Retrieve result from Redis"""
        return self.redis_client.get(result_key)
    
    def update_progress(self, job_id: str, progress: int, message: str) -> None:
        """Update job progress in Redis"""
        from datetime import datetime
        
        if not self.use_local_only and self.redis_client:
            try:
                self._with_retry(
                    self.redis_client.hset,
                    f"progress:{job_id}",
                    mapping={
                        "progress": str(progress),
                        "message": message,
                        "timestamp": datetime.now().isoformat()
                    }
                )
                # Set expiration
                self._with_retry(self.redis_client.expire, f"progress:{job_id}", 3600)
            except Exception as e:
                logger.error(f"Failed to update progress: {e}")
    
    def get_progress(self, job_id: str) -> Optional[Dict]:
        """Get job progress from Redis"""
        if not self.use_local_only and self.redis_client:
            try:
                data = self._with_retry(self.redis_client.hgetall, f"progress:{job_id}")
                if data:
                    return {
                        "progress": int(data.get(b'progress', 0) or data.get('progress', 0)),
                        "message": (data.get(b'message', b'').decode() if isinstance(data.get(b'message'), bytes) 
                                   else data.get('message', '')),
                        "timestamp": (data.get(b'timestamp', b'').decode() if isinstance(data.get(b'timestamp'), bytes)
                                    else data.get('timestamp', ''))
                    }
            except Exception as e:
                logger.error(f"Failed to get progress: {e}")
        return None
    
    def cleanup_job(self, job_id: str) -> None:
        """Clean up all data related to a job"""
        if not self.use_local_only and self.redis_client:
            try:
                # Delete all Redis keys for this job
                pattern = f"*:{job_id}:*"
                for key in self.redis_client.scan_iter(match=pattern):
                    self.redis_client.delete(key)
            except Exception as e:
                logger.error(f"Failed to cleanup Redis keys: {e}")
        
        # Clean up local storage
        job_dir = self.local_storage / job_id
        if job_dir.exists():
            import shutil
            shutil.rmtree(job_dir)

    def close(self) -> None:
        """Close Redis connections and release resources."""
        try:
            if self.redis_client is not None:
                pool = getattr(self.redis_client, "connection_pool", None)
                if pool is not None:
                    pool.disconnect()
                self.redis_client = None
                logger.info("StorageManager Redis connections closed")
        except Exception as e:
            logger.warning(f"Error while closing StorageManager: {e}")
