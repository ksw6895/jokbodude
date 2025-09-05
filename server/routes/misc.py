import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, Depends
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

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
    """Expose server capabilities for the frontend UI."""
    try:
        from config import API_KEYS as _API_KEYS  # type: ignore
        keys_count = len(_API_KEYS) if isinstance(_API_KEYS, list) else (1 if _API_KEYS else 0)
    except Exception:
        keys_count = 0
    models = ["flash", "pro"]
    return {"multi_api_available": keys_count > 1, "api_keys_count": keys_count, "models": models}


@router.get("/health")
async def health_check(request: Request, stall_minutes: int = Query(10, ge=1, le=120)):
    """Health endpoint with worker status, queue depth, and stall watchdog.

    - stall_minutes: threshold in minutes without any chunk completion to flag a job as stalled.
    """
    storage_manager = request.app.state.storage_manager
    checks = {"web": "healthy", "redis": "unknown", "worker": "unknown"}
    details: dict = {"celery": {}, "watchdog": {}}

    # Redis check
    try:
        storage_manager.redis_client.ping()
        checks["redis"] = "healthy"
    except Exception as e:
        checks["redis"] = f"unhealthy: {str(e)}"

    # Celery worker/queues
    try:
        inspector = celery_app.control.inspect()
        active = inspector.active() or {}
        reserved = inspector.reserved() or {}
        scheduled = inspector.scheduled() or {}
        # Aggregate totals
        def _agg_total(m):
            try:
                return sum(len(v or []) for v in m.values())
            except Exception:
                return None
        totals = {
            "active": _agg_total(active) or 0,
            "reserved": _agg_total(reserved) or 0,
            "scheduled": _agg_total(scheduled) or 0,
        }
        # Breakdown by routing_key if delivery_info present
        def _by_route(m):
            out: dict[str, int] = {}
            try:
                for entries in m.values():
                    for t in entries or []:
                        try:
                            di = t.get("delivery_info") or {}
                            rk = (di.get("routing_key") or di.get("exchange") or "unknown").split(".")[0]
                        except Exception:
                            rk = "unknown"
                        out[rk] = out.get(rk, 0) + 1
            except Exception:
                pass
            return out
        details["celery"]["active_by_queue"] = _by_route(active)
        details["celery"]["reserved_by_queue"] = _by_route(reserved)
        details["celery"]["scheduled_by_queue"] = _by_route(scheduled)
        details["celery"].update(totals)
        checks["worker"] = "healthy" if totals["active"] is not None else "unknown"
    except Exception as e:
        checks["worker"] = f"unhealthy: {str(e)}"

    # Redis queue LLEN best-effort
    redis_depths: dict[str, int] = {}
    try:
        r = storage_manager.redis_client
        candidates = [
            "celery",  # default queue
            "default", "analysis",
            "celery:queue:celery", "celery:queue:default", "celery:queue:analysis",
        ]
        for k in candidates:
            try:
                t = r.type(k)
                if (t or b"").decode() != "list":
                    continue
                n = r.llen(k)
                if int(n or 0) > 0:
                    redis_depths[k] = int(n)
            except Exception:
                continue
    except Exception:
        pass
    details["celery"]["redis_queue_depths"] = redis_depths

    # Watchdog: stalled jobs with no recent chunk
    stalled: list[dict] = []
    try:
        now = time.time()
        threshold = float(max(1, int(stall_minutes))) * 60.0
        r = storage_manager.redis_client
        for key in r.scan_iter(match="progress:*"):
            try:
                pdata = r.hgetall(key) or {}
                # Decode helpers
                def _g(name, default=""):
                    return (pdata.get(name) or pdata.get(name.encode()) or default)
                def _d(v):
                    return v.decode() if isinstance(v, (bytes, bytearray)) else v
                prog = int((_g("progress", "0")))
                if prog >= 100:
                    continue  # completed
                total = int((_g("total_chunks", "0")))
                done = int((_g("completed_chunks", "0")))
                last_chunk_at = float((_g("last_chunk_at", "0") or 0) or 0)
                if last_chunk_at <= 0:
                    # Fallback to started_at or consider non-stalled if no info
                    last_chunk_at = float((_g("started_at", "0") or 0) or 0)
                idle = now - last_chunk_at if last_chunk_at > 0 else 0
                if idle >= threshold and done < total:
                    jid = _d(key)[len("progress:"):] if isinstance(key, (bytes, bytearray)) else str(key).split(":",1)[-1]
                    stalled.append({
                        "job_id": jid,
                        "progress": prog,
                        "completed_chunks": done,
                        "total_chunks": total,
                        "idle_seconds": int(idle),
                        "message": _d(_g("message", "")),
                    })
            except Exception:
                # Ignore malformed progress entries and keep scanning
                continue
    except Exception:
        pass
    details["watchdog"]["stalled_jobs"] = stalled
    details["watchdog"]["stall_minutes_threshold"] = stall_minutes

    all_healthy = (checks.get("redis") == "healthy" and isinstance(details.get("celery", {}).get("active", 0), int))
    status_code = 200 if all_healthy else 503
    return JSONResponse(
        content={
            "status": "healthy" if status_code == 200 else "degraded",
            "checks": checks,
            "details": details,
            "timestamp": datetime.now().isoformat(),
        },
        status_code=status_code,
    )


from .auth import get_current_user as _get_current_user  # lazy import to avoid cycles


@router.post("/admin/cleanup")
def admin_cleanup(
    password: Optional[str] = Query(None),
    clear_cache: bool = Query(True),
    clear_debug: bool = Query(True),
    clear_temp_sessions: bool = Query(True),
    older_than_hours: int | None = Query(None, ge=1, description="Only delete files older than this many hours"),
    user=Depends(_get_current_user),
):
    """Administrative cleanup: clear cache and prune debug/temp files."""
    _require_admin(password, user)
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


# --- Admin: results index and bulk delete ---

class ResultsFilter(BaseModel):
    user_id: Optional[str] = None
    user_email: Optional[str] = None
    older_than_hours: Optional[int] = None
    filename_contains: Optional[str] = None
    limit: Optional[int] = 100
    offset: Optional[int] = 0


def _scan_results_index(request: Request):
    sm = request.app.state.storage_manager
    root = getattr(sm, "results_dir", Path(os.getenv("RENDER_STORAGE_PATH", "output")) / "results")
    now = time.time()
    if not root.exists():
        return []
    items: list[dict] = []
    for job_dir in sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.name):
        job_id = job_dir.name
        for p in job_dir.glob("*.pdf"):
            try:
                st = p.stat()
                mtime = st.st_mtime
                age_hours = (now - mtime) / 3600.0
                size_bytes = int(st.st_size)
                owner_id = None
                owner_email = None
                try:
                    owner_id = sm.get_job_owner(job_id)
                except Exception:
                    owner_id = None
                if owner_id:
                    try:
                        prof = sm.get_user_profile(owner_id) or {}
                        owner_email = prof.get("email")
                    except Exception:
                        owner_email = None
                has_redis_copy = False
                try:
                    if getattr(sm, "redis_client", None):
                        key = f"result:{job_id}:{p.name}"
                        has_redis_copy = bool(sm.redis_client.exists(key))  # type: ignore[attr-defined]
                except Exception:
                    has_redis_copy = False
                items.append({
                    "job_id": job_id,
                    "filename": p.name,
                    "size_bytes": size_bytes,
                    "modified_at": datetime.fromtimestamp(mtime).isoformat(),
                    "age_hours": age_hours,
                    "owner_id": owner_id,
                    "owner_email": owner_email,
                    "has_redis_copy": has_redis_copy,
                })
            except Exception:
                continue
    return items


def _apply_results_filters(items: list[dict], filt: ResultsFilter) -> list[dict]:
    out: list[dict] = []
    uid_q = (filt.user_id or "").strip().lower() or None
    email_q = (filt.user_email or "").strip().lower() or None
    name_q = (filt.filename_contains or "").strip().lower() or None
    older = filt.older_than_hours
    for it in items:
        if older is not None:
            try:
                if float(it.get("age_hours", 0.0)) < float(older):
                    continue
            except Exception:
                pass
        if name_q and name_q not in str(it.get("filename", "")).lower():
            continue
        if uid_q and uid_q not in str(it.get("owner_id", "")).lower():
            continue
        if email_q and email_q not in str(it.get("owner_email", "")).lower():
            continue
        out.append(it)
    return out


@router.get("/admin/results-index")
def admin_results_index(
    request: Request,
    password: Optional[str] = Query(None),
    user=Depends(_get_current_user),
    user_id: Optional[str] = Query(None),
    user_email: Optional[str] = Query(None),
    older_than_hours: Optional[int] = Query(None, ge=0),
    filename_contains: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """Scan the results directory and return an index of generated PDFs (admin-only)."""
    _require_admin(password, user)
    items = _scan_results_index(request)
    filtered = _apply_results_filters(items, ResultsFilter(
        user_id=user_id,
        user_email=user_email,
        older_than_hours=older_than_hours,
        filename_contains=filename_contains,
        limit=limit,
        offset=offset,
    ))
    total = len(filtered)
    start = max(0, int(offset))
    end = start + int(limit)
    page = filtered[start:end]
    next_offset = end if end < total else None
    return {"items": page, "total": total, "next_offset": next_offset}


class DeleteRequest(BaseModel):
    filter: ResultsFilter
    dry_run: bool = True


@router.post("/admin/delete-results")
def admin_delete_results(request: Request, payload: DeleteRequest, password: Optional[str] = Query(None), user=Depends(_get_current_user)):
    """Bulk delete results by filter (admin-only). Use dry_run to preview deletions."""
    _require_admin(password, user)
    # Scan and filter
    items = _scan_results_index(request)
    filtered = _apply_results_filters(items, payload.filter)
    # Apply pagination from filter if provided
    start = max(0, int(payload.filter.offset or 0))
    lim = int(payload.filter.limit or len(filtered) or 0)
    end = start + lim
    targets = filtered[start:end]
    # Prepare response summary
    summary_items: list[dict] = []
    total_bytes = 0
    deleted_count = 0
    sm = request.app.state.storage_manager
    for it in targets:
        size_b = int(it.get("size_bytes", 0) or 0)
        total_bytes += size_b
        summary_items.append({"job_id": it.get("job_id"), "filename": it.get("filename"), "bytes": size_b})
        if not payload.dry_run:
            try:
                if sm.delete_result(str(it.get("job_id")), str(it.get("filename"))):
                    deleted_count += 1
            except Exception:
                continue
    resp = {
        ("deleted_count" if not payload.dry_run else "would_delete_count"): deleted_count if not payload.dry_run else len(targets),
        "total_bytes": total_bytes,
        "items": summary_items,
    }
    return resp


@router.post("/admin/prune-results")
def admin_prune_results(
    request: Request,
    password: Optional[str] = Query(None),
    user=Depends(_get_current_user),
    older_than_hours: Optional[int] = Query(None, ge=0),
    dry_run: bool = Query(False),
):
    """Prune on-disk results older than the given threshold (admin-only)."""
    _require_admin(password, user)
    # Default to env RESULT_RETENTION_HOURS if not provided
    if older_than_hours is None:
        try:
            older_than_hours = int(os.getenv("RESULT_RETENTION_HOURS", "720"))
        except Exception:
            older_than_hours = 720
    # Reuse the delete endpoint logic without user/email filters
    filt = ResultsFilter(older_than_hours=older_than_hours, limit=10_000, offset=0)
    payload = DeleteRequest(filter=filt, dry_run=dry_run)
    return admin_delete_results(request, payload, password=password, user=user)  # type: ignore[arg-type]


# ----------------------
# Admin: Active/Ghost tasks
# ----------------------

class RevokeRequest(BaseModel):
    task_id: str | None = None
    job_id: str | None = None
    terminate: bool = True
    signal: str = "SIGTERM"


def _parse_job_id_from_task(task: dict) -> str | None:
    """Best-effort extract job_id from Celery task args/kwargs.
    Our tasks take job_id as the first positional arg.
    """
    # Try kwargs first
    try:
        kwargs = task.get("kwargs")
        if isinstance(kwargs, dict):
            jid = kwargs.get("job_id")
            if isinstance(jid, str) and len(jid) > 10:
                return jid
    except Exception:
        pass
    # Try args string via literal_eval
    try:
        import ast
        args_str = task.get("args")
        if isinstance(args_str, str) and args_str:
            tup = ast.literal_eval(args_str)
            if isinstance(tup, (list, tuple)) and len(tup) >= 1 and isinstance(tup[0], str):
                if len(tup[0]) > 10:
                    return tup[0]
    except Exception:
        pass
    return None


def _collect_tasks():
    insp = celery_app.control.inspect()
    payload = {
        "active": insp.active() or {},
        "reserved": insp.reserved() or {},
        "scheduled": insp.scheduled() or {},
    }
    items: list[dict] = []
    for state in ("active", "reserved", "scheduled"):
        m = payload.get(state) or {}
        for worker, entries in m.items():
            for t in (entries or []):
                d = dict(t)
                d["state"] = state
                d["worker"] = worker
                items.append(d)
    return items


@router.get("/admin/active-tasks")
def admin_active_tasks(request: Request, password: str | None = Query(None), user=Depends(_get_current_user)):
    """List active/reserved/scheduled tasks with job_id and ghost flag (admin-only)."""
    _require_admin(password, user)
    sm = request.app.state.storage_manager
    items = _collect_tasks()
    out: list[dict] = []
    for t in items:
        jid = _parse_job_id_from_task(t)
        ghost = False
        cancel_flag = False
        if jid:
            try:
                ghost = sm.get_progress(jid) is None
            except Exception:
                ghost = False
            try:
                cancel_flag = sm.is_cancelled(jid)
            except Exception:
                cancel_flag = False
        out.append({
            "state": t.get("state"),
            "worker": t.get("worker"),
            "task_id": t.get("id") or t.get("request", {}).get("id"),
            "name": t.get("name"),
            "routing": (t.get("delivery_info") or {}).get("routing_key"),
            "job_id": jid,
            "ghost": bool(ghost),
            "cancel": bool(cancel_flag),
        })
    return {"count": len(out), "tasks": out}


@router.post("/admin/revoke")
def admin_revoke_task(request: Request, payload: RevokeRequest, password: str | None = Query(None), user=Depends(_get_current_user)):
    """Revoke a running task by task_id or job_id (admin-only). Also marks job as cancelled.

    Provide at least one of task_id or job_id.
    """
    _require_admin(password, user)
    sm = request.app.state.storage_manager
    to_revoke: set[str] = set()
    # If job_id provided, mark cancel and find matching tasks
    if payload.job_id:
        try:
            sm.request_cancel(payload.job_id)
        except Exception:
            pass
        for t in _collect_tasks():
            jid = _parse_job_id_from_task(t)
            if jid and jid == payload.job_id:
                tid = t.get("id") or (t.get("request") or {}).get("id")
                if isinstance(tid, str):
                    to_revoke.add(tid)
    # If task_id provided, add directly
    if payload.task_id:
        to_revoke.add(payload.task_id)
    revoked = 0
    for tid in to_revoke:
        try:
            celery_app.control.revoke(tid, terminate=bool(payload.terminate), signal=str(payload.signal))
            revoked += 1
        except Exception:
            continue
    return {"requested": len(to_revoke), "revoked": revoked}


class KillGhostsRequest(BaseModel):
    dry_run: bool = True
    include_reserved: bool = True
    include_scheduled: bool = False


@router.post("/admin/kill-ghosts")
def admin_kill_ghosts(request: Request, payload: KillGhostsRequest, password: str | None = Query(None), user=Depends(_get_current_user)):
    """Find tasks with no matching progress key and revoke them (admin-only).

    Ghost criterion: task has a job_id but `progress:{job_id}` is missing.
    """
    _require_admin(password, user)
    sm = request.app.state.storage_manager
    tasks = _collect_tasks()
    victims: list[dict] = []
    for t in tasks:
        st = t.get("state")
        if st not in ("active", "reserved", "scheduled"):
            continue
        if st == "reserved" and not payload.include_reserved:
            continue
        if st == "scheduled" and not payload.include_scheduled:
            continue
        jid = _parse_job_id_from_task(t)
        if not jid:
            continue
        try:
            is_ghost = sm.get_progress(jid) is None
        except Exception:
            is_ghost = False
        if not is_ghost:
            continue
        tid = t.get("id") or (t.get("request") or {}).get("id")
        victims.append({"task_id": tid, "job_id": jid, "state": st, "worker": t.get("worker")})
    if payload.dry_run:
        return {"ghosts": victims, "count": len(victims), "dry_run": True}
    revoked = 0
    for v in victims:
        jid = v.get("job_id")
        tid = v.get("task_id")
        if isinstance(jid, str):
            try:
                sm.request_cancel(jid)
            except Exception:
                pass
        if isinstance(tid, str):
            try:
                celery_app.control.revoke(tid, terminate=True, signal="SIGTERM")
                revoked += 1
            except Exception:
                continue
    return {"ghosts": victims, "count": len(victims), "revoked": revoked, "dry_run": False}
