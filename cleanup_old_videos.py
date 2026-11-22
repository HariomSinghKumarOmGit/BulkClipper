#!/usr/bin/env python3
"""
cleanup_old_videos.py - Standalone Utility to Delete YouTube Loaded Videos & Assets Older than 2 Days

Features:
- Deletes all YouTube downloaded/loaded videos, subtitles, audio files, and thumbnails older than 2 days.
- Automatically cleans expired preview files in offline_previews/ (> 2 days) to free gigabytes of space.
- Automatically clears references in clipper_config.json if an expired file was actively loaded.
- Standalone CLI support for manual runs, cron jobs, and launchd daemons on macOS.

Usage:
    python3 cleanup_old_videos.py               # Delete files older than 2 days (48 hours)
    python3 cleanup_old_videos.py --days 2      # Custom retention days
    python3 cleanup_old_videos.py --all         # Delete all YouTube downloaded videos immediately
    python3 cleanup_old_videos.py --dry-run     # Preview what would be deleted without deleting
"""

import os
import sys
import argparse
from pathlib import Path

# Add project root to path and auto-switch to local .venv if available
BASE_DIR = Path(__file__).resolve().parent
venv_python = BASE_DIR / ".venv" / "bin" / "python3"
if venv_python.exists() and sys.executable != str(venv_python) and not os.environ.get("_INSIDE_MYCLIPER_VENV"):
    os.environ["_INSIDE_MYCLIPER_VENV"] = "1"
    os.execv(str(venv_python), [str(venv_python)] + sys.argv)

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from yt_downloader import cleanup_expired_youtube_videos, DOWNLOADS_DIR, format_bytes, DEFAULT_EXPIRY_DAYS


def main():
    parser = argparse.ArgumentParser(
        description="Delete YouTube downloaded/loaded videos, subtitles, audio, and previews older than 2 days."
    )
    parser.add_argument(
        "--days",
        type=float,
        default=DEFAULT_EXPIRY_DAYS,
        help="Age threshold in days. Files older than this are deleted (default: 2.0 days / 48 hours)."
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Delete all YouTube downloaded videos and previews immediately, regardless of age."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate deletion without actually removing files."
    )
    parser.add_argument(
        "--no-previews",
        action="store_true",
        help="Do not clean offline_previews directory."
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Suppress output unless errors occur."
    )

    args = parser.parse_args()

    days = 0.0 if args.all else args.days
    verbose = not args.quiet

    if verbose:
        print("=" * 65)
        print("🧹 YouTube Video & Asset Expiry Cleanup")
        if args.dry_run:
            print("⚠️  DRY RUN MODE: No files will be deleted")
        if args.all:
            print("Target: ALL YouTube downloaded videos & previews (0-day retention)")
        else:
            print(f"Target: YouTube downloaded videos older than {days:.1f} days (48 hours)")
        print(f"Directory: {DOWNLOADS_DIR}")
        print("=" * 65)

    result = cleanup_expired_youtube_videos(
        max_age_days=days,
        clean_previews=not args.no_previews,
        dry_run=args.dry_run,
        verbose=verbose,
    )

    count = result["deleted_count"]
    freed = result["freed_str"]

    if verbose:
        print("-" * 65)
        if count == 0:
            print(f"✅ No files older than {days:.1f} days were found. Everything is clean.")
        else:
            action = "Would delete" if args.dry_run else "Successfully deleted"
            print(f"🎉 {action} {count} expired file(s), freeing {freed} of disk space.")
        print("-" * 65)

    if result.get("errors"):
        for err in result["errors"]:
            print(f"⚠️  {err}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
