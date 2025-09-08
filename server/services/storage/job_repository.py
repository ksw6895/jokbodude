from __future__ import annotations

from typing import Dict, List, Optional

from storage_manager import StorageManager


class JobRepository:
    """Job metadata and user-job mapping wrapper."""

    def __init__(self, sm: StorageManager):
        self._sm = sm

    # Metadata
    def store_metadata(self, job_id: str, metadata: Dict) -> None:
        self._sm.store_job_metadata(job_id, metadata)

    def get_metadata(self, job_id: str) -> Optional[Dict]:
        return self._sm.get_job_metadata(job_id)

    # Owner / mapping
    def add_user_job(self, user_id: str, job_id: str) -> None:
        self._sm.add_user_job(user_id, job_id)

    def get_user_jobs(self, user_id: str, limit: int = 50) -> List[str]:
        return self._sm.get_user_jobs(user_id, limit=limit)

    def get_job_owner(self, job_id: str) -> Optional[str]:
        return self._sm.get_job_owner(job_id)

    def remove_user_job(self, user_id: str, job_id: str) -> bool:
        return self._sm.remove_user_job(user_id, job_id)

    def set_job_task(self, job_id: str, task_id: str) -> None:
        self._sm.set_job_task(job_id, task_id)

    def get_job_task(self, job_id: str) -> Optional[str]:
        return self._sm.get_job_task(job_id)

