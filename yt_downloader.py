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
import urllib.request
import threading
from pathlib import Path
from typing import Optional, Callable, Dict, Any

# Auto-switch to local .venv if executed with system python
BASE_DIR = Path(__file__).resolve().parent
venv_python = BASE_DIR / ".venv" / "bin" / "python3"
if venv_python.exists() and sys.executable != str(venv_python) and not os.environ.get("_INSIDE_MYCLIPER_VENV"):
    os.environ["_INSIDE_MYCLIPER_VENV"] = "1"
    os.execv(str(venv_python), [str(venv_python)] + sys.argv)

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

from sub_utils import load_any_subtitles, generate_speech_timed_srt, find_matching_subtitles, clean_and_deoverlap_subtitles
from clip_tester import extract_thumbnail

BASE_DIR = Path(__file__).resolve().parent
DOWNLOADS_DIR = BASE_DIR / "downloaded_videos"
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
AUDIO_DOWNLOADS_DIR = DOWNLOADS_DIR / "audio"
AUDIO_DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)


def get_node_bin() -> Optional[str]:
    """Finds node executable across common macOS and Linux paths."""
    candidates = [
        shutil.which("node"),
        "/opt/homebrew/bin/node",
        "/usr/local/bin/node",
        "/usr/bin/node",
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return None


def get_ffmpeg_bin() -> Optional[str]:
    """Finds ffmpeg executable across common macOS and Linux paths."""
    candidates = [
        shutil.which("ffmpeg"),
        "/opt/homebrew/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        "/usr/bin/ffmpeg",
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return None


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


DEFAULT_EXPIRY_DAYS: float = 2.0


def cleanup_expired_youtube_videos(
    max_age_days: float = DEFAULT_EXPIRY_DAYS,
    downloads_dir: Optional[Path] = None,
    previews_dir: Optional[Path] = None,
    clean_previews: bool = True,
    dry_run: bool = False,
    verbose: bool = True,
) -> Dict[str, Any]:
    """
    Deletes all YouTube loaded/downloaded videos, subtitles, audio files, thumbnails,
    and associated previews older than max_age_days (default: 2.0 days / 48 hours).

    Also updates clipper_config.json if any actively loaded path in the config
    was deleted so the application does not point to missing files.

    Returns:
        Dict with deleted_count, freed_bytes, freed_str, deleted_files, and errors.
    """
    target_dl = Path(downloads_dir) if downloads_dir else DOWNLOADS_DIR
    target_prev = Path(previews_dir) if previews_dir else BASE_DIR / "offline_previews"

    max_age_seconds = float(max_age_days) * 86400.0
    now = time.time()

    deleted_files = []
    freed_bytes = 0
    errors = []

    # 1. Clean downloaded_videos (recursively scanning all files including audio/)
    if target_dl.exists():
        for file_path in list(target_dl.rglob("*")):
            if not file_path.is_file():
                continue
            if file_path.name in [".gitkeep"]:
                continue

            try:
                stat = file_path.stat()
                file_time = stat.st_mtime
                age_seconds = now - file_time

                if age_seconds >= max_age_seconds:
                    file_size = stat.st_size
                    if not dry_run:
                        file_path.unlink()
                    deleted_files.append(str(file_path))
                    freed_bytes += file_size
                    if verbose:
                        print(f"[yt_downloader] Cleaned up expired YouTube file (> {max_age_days:.1f}d): {file_path.name} ({format_bytes(file_size)})")
            except Exception as e:
                errors.append(f"Failed to delete {file_path.name}: {e}")
                if verbose:
                    print(f"[yt_downloader] Error deleting {file_path.name}: {e}", file=sys.stderr)

        # Remove any empty subdirectories (keeping audio/ intact)
        for d in list(target_dl.glob("*")):
            if d.is_dir() and d.name not in ["audio", ".git"]:
                try:
                    if not any(d.iterdir()):
                        if not dry_run:
                            d.rmdir()
                except Exception:
                    pass

    # 2. Clean offline_previews generated from loaded videos (> max_age_days)
    if clean_previews and target_prev.exists():
        for prev_file in list(target_prev.glob("*")):
            if not prev_file.is_file() or prev_file.name in [".gitkeep", ".DS_Store"]:
                continue
            if prev_file.suffix.lower() in [".mp4", ".mov", ".srt", ".png", ".jpg"]:
                try:
                    stat = prev_file.stat()
                    file_time = stat.st_mtime
                    age_seconds = now - file_time

                    if age_seconds >= max_age_seconds:
                        file_size = stat.st_size
                        if not dry_run:
                            prev_file.unlink()
                        deleted_files.append(str(prev_file))
                        freed_bytes += file_size
                        if verbose:
                            print(f"[yt_downloader] Cleaned up expired preview file (> {max_age_days:.1f}d): {prev_file.name} ({format_bytes(file_size)})")
                except Exception as e:
                    errors.append(f"Failed to delete preview {prev_file.name}: {e}")

    # 3. Clean temporary frame in root if older than max_age_days
    temp_frame = BASE_DIR / ".temp_raw_frame.jpg"
    if temp_frame.exists() and temp_frame.is_file():
        try:
            stat = temp_frame.stat()
            if (now - stat.st_mtime) >= max_age_seconds:
                if not dry_run:
                    temp_frame.unlink()
        except Exception:
            pass

    # 4. Safely synchronize clipper_config.json if deleted files were currently saved
    if deleted_files and not dry_run:
        try:
            config_file = BASE_DIR / "clipper_config.json"
            if config_file.exists():
                import json
                with open(config_file, "r", encoding="utf-8") as f_cfg:
                    cfg_data = json.load(f_cfg)

                cfg_modified = False
                deleted_set = set(os.path.abspath(p) for p in deleted_files)

                # Reset shorts_config.video_path if it was deleted
                s_vid = cfg_data.get("shorts_config", {}).get("video_path")
                if s_vid and os.path.abspath(s_vid) in deleted_set:
                    cfg_data["shorts_config"]["video_path"] = ""
                    cfg_modified = True

                # Reset videos_config.video_path if it was deleted
                v_vid = cfg_data.get("videos_config", {}).get("video_path")
                if v_vid and os.path.abspath(v_vid) in deleted_set:
                    cfg_data["videos_config"]["video_path"] = ""
                    cfg_modified = True

                # Reset subtitles.path if it was deleted
                sub_path = cfg_data.get("subtitles", {}).get("path")
                if sub_path and os.path.abspath(sub_path) in deleted_set:
                    cfg_data["subtitles"]["path"] = ""
                    cfg_modified = True

                # Reset music.path if it was deleted
                m_path = cfg_data.get("music", {}).get("path")
                if m_path and os.path.abspath(m_path) in deleted_set:
                    cfg_data["music"]["path"] = ""
                    cfg_modified = True

                if cfg_modified:
                    with open(config_file, "w", encoding="utf-8") as f_cfg:
                        json.dump(cfg_data, f_cfg, indent=2, ensure_ascii=False)
                    if verbose:
                        print("[yt_downloader] Synced clipper_config.json: cleared references to deleted expired files.")
        except Exception as e:
            if verbose:
                print(f"[yt_downloader] Notice syncing config: {e}", file=sys.stderr)

    return {
        "deleted_count": len(deleted_files),
        "freed_bytes": freed_bytes,
        "freed_str": format_bytes(freed_bytes),
        "deleted_files": deleted_files,
        "errors": errors,
    }


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
        auto_cleanup_days: Optional[float] = DEFAULT_EXPIRY_DAYS,
    ):
        self.output_dir = Path(output_dir) if output_dir else DOWNLOADS_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.progress_callback = progress_callback
        self.completion_callback = completion_callback
        self.error_callback = error_callback
        self.auto_cleanup_days = auto_cleanup_days

        self._cancel_requested = False
        self._current_ydl = None
        self._download_thread: Optional[threading.Thread] = None
        self.downloaded_filepath: Optional[str] = None
        self.caption_filepath: Optional[str] = None
        self.thumbnail_filepath: Optional[str] = None
        self.downloaded_audio_path: Optional[str] = None
        self.video_title: str = ""
        self._current_phase = "init"  # 'video', 'audio', 'merging', 'done'
        self._last_percent: float = 0.0

        # Auto-delete YouTube loaded videos and assets older than 2 days
        if self.auto_cleanup_days is not None and self.auto_cleanup_days >= 0:
            try:
                cleanup_expired_youtube_videos(
                    max_age_days=self.auto_cleanup_days,
                    downloads_dir=self.output_dir,
                    verbose=False
                )
            except Exception:
                pass

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

            self._last_percent = min(max(percent, 0.0), 99.5)
            self._emit_progress({
                "status": "downloading",
                "percent": self._last_percent,
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

    def download(
        self,
        url: str,
        quality: str = "best",
        extract_captions: bool = True
    ) -> Optional[str]:
        """
        Synchronously downloads the video at the given URL in the selected quality,
        extracts captions/subtitles if available, extracts thumbnail,
        merges video + audio locally with FFmpeg, and returns the path to the merged file.
        """
        if not yt_dlp:
            err = "yt-dlp is not installed. Please run: pip install yt-dlp"
            if self.error_callback:
                self.error_callback(err)
            return None

        self._cancel_requested = False
        self.downloaded_filepath = None
        self.caption_filepath = None
        self.thumbnail_filepath = None
        self.video_title = ""

        # Auto-delete YouTube loaded videos older than 2 days
        if self.auto_cleanup_days is not None and self.auto_cleanup_days >= 0:
            try:
                cleanup_expired_youtube_videos(
                    max_age_days=self.auto_cleanup_days,
                    downloads_dir=self.output_dir,
                    verbose=False
                )
            except Exception:
                pass

        # Locate ffmpeg
        ffmpeg_bin = get_ffmpeg_bin()
        if not ffmpeg_bin:
            err = "FFmpeg was not found in PATH or at /opt/homebrew/bin/ffmpeg or /usr/local/bin/ffmpeg."
            if self.error_callback:
                self.error_callback(err)
            return None

        # Build format specifier based on quality selection
        q_lower = quality.lower()
        if "1080" in q_lower:
            fmt = "bestvideo[height<=1080]+bestaudio/best"
        elif "720" in q_lower:
            fmt = "bestvideo[height<=720]+bestaudio/best"
        else:
            fmt = "bestvideo+bestaudio/best"

        outtmpl = str(self.output_dir / "%(title)s.%(ext)s")

        ydl_opts: Dict[str, Any] = {
            "format": fmt,
            "merge_output_format": "mp4",
            "outtmpl": outtmpl,
            "ffmpeg_location": ffmpeg_bin,
            "progress_hooks": [self._progress_hook],
            "postprocessor_hooks": [self._postprocessor_hook],
            "nocheckcertificate": True,
            "quiet": True,
            "no_warnings": False,
            "windowsfilenames": True,
            "restrictfilenames": False,
            "updatetime": False,
            # Robust networking and chunking to prevent "read operation timed out"
            "socket_timeout": 30,
            "retries": 20,
            "fragment_retries": 20,
            "file_access_retries": 5,
            "extractor_retries": 5,
            "http_chunk_size": 10485760,  # 10MB chunking bypasses YouTube connection throttling
            "buffersize": 1024 * 64,
            "continuedl": True,
        }

        # Check for Node.js to solve JS challenges if present
        node_bin = get_node_bin()
        if node_bin:
            ydl_opts["js_runtimes"] = {"node": {"path": node_bin}}
            ydl_opts["remote_components"] = {"ejs:github"}

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
                    "message": f"Starting download: '{self.video_title}' ({dur_str})",
                    "title": self.video_title,
                })

                # Resilient download loop: automatically resume on transient network drops/read timeouts
                max_attempts = 4
                for attempt in range(1, max_attempts + 1):
                    try:
                        ydl.download([url])
                        break
                    except Exception as dl_err:
                        if self._cancel_requested:
                            raise
                        err_text = str(dl_err).lower()
                        is_resumable_net = any(k in err_text for k in [
                            "timed out", "timeout", "read operation", "connection reset",
                            "broken pipe", "remote disconnected", "temporary failure",
                            "network is unreachable", "connection refused", "httperror"
                        ])
                        if is_resumable_net and attempt < max_attempts:
                            print(f"[YouTubeDownloader] Download interrupted by network timeout ({dl_err}). Automatically resuming from partial file (Attempt {attempt + 1}/{max_attempts})...", file=sys.stderr)
                            self._emit_progress({
                                "status": "retrying",
                                "percent": self._last_percent,
                                "speed": "",
                                "eta": "",
                                "size": "",
                                "message": f"⚠️ Network stalled. Resuming download... (Attempt {attempt + 1}/{max_attempts})",
                                "title": self.video_title,
                            })
                            time.sleep(2)
                            continue
                        raise

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
                    matches = list(self.output_dir.glob(f"{glob_escape(p.stem)}.*"))
                    video_matches = [m for m in matches if m.suffix.lower() in [".mp4", ".mkv", ".mov", ".webm"]]
                    if video_matches:
                        final_path = str(video_matches[0])
                    else:
                        raise RuntimeError(f"Download completed but merged output file not found at {p.stem}")

                self.downloaded_filepath = final_path

                # Subtitle processing: Check for subtitles gracefully without risking download failure
                found_srt = None
                if extract_captions:
                    found_srt = self._try_fetch_video_subtitles(url, final_path)
                else:
                    found_srt = find_matching_subtitles(final_path)

                self.caption_filepath = found_srt

                # Thumbnail generation/extraction
                thumb_target = self.output_dir / f"{p.stem}_thumb.jpg"
                if not thumb_target.exists():
                    try:
                        extract_thumbnail(final_path, str(thumb_target), offset_sec=min(3.0, max(0.5, dur / 4.0)))
                    except Exception:
                        pass
                if thumb_target.exists():
                    self.thumbnail_filepath = str(thumb_target)

                res_str = f"{info.get('width', 1920)}x{info.get('height', 1080)}"
                self._emit_progress({
                    "status": "completed",
                    "percent": 100.0,
                    "speed": "",
                    "eta": "00:00",
                    "size": format_bytes(Path(final_path).stat().st_size),
                    "message": f"✅ Download Complete! Merged locally into: {Path(final_path).name}",
                    "title": self.video_title,
                    "filepath": final_path,
                    "caption_path": self.caption_filepath,
                    "thumbnail_path": self.thumbnail_filepath,
                    "duration": dur,
                    "resolution": res_str,
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

    def _try_fetch_video_subtitles(self, url: str, video_path: str) -> Optional[str]:
        """
        Attempts to fetch subtitles for a downloaded video.
        Guaranteed to NEVER crash, throw, or fail the video download on HTTP 429 or network errors.
        """
        try:
            # 1. First check if a matching subtitle already exists locally
            existing = find_matching_subtitles(video_path)
            if existing and os.path.exists(existing):
                return existing

            # 2. Try fetching only the most relevant subtitle tracks (NEVER 'all')
            target_dir = Path(video_path).parent
            p = Path(video_path)
            sub_outtmpl = str(target_dir / f"{p.stem}.%(ext)s")

            node_bin = get_node_bin()
            sub_opts: Dict[str, Any] = {
                "skip_download": True,
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitleslangs": ["hi-orig", "hi", "en-orig", "en", ".*orig.*"],
                "subtitlesformat": "srt/vtt/best",
                "outtmpl": sub_outtmpl,
                "quiet": True,
                "no_warnings": True,
                "nocheckcertificate": True,
                "windowsfilenames": True,
                "ignoreerrors": True,
                "updatetime": False,
                "socket_timeout": 30,
                "retries": 10,
            }
            if node_bin:
                sub_opts["js_runtimes"] = {"node": {"path": node_bin}}
                sub_opts["remote_components"] = {"ejs:github"}

            try:
                with yt_dlp.YoutubeDL(sub_opts) as ydl:
                    ydl.download([url])
            except Exception as e:
                # Catch 429 or other subtitle errors without crashing
                print(f"[yt_downloader] YouTube subtitle query note: {e}")

            # Check if any subtitles were written to disk and convert if needed
            matched = find_matching_subtitles(video_path)
            if matched and os.path.exists(matched):
                try:
                    s_obj = load_any_subtitles(matched)
                    c_obj = clean_and_deoverlap_subtitles(s_obj, overflow=False)
                    c_obj.save(matched, encoding="utf-8")
                except Exception:
                    pass
            return matched

        except Exception as e:
            print(f"[yt_downloader] _try_fetch_video_subtitles error (non-fatal): {e}")
            return None

    def download_audio(self, url: str, output_dir: Optional[Path] = None) -> Optional[str]:
        """
        Downloads the best audio from the given YouTube URL, extracts it to high quality MP3
        via FFmpeg, and returns the path to the downloaded MP3.
        """
        if not yt_dlp:
            err = "yt-dlp is not installed. Please run: pip install yt-dlp"
            if self.error_callback:
                self.error_callback(err)
            return None

        self._cancel_requested = False
        target_dir = Path(output_dir) if output_dir else AUDIO_DOWNLOADS_DIR
        target_dir.mkdir(parents=True, exist_ok=True)

        if self.auto_cleanup_days is not None and self.auto_cleanup_days >= 0:
            try:
                cleanup_expired_youtube_videos(
                    max_age_days=self.auto_cleanup_days,
                    downloads_dir=target_dir.parent if target_dir.name == "audio" else target_dir,
                    verbose=False
                )
            except Exception:
                pass

        ffmpeg_bin = get_ffmpeg_bin()
        outtmpl = str(target_dir / "%(title)s.%(ext)s")

        ydl_opts: Dict[str, Any] = {
            "format": "bestaudio/best",
            "outtmpl": outtmpl,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
            "ffmpeg_location": ffmpeg_bin,
            "progress_hooks": [self._progress_hook],
            "nocheckcertificate": True,
            "quiet": True,
            "no_warnings": False,
            "windowsfilenames": True,
            "restrictfilenames": False,
            "updatetime": False,
            # Robust networking and chunking to prevent read timeouts
            "socket_timeout": 30,
            "retries": 20,
            "fragment_retries": 20,
            "file_access_retries": 5,
            "extractor_retries": 5,
            "http_chunk_size": 10485760,
            "buffersize": 1024 * 64,
            "continuedl": True,
        }

        node_bin = get_node_bin()
        if node_bin:
            ydl_opts["js_runtimes"] = {"node": {"path": node_bin}}
            ydl_opts["remote_components"] = {"ejs:github"}

        try:
            self._emit_progress({
                "status": "fetching_info",
                "percent": 0.0,
                "speed": "",
                "eta": "",
                "size": "",
                "message": "🔍 Fetching YouTube audio stream...",
                "title": "",
            })

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                self._current_ydl = ydl
                info = ydl.extract_info(url, download=False)
                if not info:
                    raise RuntimeError("Could not extract audio information from the provided link.")

                audio_title = info.get("title", "Unknown Audio")
                dur = info.get("duration", 0)

                self._emit_progress({
                    "status": "starting",
                    "percent": 5.0,
                    "speed": "",
                    "eta": "",
                    "size": "",
                    "message": f"Downloading audio: '{audio_title}'",
                    "title": audio_title,
                })

                # Resilient audio download loop
                max_attempts = 4
                for attempt in range(1, max_attempts + 1):
                    try:
                        ydl.download([url])
                        break
                    except Exception as dl_err:
                        if self._cancel_requested:
                            raise
                        err_text = str(dl_err).lower()
                        is_resumable_net = any(k in err_text for k in [
                            "timed out", "timeout", "read operation", "connection reset",
                            "broken pipe", "remote disconnected", "temporary failure",
                            "network is unreachable", "connection refused", "httperror"
                        ])
                        if is_resumable_net and attempt < max_attempts:
                            print(f"[YouTubeDownloader] Audio download interrupted ({dl_err}). Resuming (Attempt {attempt + 1}/{max_attempts})...", file=sys.stderr)
                            self._emit_progress({
                                "status": "retrying",
                                "percent": self._last_percent,
                                "speed": "",
                                "eta": "",
                                "size": "",
                                "message": f"⚠️ Audio network stalled. Resuming download... (Attempt {attempt + 1}/{max_attempts})",
                                "title": audio_title,
                            })
                            time.sleep(2)
                            continue
                        raise

                raw_filename = ydl.prepare_filename(info)
                p = Path(raw_filename)
                mp3_cand = p.with_suffix(".mp3")

                if mp3_cand.exists():
                    final_path = str(mp3_cand)
                else:
                    matches = list(target_dir.glob(f"{glob_escape(p.stem)}*.mp3"))
                    if matches:
                        final_path = str(matches[0])
                    else:
                        final_path = str(p)

                self.downloaded_audio_path = final_path

                self._emit_progress({
                    "status": "completed",
                    "percent": 100.0,
                    "speed": "",
                    "eta": "00:00",
                    "size": format_bytes(Path(final_path).stat().st_size) if Path(final_path).exists() else "",
                    "message": f"✅ Audio Ready: {Path(final_path).name}",
                    "title": audio_title,
                    "filepath": final_path,
                    "audio_path": final_path,
                    "duration": dur,
                })

                return final_path

        except Exception as e:
            if self._cancel_requested:
                msg = "Audio download cancelled."
                if self.error_callback:
                    self.error_callback(msg)
                return None

            err_str = str(e)
            print(f"[YouTubeDownloader] Audio download error: {err_str}", file=sys.stderr)
            self._emit_progress({
                "status": "error",
                "percent": 0.0,
                "speed": "",
                "eta": "",
                "size": "",
                "message": f"❌ Audio Error: {err_str}",
                "title": "",
            })
            if self.error_callback:
                self.error_callback(err_str)
            return None
        finally:
            self._current_ydl = None

    def download_subtitles(
        self,
        url: str,
        lang: str = "en",
        as_txt: bool = False,
        output_dir: Optional[Path] = None
    ) -> Optional[str]:
        """
        Downloads available subtitles/captions from the given YouTube URL in SRT or plain TXT format,
        without downloading the full video stream.
        """
        if not yt_dlp:
            err = "yt-dlp is not installed. Please run: pip install yt-dlp"
            if self.error_callback:
                self.error_callback(err)
            return None

        self._cancel_requested = False
        target_dir = Path(output_dir) if output_dir else DOWNLOADS_DIR
        target_dir.mkdir(parents=True, exist_ok=True)

        lang_code = "en"
        if "hi" in lang.lower() or "hindi" in lang.lower():
            lang_code = "hi"
        elif "es" in lang.lower() or "spanish" in lang.lower():
            lang_code = "es"
        elif "fr" in lang.lower() or "french" in lang.lower():
            lang_code = "fr"
        elif "de" in lang.lower() or "german" in lang.lower():
            lang_code = "de"
        elif "all" in lang.lower():
            lang_code = "all"

        outtmpl = str(target_dir / "%(title)s.%(ext)s")
        if lang_code == "all":
            sub_langs = ["hi-orig", "hi", "en-orig", "en", ".*orig.*"]
        else:
            sub_langs = [f"{lang_code}-orig", lang_code, f"{lang_code}.*", "hi-orig", "hi", "en-orig", "en", ".*orig.*"]

        ydl_opts: Dict[str, Any] = {
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": sub_langs,
            "subtitlesformat": "srt/vtt/best",
            "outtmpl": outtmpl,
            "quiet": True,
            "no_warnings": True,
            "nocheckcertificate": True,
            "windowsfilenames": True,
            "ignoreerrors": True,
            "updatetime": False,
            "socket_timeout": 30,
            "retries": 10,
        }

        node_bin = get_node_bin()
        if node_bin:
            ydl_opts["js_runtimes"] = {"node": {"path": node_bin}}
            ydl_opts["remote_components"] = {"ejs:github"}

        info = None
        p = None
        try:
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    self._current_ydl = ydl
                    info = ydl.extract_info(url, download=True)
                    if info:
                        raw_filename = ydl.prepare_filename(info)
                        p = Path(raw_filename)
            except Exception as e:
                print(f"[yt_downloader] Subtitle extraction note: {e}", file=sys.stderr)

            # Look for matching subtitle files
            found_srt = None
            if p:
                found_srt = find_matching_subtitles(str(p))

            if not found_srt:
                for recent in sorted(target_dir.glob("*.srt"), key=lambda f: f.stat().st_mtime, reverse=True):
                    if (time.time() - recent.stat().st_mtime) < 30 and recent.stat().st_size > 50:
                        found_srt = str(recent)
                        break

            if not found_srt:
                for recent_vtt in sorted(target_dir.glob("*.vtt"), key=lambda f: f.stat().st_mtime, reverse=True):
                    if (time.time() - recent_vtt.stat().st_mtime) < 30 and recent_vtt.stat().st_size > 50:
                        target_srt = recent_vtt.with_suffix(".srt")
                        try:
                            subs = load_any_subtitles(str(recent_vtt))
                            subs.save(str(target_srt), encoding="utf-8")
                            found_srt = str(target_srt)
                            break
                        except Exception as e:
                            print(f"[yt_downloader] Failed converting {recent_vtt.name}: {e}")

            if not found_srt:
                raise RuntimeError(f"No subtitles found on YouTube for language '{lang}'.")

            # Clean and de-overlap found subtitles
            try:
                s_obj = load_any_subtitles(found_srt)
                c_obj = clean_and_deoverlap_subtitles(s_obj, overflow=False)
                c_obj.save(found_srt, encoding="utf-8")
            except Exception:
                pass

            if as_txt:
                txt_path = Path(found_srt).with_suffix(".txt")
                try:
                    import pysrt
                    subs = pysrt.open(found_srt, encoding="utf-8")
                    lines = []
                    last_line = ""
                    for s in subs:
                        t = s.text.strip().replace("\n", " ")
                        if t and t != last_line:
                            lines.append(t)
                            last_line = t
                    with open(txt_path, "w", encoding="utf-8") as f_out:
                        f_out.write("\n\n".join(lines))
                    return str(txt_path)
                except Exception as e:
                    print(f"[yt_downloader] TXT conversion error: {e}")
                    return found_srt

            return found_srt
        except Exception as e:
            err_str = str(e)
            print(f"[YouTubeDownloader] Subtitle error: {err_str}", file=sys.stderr)
            if self.error_callback:
                self.error_callback(err_str)
            return None
        finally:
            self._current_ydl = None

    def fetch_info(self, url: str) -> Optional[Dict[str, Any]]:
        """Extracts metadata without downloading media."""
        if not yt_dlp:
            return None
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "socket_timeout": 30,
            "retries": 10,
        }
        node_bin = get_node_bin()
        if node_bin:
            ydl_opts["js_runtimes"] = {"node": {"path": node_bin}}
            ydl_opts["remote_components"] = {"ejs:github"}
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    return None
                dur = info.get("duration", 0)
                m, s = divmod(int(dur), 60)
                dur_str = f"{m}m {s}s" if m < 60 else f"{m // 60}h {m % 60}m"
                w = info.get("width", 0)
                h = info.get("height", 0)
                res = f"{w}x{h}" if (w and h) else "HD"
                return {
                    "title": info.get("title", ""),
                    "duration": dur,
                    "duration_str": dur_str,
                    "thumbnail": info.get("thumbnail", ""),
                    "width": w,
                    "height": h,
                    "resolution": res,
                }
        except Exception as e:
            print(f"[YouTubeDownloader] fetch_info error: {e}", file=sys.stderr)
            return None

    def start_download_async(
        self,
        url: str,
        quality: str = "best",
        extract_captions: bool = True
    ) -> threading.Thread:
        """Starts the video download in a background thread."""
        thread = threading.Thread(
            target=self.download,
            args=(url, quality, extract_captions),
            daemon=True
        )
        self._download_thread = thread
        thread.start()
        return thread

    def start_download_audio_async(self, url: str) -> threading.Thread:
        """Starts the audio download in a background thread."""
        thread = threading.Thread(
            target=self.download_audio,
            args=(url,),
            daemon=True
        )
        self._download_thread = thread
        thread.start()
        return thread

    def start_download_subtitles_async(
        self,
        url: str,
        lang: str = "en",
        as_txt: bool = False,
        output_dir: Optional[Path] = None
    ) -> threading.Thread:
        """Starts subtitle download in a background thread."""
        thread = threading.Thread(
            target=self.download_subtitles,
            args=(url, lang, as_txt, output_dir),
            daemon=True
        )
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
    """CLI Runner: python3 yt_downloader.py <URL> [-o output_directory] [-m music_path] [--volume 0.15]
                   python3 yt_downloader.py --cleanup [days]
    """
    import argparse
    if len(sys.argv) < 2:
        print("Usage: python3 yt_downloader.py <YouTube-URL> [-o output_directory] [-m music_path] [--volume 0.15]")
        print("       python3 yt_downloader.py --cleanup [days]")
        print(f"Default download directory: {DOWNLOADS_DIR}")
        print("Note: YouTube loaded videos & assets older than 2 days are automatically deleted.")
        sys.exit(1)

    if sys.argv[1].strip() in ["--cleanup", "-c", "--clean"]:
        days = DEFAULT_EXPIRY_DAYS
        if len(sys.argv) > 2:
            try:
                days = float(sys.argv[2])
            except ValueError:
                pass
        print("=" * 65)
        print(f"🧹 YouTube Video Expiry Cleanup (> {days:.1f} days retention)")
        print(f"Directory: {DOWNLOADS_DIR}")
        print("=" * 65)
        res = cleanup_expired_youtube_videos(max_age_days=days, verbose=True)
        print(f"Done: {res['deleted_count']} file(s) deleted, {res['freed_str']} freed.")
        return

    parser = argparse.ArgumentParser(description="YouTube Highest Quality Video & Audio Downloader")
    parser.add_argument("url", help="YouTube video URL")
    parser.add_argument("-o", "--output", help="Output directory", default=str(DOWNLOADS_DIR))
    parser.add_argument("--quality", default="best", help="Download quality (best, 1080p, 720p)")

    args = parser.parse_args()

    url = args.url
    out_dir = Path(args.output)

    print("=" * 65)
    print("🎬 YouTube Highest Quality Video & Audio Downloader (Offline)")
    print(f"URL: {url}")
    print(f"Destination: {out_dir}")
    print(f"Retention Policy: Automatically delete videos older than {DEFAULT_EXPIRY_DAYS:.0f} days")
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
        elif status in ["merging", "processing", "fetching_info", "starting", "retrying"]:
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
        auto_cleanup_days=DEFAULT_EXPIRY_DAYS,
    )

    downloader.download(url, quality=args.quality)


if __name__ == "__main__":
    main()
