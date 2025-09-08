from __future__ import annotations

from dataclasses import dataclass

from storage_manager import StorageManager

from .file_storage import FileStorage
from .result_store import ResultStore
from .progress_tracker import ProgressTracker
from .job_repository import JobRepository


@dataclass
class StorageRegistry:
    """Aggregates storage-related services built over a StorageManager.

    This keeps call sites clean while allowing gradual migration away from the
    monolithic StorageManager.
    """

    files: FileStorage
    results: ResultStore
    progress: ProgressTracker
    jobs: JobRepository

    @classmethod
    def from_storage_manager(cls, sm: StorageManager) -> "StorageRegistry":
        return cls(
            files=FileStorage(sm),
            results=ResultStore(sm),
            progress=ProgressTracker(sm),
            jobs=JobRepository(sm),
        )

