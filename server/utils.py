import re
import shutil
import time
from pathlib import Path
from urllib.parse import quote
from fastapi import UploadFile


def save_uploaded_file(upload_file: UploadFile, destination_dir: Path) -> Path:
    """Persist an uploaded file to a destination directory."""
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination_path = destination_dir / upload_file.filename
    with destination_path.open("wb") as buffer:
        shutil.copyfileobj(upload_file.file, buffer)
    return destination_path


def build_content_disposition(original_name: str) -> str:
    """Build a RFC 6266 compliant Content-Disposition header value."""
    match = re.search(r"(\.[A-Za-z0-9]+)$", original_name)
    ext = match.group(1) if match else ""
    base = original_name[:-len(ext)] if ext else original_name

    fallback_base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("_") or "download"
    if len(fallback_base) > 150:
        fallback_base = fallback_base[:150] + "_"
    fallback = f"{fallback_base}{ext or '.pdf'}"
    utf8_star = quote(original_name)
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{utf8_star}"


def delete_path_contents(base: Path, older_than_hours: int | None = None) -> dict:
    """Delete files and empty directories under a path."""
    deleted_files = 0
    deleted_dirs = 0
    now = time.time()
    if not base.exists():
        return {"files": 0, "dirs": 0}
    for p in sorted(base.rglob("*"), key=lambda x: len(str(x)), reverse=True):
        try:
            if p.is_file():
                if older_than_hours is not None:
                    age_hours = (now - p.stat().st_mtime) / 3600.0
                    if age_hours < older_than_hours:
                        continue
                p.unlink(missing_ok=True)
                deleted_files += 1
            elif p.is_dir():
                try:
                    next(p.iterdir())
                except StopIteration:
                    p.rmdir()
                    deleted_dirs += 1
        except Exception:
            continue
    return {"files": deleted_files, "dirs": deleted_dirs}
