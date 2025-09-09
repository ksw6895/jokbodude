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
import socket

logger = logging.getLogger(__name__)

class StorageManager:
    """Manages file storage using Redis for inter-service communication"""
    
    def __init__(self, redis_url: str = None):
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.use_local_only = False
        # Local storage for intermediate files (backups/fallbacks)
        try:
            self.local_storage = Path(os.getenv("RENDER_STORAGE_PATH", "/tmp/storage"))
            self.local_storage.mkdir(parents=True, exist_ok=True)
        except Exception:
            # If an absolute path is not writable (e.g., /var/data without a mounted disk), fallback safely
            self.local_storage = Path("output") / "storage"
            try:
                self.local_storage.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
        # Dedicated results directory (persistent when RENDER_STORAGE_PATH is set, or ./output by default)
        _results_env = os.getenv("RENDER_STORAGE_PATH")
        if _results_env:
            try:
                results_root = Path(_results_env)
                (results_root / "results").mkdir(parents=True, exist_ok=True)
                self.results_dir = results_root / "results"
            except Exception:
                # Fallback to project output if creation fails
                self.results_dir = Path("output") / "results"
                self.results_dir.mkdir(parents=True, exist_ok=True)
        else:
            self.results_dir = Path("output") / "results"
            self.results_dir.mkdir(parents=True, exist_ok=True)
        # Configurable TTL for file keys (seconds). Default 24h.
        try:
            self.file_ttl_seconds = max(60, int(os.getenv("FILE_TTL_SECONDS", "86400")))
        except Exception:
            self.file_ttl_seconds = 86400
        
        # Try to connect to Redis with retry
        self._init_redis_connection()
    
    def _init_redis_connection(self, max_retries: int = 3):
        """Initialize Redis connection with retry logic"""
        # Build socket keepalive options similar to celeryconfig
        KEEPALIVE_OPTS = {}
        if hasattr(socket, "TCP_KEEPIDLE"):
            KEEPALIVE_OPTS[socket.TCP_KEEPIDLE] = 1
        if hasattr(socket, "TCP_KEEPINTVL"):
            KEEPALIVE_OPTS[socket.TCP_KEEPINTVL] = 3
        if hasattr(socket, "TCP_KEEPCNT"):
            KEEPALIVE_OPTS[socket.TCP_KEEPCNT] = 5
        if not KEEPALIVE_OPTS and hasattr(socket, "TCP_KEEPALIVE"):
            KEEPALIVE_OPTS[socket.TCP_KEEPALIVE] = 60

        # Timeouts (seconds) with sane defaults to prevent indefinite hangs
        try:
            sock_timeout = float(os.getenv("REDIS_SOCKET_TIMEOUT", "30.0"))
        except Exception:
            sock_timeout = 30.0
        try:
            sock_conn_timeout = float(os.getenv("REDIS_SOCKET_CONNECT_TIMEOUT", "30.0"))
        except Exception:
            sock_conn_timeout = 30.0
        try:
            health_check_interval = int(os.getenv("REDIS_HEALTH_CHECK_INTERVAL", "30"))
        except Exception:
            health_check_interval = 30

        for attempt in range(max_retries):
            try:
                self.redis_client = redis.from_url(
                    self.redis_url,
                    socket_timeout=sock_timeout,
                    socket_connect_timeout=sock_conn_timeout,
                    socket_keepalive=True,
                    socket_keepalive_options=KEEPALIVE_OPTS,
                    health_check_interval=health_check_interval,
                    retry_on_timeout=True,
                )
                # Validate connection once per instance; avoid noisy logs
                self.redis_client.ping()  # Test connection
                # Mark as connected
                self.use_local_only = False
                # Downgrade to debug to prevent per-second spam from hot paths
                logger.debug("Redis connection established")
                return
            except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as e:
                if attempt == max_retries - 1:
                    logger.error(f"Redis connection failed after {max_retries} attempts, using local storage only")
                    self.use_local_only = True
                    self.redis_client = None
                else:
                    wait_time = 2 ** attempt
                    logger.warning(f"Redis connection attempt {attempt + 1} failed, retrying in {wait_time}s")
                    time.sleep(wait_time)

    def _maybe_reconnect(self) -> None:
        """Best-effort reconnection to Redis if previously unavailable.

        This helps long-lived web processes recover when Redis wasn't
        ready at startup. It is intentionally lightweight and only
        attempts reconnection when we are in local-only mode or the
        client appears unavailable.
        """
        # Fast path: already connected and usable
        if not self.use_local_only and self.redis_client is not None:
            try:
                # Cheap health check; errors will fall through to re-init
                self.redis_client.ping()
                return
            except Exception:
                pass
        # Slow path: try to establish a connection now
        try:
            self._init_redis_connection(max_retries=3)
        except Exception:
            # Swallow errors; callers will handle absence gracefully
            pass
    
    def _with_retry(self, func, *args, max_retries: int = 3, **kwargs):
        """Execute a function with exponential backoff retry"""
        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as e:
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
        if self.redis_client is None or self.use_local_only:
            return
        self._with_retry(
            self.redis_client.setex,
            key,
            172800,  # 48 hours TTL
            json.dumps(metadata)
        )
    
    def get_job_metadata(self, job_id: str) -> Optional[Dict]:
        """Retrieve job metadata from Redis"""
        key = f"job:{job_id}:metadata"
        if self.redis_client is None or self.use_local_only:
            return None
        data = self._with_retry(self.redis_client.get, key)
        if data:
            return json.loads(data)
        return None
    
    def store_result(self, job_id: str, result_path: Path) -> str:
        """Store processing result"""
        with open(result_path, 'rb') as f:
            content = f.read()
        
        result_key = f"result:{job_id}:{result_path.name}"
        # Store with longer TTL for results
        if not self.use_local_only and self.redis_client:
            self._with_retry(
                self.redis_client.setex,
                result_key,
                172800,  # 48 hours TTL
                content
            )
        # Optionally persist to disk to avoid overusing Redis memory
        persist_env = os.getenv("PERSIST_RESULTS_ON_DISK", "true").strip().lower()
        persist_to_disk = persist_env in ("1", "true", "yes", "on")
        if persist_to_disk:
            try:
                dest = self.results_dir / job_id / result_path.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(content)
                # Index the on-disk path for lookup
                if not self.use_local_only and self.redis_client:
                    self._with_retry(
                        self.redis_client.setex,
                        f"result_path:{job_id}:{result_path.name}",
                        172800,
                        str(dest)
                    )
            except Exception as e:
                logger.warning(f"Failed to persist result to disk: {e}")
        return result_key
    
    def get_result(self, result_key: str) -> Optional[bytes]:
        """Retrieve result from Redis"""
        if self.redis_client is None or self.use_local_only:
            return None
        return self._with_retry(self.redis_client.get, result_key)

    def get_result_path(self, job_id: str, filename: str) -> Optional[Path]:
        """Return on-disk path for a stored result if available."""
        try:
            # First try indexed path
            v = None
            if not self.use_local_only and self.redis_client:
                v = self._with_retry(self.redis_client.get, f"result_path:{job_id}:{filename}")
                if isinstance(v, (bytes, bytearray)):
                    v = v.decode()
            # Fallback to convention path
            path = Path(v) if v else (self.results_dir / job_id / filename)
            return path if path.exists() else None
        except Exception:
            path = self.results_dir / job_id / filename
            return path if path.exists() else None

    def read_result_file(self, job_id: str, filename: str) -> Optional[bytes]:
        """Read a result file from disk if present."""
        p = self.get_result_path(job_id, filename)
        if p and p.exists():
            try:
                return p.read_bytes()
            except Exception:
                return None
        return None

    def list_result_files(self, job_id: str) -> List[str]:
        """List result filenames for a job (from Redis keys and disk)."""
        names = set()
        try:
            if not self.use_local_only and self.redis_client:
                for key in self.redis_client.scan_iter(match=f"result:{job_id}:*"):
                    ks = key.decode() if isinstance(key, (bytes, bytearray)) else key
                    names.add(ks.split(":")[-1])
        except Exception:
            pass
        # Include on-disk files
        d = self.results_dir / job_id
        if d.exists():
            for p in d.glob("*.pdf"):
                names.add(p.name)
        return sorted(names)

    def delete_result(self, job_id: str, filename: str) -> bool:
        """Delete a single result (Redis + disk). Returns True if anything was removed."""
        removed = False
        # Delete Redis content and index
        if not self.use_local_only and self.redis_client:
            try:
                rk = f"result:{job_id}:{filename}"
                rpk = f"result_path:{job_id}:{filename}"
                self._with_retry(self.redis_client.delete, rk)
                self._with_retry(self.redis_client.delete, rpk)
                removed = True
            except Exception:
                pass
        # Delete file on disk
        p = (self.results_dir / job_id / filename)
        try:
            if p.exists():
                p.unlink()
                removed = True
        except Exception:
            pass
        return removed

    def delete_all_results(self, job_id: str) -> int:
        """Delete all results (Redis + disk) for a job. Returns count removed (best-effort)."""
        count = 0
        # Remove Redis keys
        if not self.use_local_only and self.redis_client:
            try:
                for key in list(self.redis_client.scan_iter(match=f"result:{job_id}:*")):
                    self.redis_client.delete(key)
                    count += 1
                for key in list(self.redis_client.scan_iter(match=f"result_path:{job_id}:*")):
                    self.redis_client.delete(key)
            except Exception:
                pass
        # Remove disk directory
        job_results = self.results_dir / job_id
        if job_results.exists():
            try:
                # Count files before deletion
                count += len(list(job_results.glob("*")))
                import shutil
                shutil.rmtree(job_results)
            except Exception:
                pass
        return count
    
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

    def finalize_progress(self, job_id: str, message: str = "완료") -> None:
        """Set completed_chunks to total_chunks and mark progress 100% with final message.

        This avoids confusing post-completion increments from parallel workers.
        """
        if self.use_local_only or not self.redis_client:
            return
        try:
            key = f"progress:{job_id}"
            pipe = self.redis_client.pipeline()
            pipe.hget(key, "total_chunks")
            total_val, = self._with_retry(pipe.execute)
            try:
                total_chunks = int(total_val) if total_val is not None else 0
            except Exception:
                total_chunks = 0
            mapping = {
                "progress": "100",
                "message": message,
            }
            if total_chunks > 0:
                mapping.update({
                    "completed_chunks": str(total_chunks),
                    "eta_seconds": "0",
                })
            self._with_retry(self.redis_client.hset, key, mapping=mapping)
            self._with_retry(self.redis_client.expire, key, 172800)
        except Exception as e:
            logger.error(f"Failed to finalize progress: {e}")
    
    def get_progress(self, job_id: str) -> Optional[Dict]:
        """Get job progress from Redis"""
        # Attempt lazy reconnect if Redis was unavailable at startup
        if self.use_local_only or not self.redis_client:
            self._maybe_reconnect()
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
                    # Clamp completed to not exceed total for display consistency
                    if total_chunks > 0 and completed_chunks > total_chunks:
                        completed_chunks = total_chunks
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
        """Clean up all data related to a job, including user mappings."""
        # Capture owner before deleting job-scoped keys
        owner_id = None
        try:
            owner_id = self.get_job_owner(job_id)
        except Exception:
            owner_id = None

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
        # Clean up persisted results
        results_dir = self.results_dir / job_id
        if results_dir.exists():
            try:
                import shutil
                shutil.rmtree(results_dir)
            except Exception:
                pass

        # Finally, remove from the user's job list if known
        if owner_id:
            try:
                self.remove_user_job(owner_id, job_id)
            except Exception:
                pass

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

    # --- Optional debug data helpers (JSON) ---
    def store_debug_json(self, job_id: str, name: str, data: Dict) -> Optional[str]:
        """Store small JSON debug blobs (e.g., chunk results) under a namespaced key.

        Returns the Redis key used when successful, otherwise None.
        """
        if self.use_local_only or not self.redis_client:
            return None
        try:
            key = f"debug:{job_id}:{name}"
            payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self._with_retry(self.redis_client.setex, key, 172800, payload)  # 48h TTL
            return key
        except Exception as e:
            logger.warning(f"Failed to store debug json: {e}")
            return None

    def list_debug_keys(self, job_id: str) -> List[str]:
        if self.use_local_only or not self.redis_client:
            return []
        try:
            keys = [k.decode() if isinstance(k, (bytes, bytearray)) else k
                    for k in self.redis_client.scan_iter(match=f"debug:{job_id}:*")]
            return keys
        except Exception:
            return []

    def get_debug_json(self, key: str) -> Optional[Dict]:
        if self.use_local_only or not self.redis_client:
            return None
        try:
            v = self._with_retry(self.redis_client.get, key)
            if not v:
                return None
            s = v.decode() if isinstance(v, (bytes, bytearray)) else v
            return json.loads(s)
        except Exception:
            return None

    # --- Enhanced progress tracking helpers ---
    def init_progress(self, job_id: str, total_chunks: int, message: str = "") -> None:
        """Initialize chunk-based progress with ETA support."""
        from datetime import datetime
        now = time.time()
        if not self.use_local_only and self.redis_client:
            try:
                key = f"progress:{job_id}"
                # If already initialized, avoid shrinking totals and only refresh metadata
                existing = None
                try:
                    existing = self._with_retry(self.redis_client.hgetall, key)
                except Exception:
                    existing = None
                existing_total = 0
                existing_completed = 0
                if existing:
                    try:
                        existing_total = int(existing.get(b'total_chunks') or existing.get('total_chunks') or 0)
                    except Exception:
                        existing_total = 0
                    try:
                        existing_completed = int(existing.get(b'completed_chunks') or existing.get('completed_chunks') or 0)
                    except Exception:
                        existing_completed = 0
                safe_total = max(int(total_chunks), existing_total)
                # Do not reduce completed; keep previous if present
                safe_completed = max(0, existing_completed)
                self._with_retry(
                    self.redis_client.hset,
                    key,
                    mapping={
                        "progress": "0" if safe_completed == 0 else (existing.get(b'progress') if existing else "0"),
                        "message": message or "작업을 시작합니다",
                        "timestamp": datetime.now().isoformat(),
                        "started_at": str(now),
                        "total_chunks": str(safe_total),
                        "completed_chunks": str(safe_completed),
                        "eta_seconds": "-1",
                        "avg_chunk_seconds": "0",
                        # Initialize token counters if not present
                        "job_tokens_spent": (existing.get(b'job_tokens_spent') if existing else "0"),
                    }
                )
                self._with_retry(self.redis_client.expire, f"progress:{job_id}", 172800)
            except Exception as e:
                logger.error(f"Failed to init progress: {e}")

    def set_job_token_budget(self, job_id: str, budget_tokens: int, tokens_per_chunk: int | None = None) -> None:
        """Set a per-job token budget and reset spent counter if missing.

        Stored under progress:{job_id} as fields:
        - job_token_budget
        - job_tokens_spent
        - tokens_per_chunk (optional)
        """
        if self.use_local_only or not self.redis_client:
            return
        try:
            key = f"progress:{job_id}"
            mapping = {
                "job_token_budget": str(max(0, int(budget_tokens))),
            }
            if tokens_per_chunk is not None:
                try:
                    mapping["tokens_per_chunk"] = str(max(0, int(tokens_per_chunk)))
                except Exception:
                    pass
            # Preserve existing spent if present; otherwise initialize to 0
            pipe = self.redis_client.pipeline()
            pipe.hset(key, mapping=mapping)
            pipe.hsetnx(key, "job_tokens_spent", "0")
            self._with_retry(pipe.execute)
            self._with_retry(self.redis_client.expire, key, 172800)
        except Exception as e:
            logger.error(f"Failed to set job token budget: {e}")

    def increment_chunk(self, job_id: str, inc: int = 1, message: Optional[str] = None) -> None:
        """Increment completed chunk count and update ETA and percent (atomically)."""
        if self.use_local_only or not self.redis_client:
            return
        try:
            # Optional CBT token consumption per chunk (best-effort)
            try:
                self._consume_tokens_for_job(job_id, inc)
            except Exception:
                # Token logic should never break progress updates
                pass
            key = f"progress:{job_id}"
            pipe = self.redis_client.pipeline()
            # Atomically increment and fetch needed fields
            pipe.hincrby(key, "completed_chunks", int(inc))
            pipe.hget(key, "total_chunks")
            pipe.hget(key, "started_at")
            completed_val, total_val, started_at_val = self._with_retry(pipe.execute)

            try:
                total_chunks = int(total_val) if total_val is not None else 0
            except Exception:
                total_chunks = 0
            try:
                completed = int(completed_val) if completed_val is not None else 0
            except Exception:
                completed = 0
            try:
                started_at = float(started_at_val) if started_at_val is not None else time.time()
            except Exception:
                started_at = time.time()

            # Clamp completed to not exceed total for display consistency
            completed_clamped = min(completed, total_chunks) if total_chunks > 0 else completed

            now = time.time()
            elapsed = max(0.0, now - started_at)
            avg = (elapsed / completed_clamped) if completed_clamped > 0 else 0.0
            remaining = max(0, total_chunks - completed_clamped)
            eta = avg * remaining if completed_clamped > 0 else -1
            progress = int((completed_clamped / total_chunks) * 100) if total_chunks > 0 else 0
            # Always avoid showing 100% here; finalization will set it explicitly
            progress = min(progress, 99)
            auto_msg = message or f"청크 진행: {completed_clamped}/{total_chunks} 완료"

            self._with_retry(
                self.redis_client.hset,
                key,
                mapping={
                    "completed_chunks": str(completed_clamped),
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

    # --- CBT token accounting ---
    def _consume_tokens_for_job(self, job_id: str, inc: int) -> None:
        """Consume tokens for a job owner based on model and chunk increments.

        - Looks up job metadata for `user_id` and `model`.
        - Consumes FLASH_TOKENS_PER_CHUNK or PRO_TOKENS_PER_CHUNK * inc from the owner's balance.
        - If insufficient balance, marks the job as requested to cancel and updates progress message.
        """
        if self.use_local_only or not self.redis_client:
            return
        try:
            meta = self.get_job_metadata(job_id) or {}
            user_id = (meta or {}).get("user_id")
            model = (meta or {}).get("model") or "flash"
            if not user_id:
                return
            # Read costs and defaults from environment
            try:
                flash_cost = max(0, int(os.getenv("FLASH_TOKENS_PER_CHUNK", "1")))
            except Exception:
                flash_cost = 1
            try:
                pro_cost = max(0, int(os.getenv("PRO_TOKENS_PER_CHUNK", "4")))
            except Exception:
                pro_cost = 4
            per_chunk = pro_cost if str(model).lower() == "pro" else flash_cost
            to_consume = max(0, int(per_chunk) * max(1, int(inc)))
            if to_consume <= 0:
                return

            # Enforce per-job budget if present before charging user
            pre_key = f"progress:{job_id}"
            try:
                pipe = self.redis_client.pipeline()
                pipe.hget(pre_key, "job_token_budget")
                pipe.hget(pre_key, "job_tokens_spent")
                budget_val, spent_val = self._with_retry(pipe.execute)
                budget = int(budget_val) if budget_val is not None else 0
                spent = int(spent_val) if spent_val is not None else 0
            except Exception:
                budget = 0
                spent = 0

            if budget > 0:
                remain = max(0, budget - spent)
                if remain <= 0 or to_consume > remain:
                    # Budget exhausted — request cancel and stop without charging user
                    try:
                        self.request_cancel(job_id)
                        self.update_progress(job_id, (self.get_progress(job_id) or {}).get("progress", 0) or 0,
                                             "예정된 토큰 한도를 사용하여 작업을 중지합니다")
                        from pdf_processor.utils.exceptions import CancelledError as _CE
                        raise _CE("Job token budget exhausted")
                    except Exception:
                        return

            # Charge user tokens
            ok = self.consume_user_tokens(user_id, to_consume)
            if not ok:
                # Request cooperative cancel and update message, then raise to halt current flow
                try:
                    self.request_cancel(job_id)
                    self.update_progress(job_id, (self.get_progress(job_id) or {}).get("progress", 0) or 0,
                                         "토큰 잔액 부족으로 작업이 중지되었습니다")
                    from pdf_processor.utils.exceptions import CancelledError as _CE
                    raise _CE("Insufficient tokens")
                except Exception:
                    return

            # Record job-level spent counter (best-effort)
            try:
                self._with_retry(self.redis_client.hincrby, pre_key, "job_tokens_spent", int(to_consume))
                self._with_retry(self.redis_client.expire, pre_key, 172800)
            except Exception:
                pass
        except Exception:
            # Do not disrupt normal flow
            return

    # --- Public token helpers ---
    def _token_key(self, user_id: str) -> str:
        return f"user:{user_id}:tokens"

    def get_user_tokens(self, user_id: str) -> Optional[int]:
        if self.use_local_only or not self.redis_client:
            return None
        try:
            v = self._with_retry(self.redis_client.get, self._token_key(user_id))
            if v is None:
                return None
            try:
                return int(v if isinstance(v, (bytes, bytearray)) else v)
            except Exception:
                try:
                    return int(v.decode() if isinstance(v, (bytes, bytearray)) else v)
                except Exception:
                    return None
        except Exception:
            return None

    def set_user_tokens(self, user_id: str, amount: int) -> bool:
        if self.use_local_only or not self.redis_client:
            return False
        try:
            amt = max(0, int(amount))
            # long TTL (30d) auto-refresh on write
            self._with_retry(self.redis_client.setex, self._token_key(user_id), 2592000, str(amt))
            return True
        except Exception:
            return False

    def add_user_tokens(self, user_id: str, delta: int) -> Optional[int]:
        if self.use_local_only or not self.redis_client:
            return None
        try:
            # Ensure key exists
            pipe = self.redis_client.pipeline()
            pipe.incrby(self._token_key(user_id), int(delta))
            pipe.expire(self._token_key(user_id), 2592000)
            new_val, _ = self._with_retry(pipe.execute)
            try:
                return int(new_val)
            except Exception:
                return None
        except Exception:
            return None

    def consume_user_tokens(self, user_id: str, amount: int) -> bool:
        """Atomically consume tokens if available. Returns True when successful."""
        if self.use_local_only or not self.redis_client:
            return False
        try:
            amt = max(0, int(amount))
            if amt == 0:
                return True
            key = self._token_key(user_id)
            # Lua script to check-and-decrement without going negative
            script = (
                "local k=KEYS[1]; local a=tonumber(ARGV[1]); "
                "local v=redis.call('GET', k); "
                "if not v then return 0 end; "
                "local n=tonumber(v); if not n or n < a then return -1 end; "
                "n=n-a; redis.call('SET', k, tostring(n)); return n;"
            )
            lua = self.redis_client.register_script(script)
            res = self._with_retry(lua, keys=[key], args=[str(amt)])
            try:
                ival = int(res)
            except Exception:
                return False
            if ival >= 0:
                # extend TTL
                try:
                    self._with_retry(self.redis_client.expire, key, 2592000)
                except Exception:
                    pass
                return True
            return False
        except Exception:
            return False

    # --- Tester allowlist management ---
    def _testers_key(self) -> str:
        return "testers:allowed"

    def add_tester(self, email: str) -> bool:
        """Add an email to the allowed testers set (lowercased)."""
        if self.use_local_only or not self.redis_client:
            return False
        try:
            e = (email or "").strip().lower()
            if not e:
                return False
            self._with_retry(self.redis_client.sadd, self._testers_key(), e)
            # Keep allowlist around for 30 days; auto-refresh on changes
            self._with_retry(self.redis_client.expire, self._testers_key(), 2592000)
            return True
        except Exception:
            return False

    def remove_tester(self, email: str) -> bool:
        """Remove an email from the allowed testers set."""
        if self.use_local_only or not self.redis_client:
            return False
        try:
            e = (email or "").strip().lower()
            if not e:
                return False
            self._with_retry(self.redis_client.srem, self._testers_key(), e)
            return True
        except Exception:
            return False

    def list_testers(self) -> list[str]:
        """Return the dynamic tester allowlist from Redis (lowercased emails)."""
        if self.use_local_only or not self.redis_client:
            return []
        try:
            vals = self._with_retry(self.redis_client.smembers, self._testers_key()) or set()
            out: list[str] = []
            for v in vals:
                if isinstance(v, (bytes, bytearray)):
                    out.append(v.decode())
                else:
                    out.append(str(v))
            return sorted({s.strip().lower() for s in out if s and s.strip()})
        except Exception:
            return []

    def is_tester(self, email: str) -> bool:
        """Check if email is in dynamic tester allowlist (best-effort)."""
        if self.use_local_only or not self.redis_client:
            return False
        try:
            e = (email or "").strip().lower()
            if not e:
                return False
            return bool(self._with_retry(self.redis_client.sismember, self._testers_key(), e))
        except Exception:
            return False

    # --- User profile registry ---
    def _user_profile_key(self, user_id: str) -> str:
        return f"user:{user_id}:profile"

    def _users_index_key(self) -> str:
        return "users:all"

    def _email_index_key(self, email: str) -> str:
        return f"email:{email.lower()}:users"

    def save_user_profile(self, user_id: str, email: str, name: str) -> bool:
        """Persist a minimal user profile and index it for lookup."""
        if self.use_local_only or not self.redis_client:
            return False
        try:
            from datetime import datetime
            profile = {
                "user_id": user_id,
                "email": (email or "").strip(),
                "name": (name or "").strip() or (email or "").strip(),
                "updated_at": datetime.now().isoformat(),
            }
            # Preserve created_at if present
            existing = self._with_retry(self.redis_client.get, self._user_profile_key(user_id))
            if existing:
                try:
                    ex = json.loads(existing.decode() if isinstance(existing, (bytes, bytearray)) else existing)
                    if isinstance(ex, dict) and ex.get("created_at"):
                        profile["created_at"] = ex.get("created_at")
                except Exception:
                    pass
            else:
                profile["created_at"] = profile["updated_at"]
            self._with_retry(self.redis_client.setex, self._user_profile_key(user_id), 2592000, json.dumps(profile))
            # Index user id and email mapping
            self._with_retry(self.redis_client.sadd, self._users_index_key(), user_id)
            self._with_retry(self.redis_client.sadd, self._email_index_key(email), user_id)
            # Expire indices lazily
            self._with_retry(self.redis_client.expire, self._users_index_key(), 2592000)
            self._with_retry(self.redis_client.expire, self._email_index_key(email), 2592000)
            return True
        except Exception:
            return False

    def get_user_profile(self, user_id: str) -> Optional[dict]:
        if self.use_local_only or not self.redis_client:
            return None
        try:
            v = self._with_retry(self.redis_client.get, self._user_profile_key(user_id))
            if not v:
                return None
            try:
                s = v.decode() if isinstance(v, (bytes, bytearray)) else v
                return json.loads(s)
            except Exception:
                return None
        except Exception:
            return None

    def list_users(self, limit: int = 200) -> list[dict]:
        """List up to `limit` user profiles with token balances (best-effort)."""
        if self.use_local_only or not self.redis_client:
            return []
        try:
            ids = list(self._with_retry(self.redis_client.smembers, self._users_index_key()) or [])
            out: list[dict] = []
            for raw in ids[: max(0, int(limit))]:
                uid = raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)
                prof = self.get_user_profile(uid) or {"user_id": uid}
                prof["tokens"] = self.get_user_tokens(uid)
                out.append(prof)
            # Sort by updated_at desc if present
            def _key(p):
                return p.get("updated_at") or p.get("created_at") or ""
            return sorted(out, key=_key, reverse=True)
        except Exception:
            return []

    def find_user_ids_by_email(self, email: str) -> list[str]:
        if self.use_local_only or not self.redis_client:
            return []
        try:
            key = self._email_index_key(email)
            vals = self._with_retry(self.redis_client.smembers, key) or set()
            ids: list[str] = []
            for v in vals:
                ids.append(v.decode() if isinstance(v, (bytes, bytearray)) else str(v))
            return sorted(set(ids))
        except Exception:
            return []
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

    def remove_user_job(self, user_id: str, job_id: str) -> bool:
        """Remove a job from a user's job list.

        Returns True if one or more entries were removed.
        """
        if self.use_local_only or not self.redis_client:
            return False
        try:
            removed_count = self._with_retry(
                self.redis_client.lrem,
                f"user:{user_id}:jobs",
                0,
                job_id,
            )
            try:
                return int(removed_count) > 0
            except Exception:
                return bool(removed_count)
        except Exception as e:
            logger.error(f"Failed to remove user job mapping: {e}")
            return False

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

    # --- Cancellation helpers ---
    def request_cancel(self, job_id: str) -> None:
        """Mark a job as requested to cancel (cooperative check by workers)."""
        if self.use_local_only or not self.redis_client:
            return
        try:
            self._with_retry(self.redis_client.setex, f"job:{job_id}:cancel", 172800, "1")
        except Exception as e:
            logger.error(f"Failed to set cancel flag: {e}")

    def is_cancelled(self, job_id: str) -> bool:
        if self.use_local_only or not self.redis_client:
            return False
        try:
            v = self._with_retry(self.redis_client.get, f"job:{job_id}:cancel")
            return bool(v)
        except Exception:
            return False

    def clear_cancel(self, job_id: str) -> None:
        if self.use_local_only or not self.redis_client:
            return
        try:
            self._with_retry(self.redis_client.delete, f"job:{job_id}:cancel")
        except Exception:
            pass
