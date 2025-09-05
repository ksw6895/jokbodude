import os
import time
from contextlib import asynccontextmanager
import asyncio
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from pdf_processor.pdf.cache import clear_global_cache, get_global_cache
from storage_manager import StorageManager

from .core import REDIS_URL
from .routes import analyze, jobs, misc, auth
from .routes import preflight


async def _prune_path(base: Path, older_than_hours: int) -> None:
    """Best-effort prune helper: delete files older than threshold and empty dirs."""
    now = time.time()
    if not base.exists():
        return
    # Files first
    for p in base.rglob("*"):
        try:
            if not p.is_file():
                continue
            age_hours = (now - p.stat().st_mtime) / 3600.0
            if age_hours >= max(0, int(older_than_hours)):
                p.unlink(missing_ok=True)
        except Exception:
            continue
    # Then remove empty dirs deepest-first
    for d in sorted(base.rglob("*"), key=lambda x: len(str(x)), reverse=True):
        try:
            if d.is_dir():
                next(d.iterdir())
        except StopIteration:
            try:
                d.rmdir()
            except Exception:
                pass


async def _periodic_storage_prune(app: FastAPI) -> None:
    """Background task: periodically prune RENDER_STORAGE_PATH by age.

    Defaults: STORAGE_RETENTION_HOURS=24, PRUNE_INTERVAL_MINUTES=60
    """
    try:
        base = Path(os.getenv("RENDER_STORAGE_PATH", "output")).resolve()
        try:
            retention = int(os.getenv("STORAGE_RETENTION_HOURS", "24"))
        except Exception:
            retention = 24
        try:
            interval = int(os.getenv("PRUNE_INTERVAL_MINUTES", "60"))
        except Exception:
            interval = 60
        # Nothing to do if base does not exist
        while True:
            try:
                if base.exists():
                    await asyncio.to_thread(_prune_path, base, retention)  # type: ignore[arg-type]
            except Exception:
                pass
            await asyncio.sleep(max(60, interval * 60))
    except asyncio.CancelledError:
        # graceful shutdown
        return


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Application startup: Initializing resources...")
    app.state.storage_manager = StorageManager(REDIS_URL)
    get_global_cache()
    # Start periodic storage prune task
    cleaner_task: asyncio.Task | None = None
    try:
        cleaner_task = asyncio.create_task(_periodic_storage_prune(app))
        app.state.cleaner_task = cleaner_task
    except Exception:
        cleaner_task = None
    try:
        _ret_hours = int(os.getenv("DEBUG_RETENTION_HOURS", "168"))
        _results_ret_hours = int(os.getenv("RESULT_RETENTION_HOURS", "48"))  # default 48 hours
        _prune_dirs = [Path("output/debug"), Path("output/temp/sessions")]
        now = time.time()
        for d in _prune_dirs:
            if not d.exists():
                continue
            for p in d.rglob("*"):
                try:
                    if p.is_file():
                        age_hours = (now - p.stat().st_mtime) / 3600.0
                        if age_hours >= _ret_hours:
                            p.unlink(missing_ok=True)
                    if p.is_dir():
                        try:
                            next(p.iterdir())
                        except StopIteration:
                            p.rmdir()
                except Exception:
                    continue
        # Prune old generated results on disk
        try:
            sm = app.state.storage_manager
            results_root = getattr(sm, "results_dir", Path(os.getenv("RENDER_STORAGE_PATH", "output") ) / "results")
            if results_root.exists():
                for p in results_root.rglob("*"):
                    try:
                        if not p.is_file():
                            continue
                        age_hours = (now - p.stat().st_mtime) / 3600.0
                        if age_hours >= _results_ret_hours:
                            p.unlink(missing_ok=True)
                    except Exception:
                        continue
                # Remove empty subdirs
                for d in sorted(results_root.rglob("*"), key=lambda x: len(str(x)), reverse=True):
                    try:
                        if d.is_dir():
                            next(d.iterdir())
                    except StopIteration:
                        try:
                            d.rmdir()
                        except Exception:
                            pass
        except Exception:
            pass
    except Exception:
        pass
    yield
    print("Application shutdown: Cleaning up resources...")
    # Stop background cleaner
    try:
        cleaner_task = getattr(app.state, "cleaner_task", None)
        if cleaner_task is not None:
            cleaner_task.cancel()
            try:
                await cleaner_task
            except Exception:
                pass
    except Exception:
        pass
    clear_global_cache()
    sm = getattr(app.state, "storage_manager", None)
    if sm is not None:
        try:
            sm.close()
        except Exception:
            pass


def create_app() -> FastAPI:
    app = FastAPI(title="JokboDude API", version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(misc.router)
    app.include_router(auth.router)
    app.include_router(preflight.router)
    app.include_router(analyze.router)
    app.include_router(jobs.router)
    return app
"""Bootstrap a safe TMPDIR so temp files avoid /tmp exhaustion.
We set TMPDIR early (before any tempfile usage) to a path under the
configured persistent storage if available, or under project output/.
"""
try:
    _TMP_BASE = Path(os.getenv("RENDER_STORAGE_PATH", str(Path("output") / "temp" / "tmp")))
    os.environ.setdefault("TMPDIR", str(_TMP_BASE))
    _TMP_BASE.mkdir(parents=True, exist_ok=True)
except Exception:
    pass
