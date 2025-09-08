from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from storage_manager import StorageManager


class FileStorage:
    """File storage wrapper delegating to StorageManager.

    This component narrows responsibilities to file key creation, retrieval,
    TTL refresh, and safe local saving.
    """

    def __init__(self, sm: StorageManager):
        self._sm = sm

    def store(self, path: Path, job_id: str, kind: str) -> str:
        return self._sm.store_file(path, job_id, kind)

    def fetch(self, key: str) -> Optional[bytes]:
        return self._sm.get_file(key)

    def save_locally(self, key: str, target: Path) -> Path:
        return self._sm.save_file_locally(key, target)

    def verify_available(self, key: str, min_ttl_seconds: int = 60) -> bool:
        return self._sm.verify_file_available(key, min_ttl_seconds=min_ttl_seconds)

    def refresh_ttls(self, keys: List[str], ttl_seconds: int | None = None) -> None:
        self._sm.refresh_ttls(keys, ttl_seconds=ttl_seconds)

