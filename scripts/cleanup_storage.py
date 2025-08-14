#!/usr/bin/env python3
"""
Cleanup script to remove accumulated local storage files.

Default target is the path in RENDER_STORAGE_PATH.
Use --force to actually delete. Without --force, runs in dry-run mode.
"""
import argparse
import os
import shutil
from pathlib import Path
from typing import Tuple


def human_size(n: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except FileNotFoundError:
            continue
    return total


def cleanup(path: Path, dry_run: bool) -> Tuple[int, int]:
    files = 0
    bytes_freed = 0
    for child in path.iterdir():
        try:
            if dry_run:
                if child.is_file():
                    files += 1
                    bytes_freed += child.stat().st_size
                else:
                    bytes_freed += dir_size(child)
            else:
                if child.is_file():
                    files += 1
                    bytes_freed += child.stat().st_size
                    child.unlink(missing_ok=True)
                else:
                    bytes_freed += dir_size(child)
                    shutil.rmtree(child)
        except Exception as e:
            print(f"Warning: failed to process {child}: {e}")
    return files, bytes_freed


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean up accumulated local storage")
    parser.add_argument(
        "--path",
        type=str,
        default=os.getenv("RENDER_STORAGE_PATH"),
        help="Target storage path (defaults to RENDER_STORAGE_PATH)",
    )
    parser.add_argument("--force", action="store_true", help="Actually delete files (not just dry-run)")
    args = parser.parse_args()

    if not args.path:
        print("Error: --path not provided and RENDER_STORAGE_PATH is not set.")
        raise SystemExit(2)

    target = Path(args.path).resolve()
    if not target.exists() or not target.is_dir():
        print(f"Nothing to clean: {target} does not exist or is not a directory.")
        return

    # Safety checks
    if target == Path("/"):
        print("Refusing to operate on root directory.")
        raise SystemExit(3)

    dry_run = not args.force
    print(f"Cleaning {'(dry-run)' if dry_run else ''}: {target}")

    before = dir_size(target)
    files, bytes_freed = cleanup(target, dry_run=dry_run)
    after = before - bytes_freed if not dry_run else before

    print(f"Items {'to remove' if dry_run else 'removed'}: {files}")
    print(f"Space {'to free' if dry_run else 'freed'}: {human_size(bytes_freed)}")
    if dry_run:
        print("Run again with --force to apply changes.")


if __name__ == "__main__":
    main()

