#!/usr/bin/env python3
"""
Prune old artifacts (results, debug logs, temp sessions) by age.

Usage examples:
  ./venv/bin/python scripts/prune_artifacts.py --results-hours 720 --debug-hours 168 --sessions-hours 168
  ./venv/bin/python scripts/prune_artifacts.py --results-hours 240 --dry-run

Defaults:
  RESULTS_RETENTION_HOURS=720 (30 days)
  DEBUG_RETENTION_HOURS=168 (7 days)
  SESSIONS_RETENTION_HOURS=168 (7 days)
"""
import argparse
import os
import time
from pathlib import Path


def prune_dir(base: Path, older_than_hours: int, dry_run: bool = False) -> dict:
    now = time.time()
    removed_files = 0
    removed_bytes = 0

    if not base.exists():
        return {"files": 0, "bytes": 0}

    for p in base.rglob("*"):
        try:
            if not p.is_file():
                continue
            age_hours = (now - p.stat().st_mtime) / 3600.0
            if age_hours >= older_than_hours:
                if not dry_run:
                    removed_bytes += p.stat().st_size
                    p.unlink(missing_ok=True)
                else:
                    removed_bytes += p.stat().st_size
                removed_files += 1
        except Exception:
            continue

    # Remove empty directories bottom-up
    for d in sorted(base.rglob("*"), key=lambda x: len(str(x)), reverse=True):
        try:
            if d.is_dir():
                try:
                    next(d.iterdir())
                except StopIteration:
                    if not dry_run:
                        d.rmdir()
        except Exception:
            continue
    return {"files": removed_files, "bytes": removed_bytes}


def main() -> None:
    parser = argparse.ArgumentParser(description="Prune old artifacts by age")
    parser.add_argument("--results-hours", type=int, default=int(os.getenv("RESULT_RETENTION_HOURS", "720")))
    parser.add_argument("--debug-hours", type=int, default=int(os.getenv("DEBUG_RETENTION_HOURS", "168")))
    parser.add_argument("--sessions-hours", type=int, default=int(os.getenv("SESSIONS_RETENTION_HOURS", "168")))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--base", type=str, default=os.getenv("RENDER_STORAGE_PATH", None), help="Base storage path (optional)")
    args = parser.parse_args()

    # Resolve directories
    results_root = Path(args.base).resolve() / "results" if args.base else (Path("output") / "results")
    debug_dir = Path("output") / "debug"
    sessions_dir = Path("output") / "temp" / "sessions"

    print(f"Pruning results in: {results_root} (older than {args.results_hours}h)")
    r = prune_dir(results_root, args.results_hours, dry_run=args.dry_run)
    print(f"  files {'to remove' if args.dry_run else 'removed'}: {r['files']}, bytes: {r['bytes']}")

    print(f"Pruning debug in: {debug_dir} (older than {args.debug_hours}h)")
    d = prune_dir(debug_dir, args.debug_hours, dry_run=args.dry_run)
    print(f"  files {'to remove' if args.dry_run else 'removed'}: {d['files']}, bytes: {d['bytes']}")

    print(f"Pruning sessions in: {sessions_dir} (older than {args.sessions_hours}h)")
    s = prune_dir(sessions_dir, args.sessions_hours, dry_run=args.dry_run)
    print(f"  files {'to remove' if args.dry_run else 'removed'}: {s['files']}, bytes: {s['bytes']}")


if __name__ == "__main__":
    main()

