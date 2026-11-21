#!/usr/bin/env python3
"""
download_videos.py - YouTube Video Downloader CLI & Utility for Clipper

Convenient wrapper around yt_downloader.py to download any YouTube link
in highest quality, merging video and audio locally into the 'downloaded_videos' folder.

Usage:
    python3 download_videos.py "https://www.youtube.com/watch?v=..."
"""

import sys
from yt_downloader import main

if __name__ == "__main__":
    main()
