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

# File size limit: 50MB
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB in bytes
