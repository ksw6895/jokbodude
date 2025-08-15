"""
Storage manager for handling file operations across services.
Uses Redis to share file data between web and worker services.
"""

import os
import tempfile
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
        # Configurable TTL for file keys (seconds). Default 24h.
        try:
            self.file_ttl_seconds = max(60, int(os.getenv("FILE_TTL_SECONDS", "86400")))
        except Exception:
            self.file_ttl_seconds = 86400
        
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
                # Set expiration using configured TTL
                self._with_retry(self.redis_client.expire, file_key, self.file_ttl_seconds)
            except Exception as e:
                logger.error(f"Failed to store in Redis, falling back to local: {e}")
                self.use_local_only = True
        
        # Always save locally as backup (disabled to prevent disk growth)
        # local_path = self.local_storage / job_id / file_type / file_path.name
        # local_path.parent.mkdir(parents=True, exist_ok=True)
        # local_path.write_bytes(content)
        
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

        # Safety: only allow writes under the system temp directory
        tmp_root = Path(os.getenv("TMPDIR", tempfile.gettempdir())).resolve()
        resolved_target = target_path.resolve()
        try:
            resolved_target.relative_to(tmp_root)
        except ValueError:
            raise ValueError(f"Refusing to write outside temp dir: {resolved_target} not under {tmp_root}")

        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(content)
        return target_path

    # --- TTL helpers and verification ---
    def refresh_ttl(self, file_key: str, ttl_seconds: Optional[int] = None) -> None:
        """Refresh TTL on a single file key (noop if Redis/local-only unavailable)."""
        if self.use_local_only or not self.redis_client:
            return
        try:
            ttl = int(ttl_seconds) if ttl_seconds is not None else self.file_ttl_seconds
            self._with_retry(self.redis_client.expire, file_key, ttl)
        except Exception as e:
            logger.warning(f"Failed to refresh TTL for {file_key}: {e}")

    def refresh_ttls(self, file_keys: List[str], ttl_seconds: Optional[int] = None) -> None:
        """Refresh TTL on multiple file keys efficiently."""
        if self.use_local_only or not self.redis_client:
            return
        try:
            ttl = int(ttl_seconds) if ttl_seconds is not None else self.file_ttl_seconds
            pipe = self.redis_client.pipeline()
            for k in file_keys:
                pipe.expire(k, ttl)
            pipe.execute()
        except Exception as e:
            logger.warning(f"Failed to refresh TTLs: {e}")

    def verify_file_available(self, file_key: str, min_ttl_seconds: int = 60) -> bool:
        """Check if file exists in Redis and TTL is healthy.

        Returns True if the key exists and TTL is either >= min_ttl_seconds or persistent (-1).
        """
        if self.use_local_only or not self.redis_client:
            return False
        try:
            hlen = self._with_retry(self.redis_client.hlen, file_key)
            if not hlen:
                return False
            ttl = self._with_retry(self.redis_client.ttl, file_key)
            if ttl == -2:
                return False
            if ttl == -1:
                return True
            return ttl >= max(0, int(min_ttl_seconds))
        except Exception:
            return False
    
    def store_job_metadata(self, job_id: str, metadata: Dict) -> None:
        """Store job metadata in Redis"""
        key = f"job:{job_id}:metadata"
        # Keep metadata longer to support long-running tasks
        self.redis_client.setex(
            key,
            172800,  # 48 hours TTL
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
            172800,  # 48 hours TTL
            content
        )
        return result_key
    
    def get_result(self, result_key: str) -> Optional[bytes]:
        """Retrieve result from Redis"""
        return self.redis_client.get(result_key)
    
    def update_progress(self, job_id: str, progress: int, message: str) -> None:
        """Update job progress in Redis (percent + message)."""
        from datetime import datetime
        if not self.use_local_only and self.redis_client:
            try:
                self._with_retry(
                    self.redis_client.hset,
                    f"progress:{job_id}",
                    mapping={
                        "progress": str(progress),
                        "message": message,
                        "timestamp": datetime.now().isoformat(),
                    }
                )
                # Extend expiration to match long jobs
                self._with_retry(self.redis_client.expire, f"progress:{job_id}", 172800)
            except Exception as e:
                logger.error(f"Failed to update progress: {e}")
    
    def get_progress(self, job_id: str) -> Optional[Dict]:
        """Get job progress from Redis"""
        if not self.use_local_only and self.redis_client:
            try:
                data = self._with_retry(self.redis_client.hgetall, f"progress:{job_id}")
                if data:
                    def _get(key, default=b''):
                        v = data.get(key) or data.get(key if isinstance(key, str) else key)
                        return v
                    def _decode(v):
                        return v.decode() if isinstance(v, (bytes, bytearray)) else v
                    progress = int(_get(b'progress', b'0') or _get('progress', '0') or 0)
                    message = _decode(_get(b'message', b'') or _get('message', '')) or ''
                    timestamp = _decode(_get(b'timestamp', b'') or _get('timestamp', '')) or ''
                    total_chunks = int(_get(b'total_chunks', b'0') or _get('total_chunks', '0') or 0)
                    completed_chunks = int(_get(b'completed_chunks', b'0') or _get('completed_chunks', '0') or 0)
                    eta_seconds = float(_get(b'eta_seconds', b'-1') or _get('eta_seconds', '-1') or -1)
                    avg_chunk_seconds = float(_get(b'avg_chunk_seconds', b'0') or _get('avg_chunk_seconds', '0') or 0)
                    return {
                        "progress": progress,
                        "message": message,
                        "timestamp": timestamp,
                        "total_chunks": total_chunks,
                        "completed_chunks": completed_chunks,
                        "eta_seconds": eta_seconds,
                        "avg_chunk_seconds": avg_chunk_seconds,
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

    # --- Enhanced progress tracking helpers ---
    def init_progress(self, job_id: str, total_chunks: int, message: str = "") -> None:
        """Initialize chunk-based progress with ETA support."""
        from datetime import datetime
        now = time.time()
        if not self.use_local_only and self.redis_client:
            try:
                self._with_retry(
                    self.redis_client.hset,
                    f"progress:{job_id}",
                    mapping={
                        "progress": "0",
                        "message": message or "작업을 시작합니다",
                        "timestamp": datetime.now().isoformat(),
                        "started_at": str(now),
                        "total_chunks": str(int(total_chunks)),
                        "completed_chunks": "0",
                        "eta_seconds": "-1",
                        "avg_chunk_seconds": "0",
                    }
                )
                self._with_retry(self.redis_client.expire, f"progress:{job_id}", 172800)
            except Exception as e:
                logger.error(f"Failed to init progress: {e}")

    def increment_chunk(self, job_id: str, inc: int = 1, message: Optional[str] = None) -> None:
        """Increment completed chunk count and update ETA and percent."""
        if self.use_local_only or not self.redis_client:
            return
        try:
            key = f"progress:{job_id}"
            data = self._with_retry(self.redis_client.hgetall, key) or {}
            def _get_num(name: str, default: float = 0.0) -> float:
                v = data.get(name.encode()) or data.get(name)
                try:
                    return float(v) if v is not None else default
                except Exception:
                    return default
            total_chunks = int(_get_num('total_chunks', 0))
            completed = int(_get_num('completed_chunks', 0)) + int(inc)
            started_at = _get_num('started_at', time.time())
            now = time.time()
            elapsed = max(0.0, now - started_at)
            avg = (elapsed / completed) if completed > 0 else 0.0
            remaining = max(0, total_chunks - completed)
            eta = avg * remaining if completed > 0 else -1
            progress = int((completed / total_chunks) * 100) if total_chunks > 0 else 0
            # Avoid showing 100% until finalization elsewhere
            progress = min(progress, 99 if remaining > 0 else 100)
            auto_msg = message or f"청크 진행: {completed}/{total_chunks} 완료"
            self._with_retry(
                self.redis_client.hset,
                key,
                mapping={
                    "completed_chunks": str(completed),
                    "total_chunks": str(total_chunks),
                    "progress": str(progress),
                    "eta_seconds": f"{eta:.2f}",
                    "avg_chunk_seconds": f"{avg:.2f}",
                    "message": auto_msg,
                }
            )
            self._with_retry(self.redis_client.expire, key, 172800)
        except Exception as e:
            logger.error(f"Failed to increment chunk progress: {e}")

    # --- User → jobs mapping helpers ---
    def add_user_job(self, user_id: str, job_id: str) -> None:
        """Associate a job with a user id."""
        if self.use_local_only or not self.redis_client:
            return
        try:
            self._with_retry(self.redis_client.lpush, f"user:{user_id}:jobs", job_id)
            self._with_retry(self.redis_client.expire, f"user:{user_id}:jobs", 2592000)  # 30 days
            self._with_retry(self.redis_client.setex, f"job:{job_id}:user", 2592000, user_id)
        except Exception as e:
            logger.error(f"Failed to add user job mapping: {e}")

    def get_user_jobs(self, user_id: str, limit: int = 50) -> List[str]:
        if self.use_local_only or not self.redis_client:
            return []
        try:
            jobs = self._with_retry(self.redis_client.lrange, f"user:{user_id}:jobs", 0, max(0, limit - 1)) or []
            return [j.decode() if isinstance(j, (bytes, bytearray)) else j for j in jobs]
        except Exception as e:
            logger.error(f"Failed to fetch user jobs: {e}")
            return []

    def get_job_owner(self, job_id: str) -> Optional[str]:
        if self.use_local_only or not self.redis_client:
            return None
        try:
            v = self._with_retry(self.redis_client.get, f"job:{job_id}:user")
            return v.decode() if isinstance(v, (bytes, bytearray)) else v
        except Exception:
            return None

    def set_job_task(self, job_id: str, task_id: str) -> None:
        if self.use_local_only or not self.redis_client:
            return
        try:
            self._with_retry(self.redis_client.setex, f"job:{job_id}:task", 172800, task_id)
        except Exception as e:
            logger.error(f"Failed to set job task mapping: {e}")

    def get_job_task(self, job_id: str) -> Optional[str]:
        if self.use_local_only or not self.redis_client:
            return None
        try:
            v = self._with_retry(self.redis_client.get, f"job:{job_id}:task")
            return v.decode() if isinstance(v, (bytes, bytearray)) else v
        except Exception:
            return None
