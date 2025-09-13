import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, Depends
from fastapi.responses import FileResponse, JSONResponse

from pdf_processor.pdf.cache import clear_global_cache

from ..core import celery_app
from ..utils import delete_path_contents

router = APIRouter()

def _admin_emails() -> set[str]:
    raw = os.getenv("ADMIN_EMAILS", "").strip()
    if not raw:
        return set()
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def _is_admin_email(email: Optional[str]) -> bool:
    return (email or "").strip().lower() in _admin_emails()


def _require_admin(password: Optional[str], user: Optional[dict]) -> None:
    """Allow admin if user email is in ADMIN_EMAILS; otherwise require ADMIN_PASSWORD."""
    if user and _is_admin_email(user.get("email")):
        return
    admin_pw = os.getenv("ADMIN_PASSWORD", "")
    if not admin_pw:
        raise HTTPException(status_code=403, detail="Admin not authorized")
    if (password or "") != admin_pw:
        raise HTTPException(status_code=403, detail="Invalid admin password")


@router.get("/")
def read_root():
    return FileResponse("frontend/index.html")


@router.get("/guide")
def read_guide():
    return FileResponse("frontend/guide.html")

@router.get("/profile")
def read_profile():
    return FileResponse("frontend/profile.html")


@router.get("/styles.css")
def read_stylesheet():
    """Serve the main frontend stylesheet for the homepage.

    The homepage references `/styles.css?v=...`, which maps here regardless of query params.
    """
    return FileResponse("frontend/styles.css")


@router.get("/config")
def get_config():
    """Expose server capabilities for the frontend UI.

    Single-key mode has been removed; Multi-API is always on.
    """
    try:
        from config import API_KEYS as _API_KEYS  # type: ignore
        keys_count = len(_API_KEYS) if isinstance(_API_KEYS, list) else (1 if _API_KEYS else 0)
    except Exception:
        keys_count = 0
    models = ["flash", "pro"]
    return {"multi_api_available": True, "api_keys_count": keys_count, "models": models}


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


from .auth import get_current_user as _get_current_user  # lazy import to avoid cycles


@router.post("/admin/cleanup")
def admin_cleanup(
    password: Optional[str] = Query(None),
    clear_cache: bool = Query(True),
    clear_debug: bool = Query(True),
    clear_temp_sessions: bool = Query(True),
    clear_results: bool = Query(False),
    older_than_hours: int | None = Query(None, ge=1, description="Only delete files older than this many hours"),
    user=Depends(_get_current_user),
):
    """Administrative cleanup: clear cache, results, and debug/temp files."""
    _require_admin(password, user)
    summary: dict[str, dict | bool] = {}
    if clear_results:
        try:
            base_storage = Path(os.getenv("RENDER_STORAGE_PATH", "output"))
            results_dir = (base_storage / "results").resolve()
            summary["results_deleted"] = delete_path_contents(results_dir, older_than_hours)
        except Exception as e:
            summary["results_deleted"] = {"error": str(e)}
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


@router.get("/admin/storage-stats")
def storage_stats(password: Optional[str] = Query(None), user=Depends(_get_current_user)):
    """Return sizes and counts for results, debug, and sessions directories (admin-only)."""
    _require_admin(password, user)
    def dir_stats(base: Path) -> dict:
        total = 0
        files = 0
        if base.exists():
            for p in base.rglob("*"):
                try:
                    if p.is_file():
                        files += 1
                        total += p.stat().st_size
                except Exception:
                    continue
        return {"path": str(base), "files": files, "bytes": total}

    base_storage = Path(os.getenv("RENDER_STORAGE_PATH", "output"))
    results_dir = (base_storage / "results").resolve()
    debug_dir = Path("output/debug").resolve()
    sessions_dir = Path("output/temp/sessions").resolve()
    tmpdir = Path(os.getenv("TMPDIR", "")).resolve() if os.getenv("TMPDIR") else None
    return {
        "results": dir_stats(results_dir),
        "debug": dir_stats(debug_dir),
        "sessions": dir_stats(sessions_dir),
        "tmp": dir_stats(tmpdir) if tmpdir else None,
        "timestamp": datetime.now().isoformat(),
    }


@router.post("/admin/worker-cleanup")
def trigger_worker_cleanup(
    password: Optional[str] = Query(None),
    older_than_hours: Optional[int] = Query(None, ge=0, description="Override retention just for this run (0 deletes all)"),
    user=Depends(_get_current_user),
):
    """Trigger a one-shot cleanup on workers (admin-only).

    Enqueues a Celery task (`tasks.worker_cleanup_now`) that runs the same
    pruning logic workers execute periodically, allowing immediate cleanup
    of results/debug/sessions/tmp on worker disks.
    """
    _require_admin(password, user)
    try:
        # Send to the analysis queue so it runs on workers that only consume 'analysis'
        task = celery_app.send_task(
            "tasks.worker_cleanup_now",
            kwargs={"older_hours": older_than_hours} if older_than_hours is not None else {},
            queue="analysis",
        )
        return {"status": "queued", "task_id": task.id, "older_than_hours": older_than_hours}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to enqueue worker cleanup: {e}")


@router.get("/admin/worker-storage-stats")
def get_worker_storage_stats(password: Optional[str] = Query(None), user=Depends(_get_current_user)):
    """Queue a worker-side storage stats task (admin-only).

    Returns a task_id which can be polled via GET /status/{task_id}.
    """
    _require_admin(password, user)
    try:
        task = celery_app.send_task("tasks.worker_storage_stats", queue="analysis")
        return {"status": "queued", "task_id": task.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to enqueue worker storage stats: {e}")
