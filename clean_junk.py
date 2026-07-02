#!/usr/bin/env python3
"""Safely clean common temporary files and cache folders.

The script defaults to dry-run behavior unless --execute is provided. It only
targets well-known temporary/cache locations for the current operating system
and skips missing paths, protected files, and items newer than --min-age-days.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class CleanupTarget:
    label: str
    path: Path


@dataclass
class CleanupStats:
    files_removed: int = 0
    dirs_removed: int = 0
    bytes_removed: int = 0
    skipped: int = 0
    errors: int = 0


def unique_existing_targets(targets: Iterable[CleanupTarget]) -> list[CleanupTarget]:
    seen: set[Path] = set()
    result: list[CleanupTarget] = []

    for target in targets:
        try:
            resolved = target.path.expanduser().resolve(strict=False)
        except OSError:
            continue

        if resolved in seen or not resolved.exists():
            continue

        seen.add(resolved)
        result.append(CleanupTarget(target.label, resolved))

    return result


def discover_targets() -> list[CleanupTarget]:
    home = Path.home()
    system = platform.system().lower()
    targets = [
        CleanupTarget("Python/user temp", Path(tempfile.gettempdir())),
    ]

    if system == "windows":
        local_app_data = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        app_data = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        targets.extend(
            [
                CleanupTarget("Windows user temp", Path(os.environ.get("TEMP", tempfile.gettempdir()))),
                CleanupTarget("Windows temp", Path(os.environ.get("WINDIR", "C:/Windows")) / "Temp"),
                CleanupTarget("Chrome cache", local_app_data / "Google" / "Chrome" / "User Data" / "Default" / "Cache"),
                CleanupTarget("Edge cache", local_app_data / "Microsoft" / "Edge" / "User Data" / "Default" / "Cache"),
                CleanupTarget("Firefox cache", local_app_data / "Mozilla" / "Firefox" / "Profiles"),
                CleanupTarget("Discord cache", app_data / "discord" / "Cache"),
            ]
        )
    elif system == "darwin":
        targets.extend(
            [
                CleanupTarget("macOS user caches", home / "Library" / "Caches"),
                CleanupTarget("macOS logs", home / "Library" / "Logs"),
                CleanupTarget("macOS temp", Path("/tmp")),
            ]
        )
    else:
        targets.extend(
            [
                CleanupTarget("Linux user cache", home / ".cache"),
                CleanupTarget("Linux temp", Path("/tmp")),
                CleanupTarget("Linux var temp", Path("/var/tmp")),
            ]
        )

    return unique_existing_targets(targets)


def is_old_enough(path: Path, cutoff: float | None) -> bool:
    if cutoff is None:
        return True
    try:
        return path.stat().st_mtime <= cutoff
    except OSError:
        return False


def size_of(path: Path) -> int:
    try:
        if path.is_file() or path.is_symlink():
            return path.stat().st_size
        total = 0
        for item in path.rglob("*"):
            if item.is_file() or item.is_symlink():
                try:
                    total += item.stat().st_size
                except OSError:
                    pass
        return total
    except OSError:
        return 0


def remove_path(path: Path, dry_run: bool, stats: CleanupStats) -> None:
    item_size = size_of(path)

    if dry_run:
        print(f"[dry-run] would remove: {path} ({format_bytes(item_size)})")
        stats.bytes_removed += item_size
        if path.is_dir() and not path.is_symlink():
            stats.dirs_removed += 1
        else:
            stats.files_removed += 1
        return

    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
            stats.dirs_removed += 1
        else:
            path.unlink()
            stats.files_removed += 1
        stats.bytes_removed += item_size
        print(f"removed: {path} ({format_bytes(item_size)})")
    except (PermissionError, OSError) as exc:
        stats.errors += 1
        print(f"[error] could not remove {path}: {exc}", file=sys.stderr)


def clean_target(target: CleanupTarget, cutoff: float | None, dry_run: bool, stats: CleanupStats) -> None:
    print(f"\n== {target.label}: {target.path}")

    try:
        children = list(target.path.iterdir())
    except (PermissionError, OSError) as exc:
        stats.skipped += 1
        print(f"[skip] cannot inspect {target.path}: {exc}", file=sys.stderr)
        return

    for child in children:
        if not is_old_enough(child, cutoff):
            stats.skipped += 1
            continue
        remove_path(child, dry_run, stats)


def format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clean common temporary and cache files safely.")
    parser.add_argument("--list", action="store_true", help="List cleanup targets and exit.")
    parser.add_argument("--execute", action="store_true", help="Actually delete files. Omit for dry-run mode.")
    parser.add_argument(
        "--min-age-days",
        type=float,
        default=7.0,
        help="Only remove items at least this many days old. Use 0 to include all items.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.min_age_days < 0:
        print("--min-age-days must be non-negative", file=sys.stderr)
        return 2

    targets = discover_targets()
    if args.list:
        for target in targets:
            print(f"{target.label}: {target.path}")
        return 0

    cutoff = time.time() - args.min_age_days * 24 * 60 * 60
    stats = CleanupStats()
    dry_run = not args.execute

    if dry_run:
        print("Dry-run mode. Re-run with --execute to delete files.")

    for target in targets:
        clean_target(target, cutoff, dry_run, stats)

    print("\nSummary")
    print(f"Files: {stats.files_removed}")
    print(f"Directories: {stats.dirs_removed}")
    print(f"Estimated space: {format_bytes(stats.bytes_removed)}")
    print(f"Skipped newer items: {stats.skipped}")
    print(f"Errors: {stats.errors}")
    return 1 if stats.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
