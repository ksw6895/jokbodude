"""Shared helpers for configuring a safe temporary directory.

Render workers provide only 2GB of `/tmp`.  This module forces the
application to use a directory under the persistent storage volume when
available, or a project-local fallback otherwise.  The helper also updates
Python's `tempfile` module so `TemporaryDirectory`/`NamedTemporaryFile`
respect the override.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DEF_SUBDIR = "tmp"


def _resolve_base_dir(subdir: str) -> Path:
    """Return the desired base directory for temporary files.

    Preference order:
    1. WORKER_TMPDIR / APP_TMPDIR (explicit override)
    2. Existing TMPDIR if it is not pointing at /tmp
    3. RENDER_STORAGE_PATH/<subdir>
    4. output/temp/<subdir>
    """

    override = os.getenv("WORKER_TMPDIR") or os.getenv("APP_TMPDIR")
    if override:
        return Path(override).expanduser()

    existing = os.getenv("TMPDIR")
    if existing:
        cand = Path(existing).expanduser()
        try:
            resolved: Optional[Path] = cand.resolve()
        except Exception:
            resolved = cand
        # Avoid returning /tmp or nested children of /tmp
        try:
            if resolved.is_relative_to(Path("/tmp")):
                resolved = None
        except AttributeError:
            if str(resolved).startswith("/tmp"):
                resolved = None
        if resolved:
            return resolved

    storage_root = os.getenv("RENDER_STORAGE_PATH")
    if storage_root:
        return Path(storage_root).expanduser() / subdir

    return Path("output") / "temp" / subdir


def configure_tmpdir(subdir: Optional[str] = None) -> Path:
    """Ensure TMPDIR points to a writable, persistent location.

    Returns the resolved directory path.  Any errors fall back to Python's
    default temp dir so the caller can continue, though /tmp exhaustion may
    still occur if the fallback is hit.
    """

    target = _resolve_base_dir(subdir or _DEF_SUBDIR)
    try:
        target.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning("Failed to create temp dir %s: %s", target, exc)
        return target

    try:
        resolved = target.resolve()
    except Exception:
        resolved = target

    path_str = str(resolved)
    os.environ["TMPDIR"] = path_str
    os.environ["TMP"] = path_str
    os.environ["TEMP"] = path_str
    tempfile.tempdir = path_str
    logger.info("TMPDIR configured to %s", path_str)
    return resolved

__all__ = ["configure_tmpdir"]
