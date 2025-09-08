from __future__ import annotations

from typing import Optional, Dict

from storage_manager import StorageManager


class ProgressTracker:
    """Progress and ETA tracking wrapper over StorageManager."""

    def __init__(self, sm: StorageManager):
        self._sm = sm

    def init(self, job_id: str, total_chunks: int, message: str = "") -> None:
        self._sm.init_progress(job_id, total_chunks, message)

    def set_final(self, job_id: str, message: str = "완료") -> None:
        self._sm.finalize_progress(job_id, message)

    def get(self, job_id: str) -> Optional[Dict]:
        return self._sm.get_progress(job_id)

    def tick(self, job_id: str, inc: int = 1, message: str | None = None) -> None:
        self._sm.increment_chunk(job_id, inc=inc, message=message)

