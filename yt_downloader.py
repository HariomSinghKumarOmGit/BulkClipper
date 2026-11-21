#!/usr/bin/env python3
"""
yt_downloader.py - Highest Quality YouTube Video & Audio Downloader with Local FFmpeg Merge

Features:
  - Downloads highest quality video stream + highest quality audio stream (4K, 1440p, 1080p, 60fps).
  - Automatically merges video and audio locally using FFmpeg into a clean MP4 file.
  - Real-time progress tracking: percentage, speed, ETA, stream info, and merge state.
  - Default storage in 'downloaded_videos' directory within the Clipper folder.
  - Cancellable background downloads with clean cleanup.
  - Standalone CLI support: python3 yt_downloader.py "https://youtu.be/..."
"""

import os
import sys
import time
import shutil
import threading
from pathlib import Path
from typing import Optional, Callable, Dict, Any

# Ensure common macOS Homebrew & system binary paths are available for ffmpeg / ffprobe / yt-dlp
for extra_path in ["/opt/homebrew/bin", "/usr/local/bin"]:
    if extra_path not in os.environ.get("PATH", "") and os.path.exists(extra_path):
        os.environ["PATH"] = extra_path + ":" + os.environ.get("PATH", "")

# Ensure SSL certificates are properly loaded on macOS Python
try:
    import certifi
    os.environ["SSL_CERT_FILE"] = certifi.where()
except ImportError:
    pass

try:
    import yt_dlp
except ImportError:
    print("Error: yt-dlp is required. Please install it with: pip install yt-dlp", file=sys.stderr)
    yt_dlp = None

BASE_DIR = Path(__file__).resolve().parent
DOWNLOADS_DIR = BASE_DIR / "downloaded_videos"
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)


def format_bytes(num_bytes: Optional[float]) -> str:
    """Format bytes to human-readable string (KB, MB, GB)."""
    if num_bytes is None or num_bytes <= 0:
        return "0 MB"
    for unit in ["B", "KB", "MB", "GB"]:
        if num_bytes < 1024.0:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} TB"


def format_eta(seconds: Optional[int]) -> str:
    """Format ETA seconds to mm:ss or hh:mm:ss."""
    if seconds is None or seconds < 0:
        return "--:--"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


class YouTubeDownloader:
    """
    Manages downloading YouTube videos in the highest available quality
    and merging video + audio locally using FFmpeg.
    """

    def __init__(
        self,
        output_dir: Optional[Path] = None,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        completion_callback: Optional[Callable[[str], None]] = None,
        error_callback: Optional[Callable[[str], None]] = None,
    ):
        self.output_dir = Path(output_dir) if output_dir else DOWNLOADS_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.progress_callback = progress_callback
        self.completion_callback = completion_callback
        self.error_callback = error_callback

        self._cancel_requested = False
        self._current_ydl = None
        self._download_thread: Optional[threading.Thread] = None
        self.downloaded_filepath: Optional[str] = None
        self.video_title: str = ""
        self._current_phase = "init"  # 'video', 'audio', 'merging', 'done'

    def cancel(self):
        """Requests cancellation of active download."""
        self._cancel_requested = True
        self._emit_progress({
            "status": "cancelled",
            "percent": 0.0,
            "speed": "0 MB/s",
            "eta": "--:--",
            "size": "",
            "message": "Download cancelled by user",
            "title": self.video_title,
        })

    def _emit_progress(self, data: Dict[str, Any]):
        if self.progress_callback:
            try:
                self.progress_callback(data)
            except Exception as e:
                print(f"[YouTubeDownloader] Progress callback error: {e}", file=sys.stderr)

    def _progress_hook(self, d: Dict[str, Any]):
        if self._cancel_requested:
            raise RuntimeError("Download cancelled by user")

        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes", 0)
            percent = (downloaded / total * 100.0) if total > 0 else 0.0

            speed_val = d.get("speed")
            speed_str = f"{format_bytes(speed_val)}/s" if speed_val else "-- MB/s"
            eta_val = d.get("eta")
            eta_str = format_eta(eta_val)

            size_str = f"{format_bytes(downloaded)} / {format_bytes(total)}" if total > 0 else format_bytes(downloaded)

            filename = d.get("filename", "")
            # Identify whether downloading video or audio stream
            stream_type = "Video"
            if ".f" in filename or "audio" in filename.lower():
                ext = Path(filename).suffix.lower()
                if ext in [".m4a", ".webm", ".opus", ".aac", ".mp3"] or "audio" in filename.lower():
                    stream_type = "Audio"

            self._emit_progress({
                "status": "downloading",
                "percent": min(max(percent, 0.0), 99.5),
                "speed": speed_str,
                "eta": eta_str,
                "size": size_str,
                "stream": stream_type,
                "message": f"Downloading {stream_type} stream: {percent:.1f}% ({speed_str}, ETA {eta_str})",
                "title": self.video_title,
            })

        elif status == "finished":
            self._emit_progress({
                "status": "processing",
                "percent": 99.0,
                "speed": "",
                "eta": "00:00",
                "size": "",
                "message": "Stream download finished. Preparing local FFmpeg merge...",
                "title": self.video_title,
            })

    def _postprocessor_hook(self, d: Dict[str, Any]):
        if self._cancel_requested:
            raise RuntimeError("Download cancelled by user")

        pp_name = d.get("postprocessor", "")
        status = d.get("status")

        if "Merger" in pp_name or "FFmpeg" in pp_name:
            if status == "started":
                self._current_phase = "merging"
                self._emit_progress({
                    "status": "merging",
                    "percent": 99.5,
                    "speed": "",
                    "eta": "",
                    "size": "",
                    "message": "⚡ Merging highest quality Video & Audio with FFmpeg locally...",
                    "title": self.video_title,
                })
            elif status == "finished":
                self._current_phase = "done"

    def download(self, url: str) -> Optional[str]:
        """
        Synchronously downloads the video at the given URL in highest quality,
        merges video + audio locally with FFmpeg, and returns the path to the merged file.
        """
        if not yt_dlp:
            err = "yt-dlp is not installed. Please run: pip install yt-dlp"
            if self.error_callback:
                self.error_callback(err)
            return None

        self._cancel_requested = False
        self.downloaded_filepath = None
        self.video_title = ""

        # Locate ffmpeg
        ffmpeg_bin = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
        if not os.path.exists(ffmpeg_bin):
            err = f"FFmpeg was not found in PATH or at /opt/homebrew/bin/ffmpeg."
            if self.error_callback:
                self.error_callback(err)
            return None

        # Build yt-dlp options for HIGHEST quality video + audio merged
        outtmpl = str(self.output_dir / "%(title)s.%(ext)s")

        ydl_opts: Dict[str, Any] = {
            # Highest quality video + highest quality audio, fallback to best single file
            "format": "bestvideo+bestaudio/best",
            # Merge into standard MP4 for maximum macOS & Apple Silicon compatibility
            "merge_output_format": "mp4",
            "outtmpl": outtmpl,
            "ffmpeg_location": ffmpeg_bin,
            "progress_hooks": [self._progress_hook],
            "postprocessor_hooks": [self._postprocessor_hook],
            "nocheckcertificate": True,
            "quiet": True,
            "no_warnings": False,
            # Windows/Mac safe filename characters
            "windowsfilenames": True,
            "restrictfilenames": False,
        }

        # Check for Node.js to solve JS challenges if present
        node_bin = shutil.which("node") or "/usr/local/bin/node"
        if os.path.exists(node_bin):
            ydl_opts["js_runtimes"] = {"node": {"path": node_bin}}

        try:
            self._emit_progress({
                "status": "fetching_info",
                "percent": 0.0,
                "speed": "",
                "eta": "",
                "size": "",
                "message": "🔍 Fetching YouTube video information & available streams...",
                "title": "",
            })

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                self._current_ydl = ydl

                # Extract info first to get title & duration
                info = ydl.extract_info(url, download=False)
                if not info:
                    raise RuntimeError("Could not extract video information from the provided link.")

                self.video_title = info.get("title", "Unknown Title")
                dur = info.get("duration", 0)
                m, s = divmod(int(dur), 60)
                dur_str = f"{m}m {s}s" if m < 60 else f"{m // 60}h {m % 60}m"

                self._emit_progress({
                    "status": "starting",
                    "percent": 2.0,
                    "speed": "",
                    "eta": "",
                    "size": "",
                    "message": f"Starting highest quality download: '{self.video_title}' ({dur_str})",
                    "title": self.video_title,
                })

                # Perform the download & local FFmpeg merge
                ydl.download([url])

                # Determine final file path
                raw_filename = ydl.prepare_filename(info)
                p = Path(raw_filename)
                mp4_cand = p.with_suffix(".mp4")
                mkv_cand = p.with_suffix(".mkv")

                if mp4_cand.exists():
                    final_path = str(mp4_cand)
                elif mkv_cand.exists():
                    final_path = str(mkv_cand)
                elif p.exists():
                    final_path = str(p)
                else:
                    # Search output dir for matching title
                    matches = list(self.output_dir.glob(f"{glob_escape(p.stem)}.*"))
                    if matches:
                        final_path = str(matches[0])
                    else:
                        raise RuntimeError(f"Download completed but merged output file not found at {p.stem}")

                self.downloaded_filepath = final_path

                self._emit_progress({
                    "status": "completed",
                    "percent": 100.0,
                    "speed": "",
                    "eta": "00:00",
                    "size": format_bytes(Path(final_path).stat().st_size),
                    "message": f"✅ Download Complete! Merged locally into: {Path(final_path).name}",
                    "title": self.video_title,
                    "filepath": final_path,
                })

                if self.completion_callback:
                    self.completion_callback(final_path)

                return final_path

        except Exception as e:
            if self._cancel_requested:
                msg = "Download was cancelled."
                if self.error_callback:
                    self.error_callback(msg)
                return None

            err_str = str(e)
            print(f"[YouTubeDownloader] Download error: {err_str}", file=sys.stderr)
            self._emit_progress({
                "status": "error",
                "percent": 0.0,
                "speed": "",
                "eta": "",
                "size": "",
                "message": f"❌ Error: {err_str}",
                "title": self.video_title,
            })
            if self.error_callback:
                self.error_callback(err_str)
            return None
        finally:
            self._current_ydl = None

    def start_download_async(self, url: str) -> threading.Thread:
        """Starts the download in a background thread."""
        thread = threading.Thread(target=self.download, args=(url,), daemon=True)
        self._download_thread = thread
        thread.start()
        return thread


def glob_escape(pathname: str) -> str:
    """Escape special glob characters."""
    chars_to_escape = ["*", "?", "[", "]"]
    res = []
    for c in pathname:
        if c in chars_to_escape:
            res.append(f"[{c}]")
        else:
            res.append(c)
    return "".join(res)


def main():
    """CLI Runner: python3 yt_downloader.py <URL> [output_directory]"""
    if len(sys.argv) < 2:
        print("Usage: python3 yt_downloader.py <YouTube-URL> [output_directory]")
        print(f"Default download directory: {DOWNLOADS_DIR}")
        sys.exit(1)

    url = sys.argv[1].strip()
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else DOWNLOADS_DIR

    print("=" * 65)
    print("🎬 YouTube Highest Quality Video & Audio Downloader (Offline)")
    print(f"URL: {url}")
    print(f"Destination: {out_dir}")
    print("=" * 65)

    def on_progress(p: Dict[str, Any]):
        status = p.get("status")
        pct = p.get("percent", 0.0)
        msg = p.get("message", "")
        if status == "downloading":
            bar_len = 30
            filled = int(bar_len * (pct / 100.0))
            bar = "█" * filled + "░" * (bar_len - filled)
            sys.stdout.write(f"\r[{bar}] {pct:5.1f}% | {p.get('speed', '')} | ETA: {p.get('eta', '')} | {p.get('size', '')}")
            sys.stdout.flush()
        elif status in ["merging", "processing", "fetching_info", "starting"]:
            sys.stdout.write(f"\n{msg}\n")
            sys.stdout.flush()

    def on_complete(filepath: str):
        print(f"\n\n🎉 SUCCESS! Video saved & merged locally:")
        print(f"📁 Path: {filepath}")

    def on_error(err: str):
        print(f"\n\n❌ DOWNLOAD FAILED: {err}", file=sys.stderr)

    downloader = YouTubeDownloader(
        output_dir=out_dir,
        progress_callback=on_progress,
        completion_callback=on_complete,
        error_callback=on_error,
    )

    downloader.download(url)


if __name__ == "__main__":
    main()
