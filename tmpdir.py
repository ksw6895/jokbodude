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
_PATCHED_GETTEMP = False
_CURRENT_TARGET: Optional[str] = None


def _ensure_directory(path: Path) -> Path:
    """Create *path* (including parents) and verify it is usable."""

    path.mkdir(parents=True, exist_ok=True)
    if not path.exists():  # pragma: no cover - defensive guard
        raise FileNotFoundError(path)
    # Require execute (directory traversal) and write permissions
    if not os.access(path, os.W_OK | os.X_OK):  # pragma: no cover - environment specific
        raise PermissionError(f"Directory {path} is not writable")
    return path


def _patch_tempfile_guard() -> None:
    """Ensure tempfile always recreates the configured directory if missing."""

    global _PATCHED_GETTEMP
    if _PATCHED_GETTEMP:
        return

    original_gettempdir = tempfile._gettempdir

    def _wrapped_gettempdir() -> str:
        value = original_gettempdir()
        try:
            _ensure_directory(Path(value))
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning("Failed to ensure temp dir %s: %s", value, exc)
        return value

    tempfile._gettempdir = _wrapped_gettempdir
    _PATCHED_GETTEMP = True


def _apply_tempdir(target: Path) -> None:
    """Write environment variables and patch tempfile for *target*."""

    global _CURRENT_TARGET

    path_str = str(target)
    os.environ["TMPDIR"] = path_str
    os.environ["TMP"] = path_str
    os.environ["TEMP"] = path_str
    tempfile.tempdir = path_str
    _patch_tempfile_guard()
    if _CURRENT_TARGET != path_str:
        logger.info("TMPDIR configured to %s", path_str)
        _CURRENT_TARGET = path_str


def _candidate_dirs(subdir: str) -> list[Path]:
    """Generate candidate directories in preference order.

    Preference order matches previous behaviour but allows graceful
    fallback when a preferred location is unusable.
    """

    candidates: list[Path] = []

    def _add(path: Optional[str | Path]) -> None:
        if not path:
            return
        candidate = Path(path).expanduser()
        if candidate in candidates:
            return
        candidates.append(candidate)

    override = os.getenv("WORKER_TMPDIR") or os.getenv("APP_TMPDIR")
    _add(override)

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
        _add(resolved)

    storage_root = os.getenv("RENDER_STORAGE_PATH")
    if storage_root:
        _add(Path(storage_root).expanduser() / subdir)

    # Project-local fallback (ensures deterministic behaviour in tests/dev)
    _add(Path("output") / "temp" / subdir)

    # Last resort: allow the system default temp dir
    _add(Path(tempfile.gettempdir()) / subdir)

    return candidates


def configure_tmpdir(subdir: Optional[str] = None) -> Path:
    """Ensure TMPDIR points to a writable, persistent location.

    Returns the resolved directory path.  Any errors fall back to Python's
    default temp dir so the caller can continue, though /tmp exhaustion may
    still occur if the fallback is hit.
    """

    final_target: Optional[Path] = None
    errors: list[tuple[Path, Exception]] = []

    for candidate in _candidate_dirs(subdir or _DEF_SUBDIR):
        try:
            prepared = _ensure_directory(candidate)
            final_target = prepared.resolve()
            break
        except Exception as exc:  # pragma: no cover - defensive logging
            errors.append((candidate, exc))
            continue

    if final_target is None:
        for candidate, exc in errors:
            logger.warning("Failed to prepare temp dir %s: %s", candidate, exc)
        fallback = Path(tempfile.gettempdir()).resolve()
        final_target = _ensure_directory(fallback)

    _apply_tempdir(final_target)
    return final_target

__all__ = ["configure_tmpdir"]
