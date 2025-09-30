import os
from pathlib import Path
from celery import Celery
from settings import settings as app_settings

# Core configuration
STORAGE_PATH = Path(app_settings.RENDER_STORAGE_PATH or os.getenv("RENDER_STORAGE_PATH", "persistent_storage"))
REDIS_URL = app_settings.REDIS_URL

# Celery application shared across routes
celery_app = Celery("tasks")
celery_app.config_from_object("celeryconfig")

# File size limit: 100MB
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB in bytes

# Combined upload cap (default 1GB, configurable via MAX_UPLOAD_TOTAL_BYTES)
try:
    _default_total = 1024 * 1024 * 1024  # 1GB in bytes
    MAX_UPLOAD_TOTAL_BYTES = int(os.getenv("MAX_UPLOAD_TOTAL_BYTES", str(_default_total)))
except Exception:
    MAX_UPLOAD_TOTAL_BYTES = 1024 * 1024 * 1024

if MAX_UPLOAD_TOTAL_BYTES < MAX_FILE_SIZE:
    MAX_UPLOAD_TOTAL_BYTES = MAX_FILE_SIZE
