import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from pdf_processor.pdf.cache import clear_global_cache, get_global_cache
from storage_manager import StorageManager

from .core import REDIS_URL
from .routes import analyze, jobs, misc


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Application startup: Initializing resources...")
    app.state.storage_manager = StorageManager(REDIS_URL)
    get_global_cache()
    try:
        _ret_hours = int(os.getenv("DEBUG_RETENTION_HOURS", "168"))
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
    except Exception:
        pass
    yield
    print("Application shutdown: Cleaning up resources...")
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
    app.include_router(analyze.router)
    app.include_router(jobs.router)
    return app
