from __future__ import annotations

"""
Centralized application settings using Pydantic Settings (with safe fallback).

This module exposes a single `settings` instance that other modules can import.
It prefers pydantic-settings when available for validation and .env loading,
but gracefully falls back to environment variables if the dependency is missing.
"""

import os
from typing import List, Optional

try:
    # Pydantic v2 compatible settings
    from pydantic_settings import BaseSettings
    from pydantic import Field

    class Settings(BaseSettings):
        # Redis / Storage
        REDIS_URL: str = "redis://localhost:6379/0"
        FILE_TTL_SECONDS: int = 86400
        RENDER_STORAGE_PATH: Optional[str] = None
        TMPDIR: Optional[str] = None

        # Celery limits (long-running by default)
        CELERY_SOFT_TIME_LIMIT: int = 86400  # 24h
        CELERY_TIME_LIMIT: int = 90000       # ~25h

        # Gemini / Model
        GEMINI_MODEL: str = "flash"
        GEMINI_API_KEYS: Optional[List[str]] = Field(default=None)
        GEMINI_API_KEY: Optional[str] = Field(default=None)
        DISABLE_SAFETY_FILTERS: bool = True
        GEMINI_PER_KEY_CONCURRENCY: int = 1
        GEMINI_RATE_LIMIT_COOLDOWN_SECS: int = 30

        # Features / Auth
        ALLOW_DEV_LOGIN: bool = False

        # CBT / Token accounting
        FLASH_TOKENS_PER_CHUNK: int = 1
        PRO_TOKENS_PER_CHUNK: int = 4

        # Retention / Cleanup
        DEBUG_RETENTION_HOURS: int = 168   # 7 days
        RESULT_RETENTION_HOURS: int = 720  # 30 days

        class Config:
            env_file = ".env"
            env_nested_delimiter = "__"

    settings = Settings()

except Exception:
    # Fallback when pydantic-settings is not installed.
    class _FallbackSettings:
        def __init__(self) -> None:
            # Redis / Storage
            self.REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
            self.FILE_TTL_SECONDS = int(os.getenv("FILE_TTL_SECONDS", "86400"))
            self.RENDER_STORAGE_PATH = os.getenv("RENDER_STORAGE_PATH")
            self.TMPDIR = os.getenv("TMPDIR")

            # Celery limits
            self.CELERY_SOFT_TIME_LIMIT = int(os.getenv("CELERY_SOFT_TIME_LIMIT", "86400"))
            self.CELERY_TIME_LIMIT = int(os.getenv("CELERY_TIME_LIMIT", "90000"))

            # Gemini / Model
            self.GEMINI_MODEL = os.getenv("GEMINI_MODEL", "flash")
            _keys = os.getenv("GEMINI_API_KEYS")
            self.GEMINI_API_KEYS = [s.strip() for s in _keys.split(",") if s.strip()] if _keys else None
            self.GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
            self.DISABLE_SAFETY_FILTERS = str(os.getenv("DISABLE_SAFETY_FILTERS", "true")).lower() in {"1","true","yes","on"}
            self.GEMINI_PER_KEY_CONCURRENCY = int(os.getenv("GEMINI_PER_KEY_CONCURRENCY", "1"))
            self.GEMINI_RATE_LIMIT_COOLDOWN_SECS = int(os.getenv("GEMINI_RATE_LIMIT_COOLDOWN_SECS", "30"))

            # Features / Auth
            self.ALLOW_DEV_LOGIN = str(os.getenv("ALLOW_DEV_LOGIN", "false")).lower() in {"1","true","yes","on"}

            # CBT / Token accounting
            self.FLASH_TOKENS_PER_CHUNK = int(os.getenv("FLASH_TOKENS_PER_CHUNK", "1"))
            self.PRO_TOKENS_PER_CHUNK = int(os.getenv("PRO_TOKENS_PER_CHUNK", "4"))

            # Retention / Cleanup
            self.DEBUG_RETENTION_HOURS = int(os.getenv("DEBUG_RETENTION_HOURS", "168"))
            self.RESULT_RETENTION_HOURS = int(os.getenv("RESULT_RETENTION_HOURS", "720"))

    settings = _FallbackSettings()

