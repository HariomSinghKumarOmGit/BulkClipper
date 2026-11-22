#!/usr/bin/env python3
"""
download_videos.py - YouTube Video Downloader CLI & Utility for Clipper

Convenient wrapper around yt_downloader.py to download any YouTube link
in highest quality, merging video and audio locally into the 'downloaded_videos' folder.
Purges loaded YouTube videos older than 2 days.

Usage:
    python3 download_videos.py "https://www.youtube.com/watch?v=..."
    python3 download_videos.py --cleanup [days]
"""

import os
import sys
from pathlib import Path

# Auto-switch to local .venv if run with system python
BASE_DIR = Path(__file__).resolve().parent
venv_python = BASE_DIR / ".venv" / "bin" / "python3"
if venv_python.exists() and sys.executable != str(venv_python) and not os.environ.get("_INSIDE_MYCLIPER_VENV"):
    os.environ["_INSIDE_MYCLIPER_VENV"] = "1"
    os.execv(str(venv_python), [str(venv_python)] + sys.argv)

from yt_downloader import main

if __name__ == "__main__":
    main()
