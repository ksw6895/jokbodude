"""Lightweight package init.

Avoid importing heavy submodules here to prevent circular imports during early
initialization (e.g., when `storage_manager` imports `server.services.*`).

Downstream modules should import directly, e.g. `from server.main import create_app`
and `from server.core import celery_app`.
"""

__all__: list[str] = []
