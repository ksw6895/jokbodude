"""
Storage manager for handling file operations across services.
Uses Redis to share file data between web and worker services.
"""

import os
import base64
import json
import redis
from pathlib import Path
from typing import List, Dict, Optional
import hashlib

class StorageManager:
    """Manages file storage using Redis for inter-service communication"""
    
    def __init__(self, redis_url: str = None):
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.redis_client = redis.from_url(self.redis_url)
        self.local_storage = Path(os.getenv("RENDER_STORAGE_PATH", "/tmp/storage"))
        self.local_storage.mkdir(parents=True, exist_ok=True)
        
    def store_file(self, file_path: Path, job_id: str, file_type: str) -> str:
        """Store a file and make it available across services"""
        # Read file content
        with open(file_path, 'rb') as f:
            content = f.read()
        
        # Generate file key
        file_hash = hashlib.md5(content).hexdigest()[:8]
        file_key = f"file:{job_id}:{file_type}:{file_path.name}:{file_hash}"
        
        # Store in Redis with 1 hour expiration
        self.redis_client.setex(
            file_key,
            3600,  # 1 hour TTL
            content
        )
        
        # Also save locally if we have persistent storage
        local_path = self.local_storage / job_id / file_type / file_path.name
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(content)
        
        return file_key
    
    def get_file(self, file_key: str) -> Optional[bytes]:
        """Retrieve file content from Redis"""
        content = self.redis_client.get(file_key)
        return content
    
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
    
    def cleanup_job(self, job_id: str) -> None:
        """Clean up all data related to a job"""
        # Delete all Redis keys for this job
        pattern = f"*:{job_id}:*"
        for key in self.redis_client.scan_iter(match=pattern):
            self.redis_client.delete(key)
        
        # Clean up local storage
        job_dir = self.local_storage / job_id
        if job_dir.exists():
            import shutil
            shutil.rmtree(job_dir)