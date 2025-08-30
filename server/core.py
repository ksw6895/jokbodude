import os
from pathlib import Path
from celery import Celery

# Core configuration
STORAGE_PATH = Path(os.getenv("RENDER_STORAGE_PATH", "persistent_storage"))
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Celery application shared across routes
celery_app = Celery("tasks")
celery_app.config_from_object("celeryconfig")

# File size limit: 50MB
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB in bytes
