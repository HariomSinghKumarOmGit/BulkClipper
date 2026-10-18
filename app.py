#!/usr/bin/env python3
"""
app.py - Local Web Studio for Video Cropping, Audio Ducking & Subtitle Preview

Launches a sleek local web interface (default port 5050):
  - Interactive aspect ratio selection (9:16, 1:1, 4:5, 16:9) & horizontal crop slider
  - Real-time visual crop framing box
  - SRT caption and background music selection with volume ducking slider
  - 1-minute high-speed preview generation (using Apple Silicon VideoToolbox)
  - HTML5 in-browser video player
  - Instant generation of headless CLI commands to process full videos (even 10+ hours) at maximum speed
"""

import os
import sys
import glob
import json
import time
import uuid
import threading
import subprocess
from pathlib import Path
from typing import Dict, Any, List

from flask import Flask, render_template, request, jsonify, send_from_directory, abort

# Import core multimedia processor functions
from process_full import probe_video, compute_crop_filter, run_pipeline, get_available_encoders

app = Flask(__name__, template_folder="templates")

# Configure directories
BASE_DIR = Path(__file__).resolve().parent
PREVIEW_DIR = BASE_DIR / "static_previews"
PREVIEW_DIR.mkdir(parents=True, exist_ok=True)

# Background jobs registry
ACTIVE_JOBS: Dict[str, Dict[str, Any]] = {}


def format_seconds(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


@app.route("/")
def index():
    encoders = get_available_encoders()
    hw_name = "Apple Silicon VideoToolbox (Hardware Acceleration)" if encoders.get("videotoolbox") else "CPU (libx264 veryfast)"
    return render_template("index.html", hw_name=hw_name, has_hw=encoders.get("videotoolbox", False))


@app.route("/api/scan", methods=["GET"])
def scan_local_files():
    """Scans local directories (workspace, Desktop) for video, audio, and subtitle files."""
    scan_paths = [
        str(BASE_DIR),
        str(Path.home() / "Desktop"),
        str(Path.home() / "Downloads")
    ]
    
    videos = []
    audios = []
    subtitles = []

    video_exts = {".mp4", ".mov", ".mkv", ".m4v", ".webm"}
    audio_exts = {".mp3", ".wav", ".m4a", ".aac", ".ogg"}
    sub_exts = {".srt", ".vtt", ".ass", ".ssa", ".lrc"}

    seen = set()

    for p in scan_paths:
        if not os.path.exists(p):
            continue
        try:
            for root, _, files in os.walk(p):
                # Don't recurse more than 2 levels deep
                depth = root[len(p):].count(os.sep)
                if depth > 1:
                    continue
                for f in files:
                    if f.startswith("."):
                        continue
                    ext = os.path.splitext(f)[1].lower()
                    full = os.path.join(root, f)
                    if full in seen:
                        continue
                    seen.add(full)

                    item = {"name": f, "path": full, "folder": os.path.basename(root) or root}
                    if ext in video_exts:
                        videos.append(item)
                    elif ext in audio_exts:
                        audios.append(item)
                    elif ext in sub_exts:
                        subtitles.append(item)
        except Exception:
            pass

    return jsonify({
        "videos": sorted(videos, key=lambda x: x["name"]),
        "audios": sorted(audios, key=lambda x: x["name"]),
        "subtitles": sorted(subtitles, key=lambda x: x["name"])
    })


@app.route("/api/probe", methods=["POST"])
def probe_file():
    data = request.json or {}
    path = data.get("path", "").strip()
    if not path or not os.path.exists(path):
        return jsonify({"success": False, "error": "File does not exist"}), 400

    try:
        info = probe_video(path)
        info["duration_fmt"] = format_seconds(info["duration"])
        info["success"] = True
        return jsonify(info)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/preview", methods=["POST"])
def generate_preview():
    """Generates a 1-minute (or specified) preview video with the chosen crop, audio mix & captions."""
    data = request.json or {}
    video_path = data.get("video_path", "").strip()
    if not video_path or not os.path.exists(video_path):
        return jsonify({"success": False, "error": "Source video file not found."}), 400

    ratio = data.get("ratio", "9:16")
    pos_x = float(data.get("pos_x", 0.5))
    pos_y = float(data.get("pos_y", 0.5))
    srt_path = data.get("srt_path")
    music_path = data.get("music_path")
    music_volume = float(data.get("music_volume", 0.12))
    start_sec = float(data.get("start_sec", 0.0))
    duration_sec = float(data.get("duration_sec", 60.0))  # Default 1 minute
    font_color = data.get("font_color", "&H00FFFFFF")
    font_size = int(data.get("font_size", 22))

    try:
        v_info = probe_video(video_path)
        
        # Calculate crop filter
        if ratio.lower() not in ["original", "none"]:
            crop_filter, crop_w, crop_h, off_x, off_y = compute_crop_filter(
                in_w=v_info["width"],
                in_h=v_info["height"],
                ratio=ratio,
                pos_x=pos_x,
                pos_y=pos_y
            )
        else:
            crop_filter = None
            crop_w, crop_h, off_x, off_y = v_info["width"], v_info["height"], 0, 0

        # Output preview file
        preview_filename = f"preview_{uuid.uuid4().hex[:8]}.mp4"
        out_preview_path = str(PREVIEW_DIR / preview_filename)

        # Run render
        run_pipeline(
            video_path=video_path,
            output_path=out_preview_path,
            crop_filter=crop_filter,
            srt_path=srt_path if (srt_path and os.path.exists(srt_path)) else None,
            music_path=music_path if (music_path and os.path.exists(music_path)) else None,
            music_volume=music_volume,
            start_sec=start_sec,
            duration_sec=duration_sec,
            use_hw_accel=True,
            font_size=font_size,
            font_color=font_color
        )

        return jsonify({
            "success": True,
            "preview_url": f"/previews/{preview_filename}",
            "crop_filter": crop_filter or "None",
            "crop_dimensions": f"{crop_w}x{crop_h}",
            "crop_offset": f"x={off_x}, y={off_y}"
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/previews/<path:filename>")
def serve_preview(filename):
    """Serves preview videos with conditional streaming (HTTP 206 Partial Content)."""
    return send_from_directory(PREVIEW_DIR, filename, conditional=True)


@app.route("/api/generate-command", methods=["POST"])
def build_cli_command():
    """Builds the exact CLI command to run on the entire video without UI."""
    data = request.json or {}
    video_path = data.get("video_path", "").strip()
    output_path = data.get("output_path", "").strip()
    if not output_path:
        base, ext = os.path.splitext(video_path or "video.mp4")
        output_path = f"{base}_cropped{ext or '.mp4'}"

    ratio = data.get("ratio", "9:16")
    pos_x = float(data.get("pos_x", 0.5))
    srt_path = data.get("srt_path", "").strip()
    music_path = data.get("music_path", "").strip()
    music_volume = float(data.get("music_volume", 0.12))

    cmd_parts = [
        "python3", "process_full.py",
        "-v", f'"{video_path}"',
        "-o", f'"{output_path}"',
        "-r", ratio,
        "--pos-x", f"{pos_x:.2f}"
    ]

    if srt_path and os.path.exists(srt_path):
        cmd_parts.extend(["-s", f'"{srt_path}"'])

    if music_path and os.path.exists(music_path):
        cmd_parts.extend(["-m", f'"{music_path}"', "--volume", f"{music_volume:.2f}"])

    command_str = " ".join(cmd_parts)
    return jsonify({"command": command_str, "output_path": output_path})


@app.route("/api/run-full", methods=["POST"])
def run_full_video_job():
    """Runs the full headless processor in a background thread and streams status."""
    data = request.json or {}
    video_path = data.get("video_path", "").strip()
    output_path = data.get("output_path", "").strip()
    if not output_path:
        base, ext = os.path.splitext(video_path or "video.mp4")
        output_path = f"{base}_cropped{ext or '.mp4'}"

    ratio = data.get("ratio", "9:16")
    pos_x = float(data.get("pos_x", 0.5))
    srt_path = data.get("srt_path", "").strip()
    music_path = data.get("music_path", "").strip()
    music_volume = float(data.get("music_volume", 0.12))

    job_id = uuid.uuid4().hex[:8]
    ACTIVE_JOBS[job_id] = {
        "status": "starting",
        "progress": "Initializing encoder...",
        "output_path": output_path,
        "started_at": time.time(),
        "error": None
    }

    def worker():
        try:
            cmd = [
                sys.executable, str(BASE_DIR / "process_full.py"),
                "-v", video_path,
                "-o", output_path,
                "-r", ratio,
                "--pos-x", str(pos_x)
            ]
            if srt_path and os.path.exists(srt_path):
                cmd.extend(["-s", srt_path])
            if music_path and os.path.exists(music_path):
                cmd.extend(["-m", music_path, "--volume", str(music_volume)])

            ACTIVE_JOBS[job_id]["status"] = "running"

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True
            )

            for line in iter(proc.stdout.readline, ""):
                line_s = line.strip()
                if "frame=" in line_s or "time=" in line_s:
                    ACTIVE_JOBS[job_id]["progress"] = line_s
                elif "Done!" in line_s:
                    ACTIVE_JOBS[job_id]["progress"] = line_s

            proc.stdout.close()
            ret = proc.wait()

            if ret == 0:
                ACTIVE_JOBS[job_id]["status"] = "completed"
            else:
                ACTIVE_JOBS[job_id]["status"] = "failed"
                ACTIVE_JOBS[job_id]["error"] = f"Exit code {ret}"

        except Exception as e:
            ACTIVE_JOBS[job_id]["status"] = "failed"
            ACTIVE_JOBS[job_id]["error"] = str(e)

    t = threading.Thread(target=worker, daemon=True)
    t.start()

    return jsonify({"job_id": job_id, "status": "started", "output_path": output_path})


@app.route("/api/job-status/<job_id>")
def job_status(job_id):
    job = ACTIVE_JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    print("\n" + "=" * 65)
    print(f"🚀 Studio Clipper UI running at: http://127.0.0.1:{port}")
    print("=" * 65 + "\n")
    app.run(host="127.0.0.1", port=port, debug=False)
