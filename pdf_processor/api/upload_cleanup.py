"""
Utilities to purge uploaded files for the current API key, independent of
FileManager tracking state. This is used to aggressively clear stale lesson
chunk uploads (e.g., display_name starting with "강의자료_") before starting a
new chunk, while preserving the center file (e.g., "족보_...").
"""

from typing import List, Optional, Set, Dict, Any

from .client import GeminiAPIClient
from ..utils.logging import get_logger

logger = get_logger(__name__)


def purge_key_files(
    api_client: GeminiAPIClient,
    *,
    delete_prefixes: Optional[List[str]] = None,
    keep_display_names: Optional[Set[str]] = None,
    delete_all: bool = False,
    log_context: str = "",
) -> Dict[str, int]:
    """
    Delete uploaded files visible to the given API key based on display_name.

    Args:
        api_client: Bound GeminiAPIClient (holds the target API key)
        delete_prefixes: Only delete files whose display_name starts with any prefix
        keep_display_names: Do not delete files whose display_name is in this set
        delete_all: If True, delete everything except keep_display_names
        log_context: Optional short context added to logs

    Returns:
        Dict with counts: {"total": n, "deleted": m, "skipped": k}
    """
    keep_display_names = keep_display_names or set()
    delete_prefixes = delete_prefixes or []

    files = api_client.list_files() or []
    total = len(files)
    deleted = 0
    skipped = 0

    if log_context:
        logger.info(
            f"[purge] listing files: total={total} ctx={log_context} [key={api_client._key_tag()}]"
        )
    else:
        logger.info(
            f"[purge] listing files: total={total} [key={api_client._key_tag()}]"
        )

    for f in files:
        try:
            dn = getattr(f, "display_name", "") or ""
        except Exception:
            dn = ""

        # Keep explicit keepers
        if dn in keep_display_names:
            skipped += 1
            continue

        # If delete_all, remove everything except keepers
        should_delete = delete_all
        if not should_delete and delete_prefixes:
            for p in delete_prefixes:
                try:
                    if dn.startswith(p):
                        should_delete = True
                        break
                except Exception:
                    continue

        if not should_delete:
            skipped += 1
            continue

        ok = api_client.delete_file(f, max_retries=1)
        if ok:
            deleted += 1
        else:
            # Treat failure as skip; higher-level cleanup may retry via GC
            skipped += 1

    logger.info(
        f"[purge] done: total={total}, deleted={deleted}, skipped={skipped}"
        + (f" ctx={log_context}" if log_context else "")
        + f" [key={api_client._key_tag()}]"
    )
    return {"total": total, "deleted": deleted, "skipped": skipped}

