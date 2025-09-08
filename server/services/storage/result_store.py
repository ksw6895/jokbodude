from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from storage_manager import StorageManager


class ResultStore:
    """Result storage wrapper over StorageManager (Redis + Disk)."""

    def __init__(self, sm: StorageManager):
        self._sm = sm

    def store(self, job_id: str, result_path: Path) -> str:
        return self._sm.store_result(job_id, result_path)

    def get_result_path(self, job_id: str, filename: str) -> Optional[Path]:
        return self._sm.get_result_path(job_id, filename)

    def read_result_file(self, job_id: str, filename: str) -> Optional[bytes]:
        return self._sm.read_result_file(job_id, filename)

    def list_result_files(self, job_id: str) -> List[str]:
        return self._sm.list_result_files(job_id)

    def delete_result(self, job_id: str, filename: str) -> bool:
        return self._sm.delete_result(job_id, filename)

    def delete_all(self, job_id: str) -> int:
        return self._sm.delete_all_results(job_id)

