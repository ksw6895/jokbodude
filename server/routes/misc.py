from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from pdf_processor.pdf.cache import clear_global_cache

from ..core import PRO_MODEL_PASSWORD, celery_app
from ..utils import delete_path_contents

router = APIRouter()


@router.get("/")
def read_root():
    return FileResponse("frontend/index.html")


@router.get("/guide")
def read_guide():
    return FileResponse("frontend/guide.html")


@router.get("/styles.css")
def read_stylesheet():
    """Serve the shared frontend stylesheet."""
    return FileResponse("frontend/styles.css")


@router.get("/config")
def get_config(password: Optional[str] = None):
    """Expose server capabilities for the frontend UI."""
    try:
        from config import API_KEYS as _API_KEYS  # type: ignore
        keys_count = len(_API_KEYS) if isinstance(_API_KEYS, list) else (1 if _API_KEYS else 0)
    except Exception:
        keys_count = 0
    models = ["flash"]
    if password and PRO_MODEL_PASSWORD and password == PRO_MODEL_PASSWORD:
        models.append("pro")
    return {"multi_api_available": keys_count > 1, "api_keys_count": keys_count, "models": models}


@router.get("/health")
async def health_check(request: Request):
    """Health check endpoint for monitoring."""
    storage_manager = request.app.state.storage_manager
    checks = {"web": "healthy", "redis": "unknown", "worker": "unknown"}
    try:
        storage_manager.redis_client.ping()
        checks["redis"] = "healthy"
    except Exception as e:
        checks["redis"] = f"unhealthy: {str(e)}"
    try:
        inspector = celery_app.control.inspect()
        active_workers = inspector.active()
        if active_workers:
            checks["worker"] = "healthy"
        else:
            checks["worker"] = "no active workers"
    except Exception as e:
        checks["worker"] = f"unhealthy: {str(e)}"
    all_healthy = all(v == "healthy" for v in checks.values())
    status_code = 200 if all_healthy else 503
    return JSONResponse(content={"status": "healthy" if all_healthy else "degraded", "checks": checks, "timestamp": datetime.now().isoformat()}, status_code=status_code)


@router.post("/admin/cleanup")
def admin_cleanup(
    clear_cache: bool = Query(True),
    clear_debug: bool = Query(True),
    clear_temp_sessions: bool = Query(True),
    older_than_hours: int | None = Query(None, ge=1, description="Only delete files older than this many hours"),
):
    """Administrative cleanup: clear cache and prune debug/temp files."""
    summary: dict[str, dict | bool] = {}
    if clear_cache:
        try:
            clear_global_cache()
            summary["cache_cleared"] = True
        except Exception as e:
            summary["cache_cleared"] = {"error": str(e)}
    if clear_debug:
        try:
            summary["debug_deleted"] = delete_path_contents(Path("output/debug"), older_than_hours)
        except Exception as e:
            summary["debug_deleted"] = {"error": str(e)}
    if clear_temp_sessions:
        try:
            summary["temp_sessions_deleted"] = delete_path_contents(Path("output/temp/sessions"), older_than_hours)
        except Exception as e:
            summary["temp_sessions_deleted"] = {"error": str(e)}
    return summary
