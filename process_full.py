#!/usr/bin/env python3
"""
process_full.py - High-Performance Headless Video Processor

Designed to process full-length videos (even 10+ hours) as fast as possible.
Features:
  - Apple Silicon Media Engine Hardware Acceleration (h264_videotoolbox) with CPU fallback
  - Crop & aspect ratio transformation (9:16, 1:1, 4:5, 16:9, or custom crop=w:h:x:y)
  - Subtitle burning with Devanagari font support (Kohinoor / Devanagari Sangam / Noto)
  - Background music looping & volume ducking (voice remains 100% crisp)
  - Real-time speed and progress reporting
"""

import os
import sys
import json
import time
import uuid
import shutil
import argparse
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

for extra_path in ["/opt/homebrew/bin", "/usr/local/bin"]:
    if extra_path not in os.environ.get("PATH", "") and os.path.exists(extra_path):
        os.environ["PATH"] = extra_path + ":" + os.environ.get("PATH", "")

from sub_utils import prepare_shifted_subtitles, load_any_subtitles


def check_dependencies() -> None:
    for tool in ["ffmpeg", "ffprobe"]:
        if shutil.which(tool) is None:
            print(f"Error: Required tool '{tool}' not found in PATH.", file=sys.stderr)
            sys.exit(1)


def probe_video(video_path: str) -> Dict[str, Any]:
    """Returns video width, height, duration, and audio presence."""
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", video_path
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Failed to probe '{video_path}': {res.stderr}")
    data = json.loads(res.stdout)
    
    v_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if not v_stream:
        raise RuntimeError(f"No video stream found in '{video_path}'.")
    
    width = int(v_stream.get("width", 0))
    height = int(v_stream.get("height", 0))
    has_audio = any(s.get("codec_type") == "audio" for s in data.get("streams", []))

    v_dur = 0.0
    try:
        raw_vd = v_stream.get("duration")
        if raw_vd not in [None, "N/A", ""]:
            v_dur = float(raw_vd)
    except (ValueError, TypeError):
        pass

    fmt_dur = 0.0
    try:
        raw_fd = data.get("format", {}).get("duration")
        if raw_fd not in [None, "N/A", ""]:
            fmt_dur = float(raw_fd)
    except (ValueError, TypeError):
        pass

    # Video processing (crop, scale, seek, preview) cannot exceed available video frames.
    # If the video stream is shorter than the container/audio duration (common in YouTube livestream downloads),
    # use the video stream's duration to prevent seeking past EOF and crashing FFmpeg.
    if v_dur > 0 and fmt_dur > 0:
        duration = min(v_dur, fmt_dur)
    elif v_dur > 0:
        duration = v_dur
    else:
        duration = fmt_dur
    
    return {
        "width": width,
        "height": height,
        "has_audio": has_audio,
        "duration": duration,
        "video_duration": v_dur,
        "format_duration": fmt_dur
    }


def probe_audio(audio_path: str) -> Dict[str, Any]:
    """Probes audio file for stream info, duration, and codec using ffprobe."""
    check_dependencies()
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format", "-show_streams",
        audio_path
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Failed to probe audio '{audio_path}': {res.stderr}")
    data = json.loads(res.stdout)
    a_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    dur = 0.0
    try:
        raw_dur = data.get("format", {}).get("duration")
        if raw_dur not in [None, "N/A", ""]:
            dur = float(raw_dur)
    except (ValueError, TypeError):
        pass
    codec = a_stream.get("codec_name", "audio") if a_stream else "audio"
    sample_rate = a_stream.get("sample_rate", 44100) if a_stream else 44100
    return {
        "duration": dur,
        "codec": codec,
        "sample_rate": sample_rate,
        "has_audio": bool(a_stream)
    }


def combine_video_audio(
    video_path: str,
    audio_path: str,
    output_path: str,
    cancel_check: Optional[Any] = None,
    proc_callback: Optional[Any] = None,
    video_volume: float = 1.0
) -> Tuple[bool, str]:
    """
    Combines an external high-quality audio file with a video into a single MP4.
    Copies video stream directly without loss or re-encoding (-c:v copy),
    encodes audio in high-fidelity AAC 320k (-c:a aac -b:a 320k).
    Applies video_volume adjustment if specified.
    """
    check_dependencies()
    v_info = probe_video(video_path)
    total_dur = v_info.get("duration", 0.0)

    cmd = [
        "ffmpeg", "-y",
        "-err_detect", "ignore_err",
        "-max_error_rate", "1.0",
        "-fflags", "+genpts+discardcorrupt",
        "-i", video_path,
        "-i", audio_path,
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "320k"
    ]
    if abs(video_volume - 1.0) > 0.01:
        cmd.extend(["-af", f"volume={video_volume:.2f}"])
    if total_dur > 0:
        cmd.extend(["-t", f"{total_dur:.3f}"])
    cmd.extend(["-shortest", "-avoid_negative_ts", "make_zero", output_path])

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True
    )
    if proc_callback:
        try:
            proc_callback(proc)
        except Exception:
            pass

    error_lines = []
    for line in iter(proc.stdout.readline, ""):
        if cancel_check and cancel_check():
            proc.kill()
            proc.wait()
            raise RuntimeError("Operation cancelled by user.")
        line_s = line.strip()
        if any(k in line_s.lower() for k in ["error", "invalid", "failed", "unable"]):
            error_lines.append(line_s)
            if len(error_lines) > 20:
                error_lines.pop(0)

    proc.stdout.close()
    rc = proc.wait()
    if rc != 0:
        err_out = "\n".join(error_lines[-5:]) if error_lines else f"FFmpeg exited with error code {rc}"
        return False, err_out
    return True, output_path


def mix_video_background_music(
    video_path: str,
    music_path: str,
    output_path: str,
    music_volume: float = 0.15,
    video_volume: float = 1.0,
    cancel_check: Optional[Any] = None,
    proc_callback: Optional[Any] = None
) -> Tuple[bool, str]:
    """
    Mixes background music into a video:
    - If music duration < video duration: loops music continuously until video ends.
    - Else: combines / mixes directly without looping.
    - Speech dialogue remains crisp & clear using amix with normalize=0.
    - Video stream is copied (-c:v copy) for ultra-fast, lossless processing.
    """
    check_dependencies()
    v_info = probe_video(video_path)
    video_dur = v_info.get("duration", 0.0)
    has_vid_audio = v_info.get("has_audio", False)

    m_info = probe_audio(music_path)
    music_dur = m_info.get("duration", 0.0)

    # Make music in loop if it is shorter than long video, else just combine
    is_shorter = (music_dur > 0 and video_dur > 0 and music_dur < video_dur)

    if has_vid_audio:
        dialogue_filter = "[0:a]asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0"
        if abs(video_volume - 1.0) > 0.01:
            dialogue_filter += f",volume={video_volume:.2f}"
        dialogue_filter += "[a_diag]"

        music_filter = f"[1:a]asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0,volume={music_volume:.3f}[a_music]"
        mix_filter = "[a_diag][a_music]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a_out]"
        filter_complex = f"{dialogue_filter};{music_filter};{mix_filter}"
        audio_map = "[a_out]"
    else:
        filter_complex = f"[1:a]asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0,volume={music_volume:.3f}[a_out]"
        audio_map = "[a_out]"

    cmd = [
        "ffmpeg", "-y",
        "-err_detect", "ignore_err",
        "-max_error_rate", "1.0",
        "-fflags", "+genpts+discardcorrupt",
        "-i", video_path
    ]
    if is_shorter:
        cmd.extend(["-stream_loop", "-1"])
    cmd.extend(["-i", music_path])
    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", "0:v:0",
        "-map", audio_map,
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k"
    ])
    if video_dur > 0:
        cmd.extend(["-t", f"{video_dur:.3f}"])
    cmd.extend(["-avoid_negative_ts", "make_zero", output_path])

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True
    )
    if proc_callback:
        try:
            proc_callback(proc)
        except Exception:
            pass

    error_lines = []
    for line in iter(proc.stdout.readline, ""):
        if cancel_check and cancel_check():
            proc.kill()
            proc.wait()
            raise RuntimeError("Operation cancelled by user.")
        line_s = line.strip()
        if any(k in line_s.lower() for k in ["error", "invalid", "failed", "unable"]):
            error_lines.append(line_s)
            if len(error_lines) > 20:
                error_lines.pop(0)

    proc.stdout.close()
    rc = proc.wait()
    if rc != 0:
        # Fallback to hardware-accelerated re-encode if stream copy fails
        encoders = get_available_encoders()
        hw_enabled = encoders.get("videotoolbox", False)
        v_enc = ["-c:v", "h264_videotoolbox", "-b:v", "6000k"] if hw_enabled else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
        cmd_fallback = [
            "ffmpeg", "-y",
            "-err_detect", "ignore_err",
            "-max_error_rate", "1.0",
            "-fflags", "+genpts+discardcorrupt",
            "-i", video_path
        ]
        if is_shorter:
            cmd_fallback.extend(["-stream_loop", "-1"])
        cmd_fallback.extend(["-i", music_path])
        cmd_fallback.extend([
            "-filter_complex", filter_complex,
            "-map", "0:v:0",
            "-map", audio_map
        ])
        cmd_fallback.extend(v_enc)
        cmd_fallback.extend(["-c:a", "aac", "-b:a", "192k"])
        if video_dur > 0:
            cmd_fallback.extend(["-t", f"{video_dur:.3f}"])
        cmd_fallback.extend(["-avoid_negative_ts", "make_zero", output_path])
        proc2 = subprocess.Popen(cmd_fallback, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        if proc_callback:
            try:
                proc_callback(proc2)
            except Exception:
                pass
        for line in iter(proc2.stdout.readline, ""):
            if cancel_check and cancel_check():
                proc2.kill()
                proc2.wait()
                raise RuntimeError("Operation cancelled by user.")
        proc2.stdout.close()
        rc2 = proc2.wait()
        if rc2 != 0:
            err_out = "\n".join(error_lines[-5:]) if error_lines else f"FFmpeg exited with error code {rc}"
            return False, err_out
    return True, output_path


BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"
ASSETS_DIR.mkdir(parents=True, exist_ok=True)


# ==============================================================
# MODERN VIRAL CAPTION / SUBTITLE STYLES PRESETS
# ==============================================================
CAPTION_STYLES: Dict[str, Dict[str, Any]] = {
    "✨ Soothe White Light (Soft Clean Glow)": {
        "PrimaryColour": "&H00FFFFFF",
        "OutlineColour": "&H40000000",
        "BackColour": "&H70000000",
        "BorderStyle": 1,
        "Outline": 1.6,
        "Shadow": 2.8,
        "Bold": 1,
    },
    "🌟 Viral Reel Yellow (Bold Black Border)": {
        "PrimaryColour": "&H0000FFFF",
        "OutlineColour": "&H00000000",
        "BackColour": "&H00000000",
        "BorderStyle": 1,
        "Outline": 2.5,
        "Shadow": 1.5,
        "Bold": 1,
    },
    "⚪ Classic Crisp White (Bold Black Border)": {
        "PrimaryColour": "&H00FFFFFF",
        "OutlineColour": "&H00000000",
        "BackColour": "&H00000000",
        "BorderStyle": 1,
        "Outline": 2.5,
        "Shadow": 1.0,
        "Bold": 1,
    },
    "🖤 Frosted Pill Box (Modern Translucent Bar)": {
        "PrimaryColour": "&H00FFFFFF",
        "OutlineColour": "&H00000000",
        "BackColour": "&H85000000",
        "BorderStyle": 3,
        "Outline": 1.0,
        "Shadow": 0.0,
        "Bold": 1,
    },
    "💎 Neon Aqua / Cyan (Tech & Podcast)": {
        "PrimaryColour": "&H00FFFF00",
        "OutlineColour": "&H00000000",
        "BackColour": "&H60000000",
        "BorderStyle": 1,
        "Outline": 2.2,
        "Shadow": 2.0,
        "Bold": 1,
    },
    "🔥 Golden Glow (Warm Aesthetic)": {
        "PrimaryColour": "&H003AD7FF",
        "OutlineColour": "&H00000000",
        "BackColour": "&H60000000",
        "BorderStyle": 1,
        "Outline": 2.2,
        "Shadow": 2.0,
        "Bold": 1,
    },
    "🌿 Vibrant Lime (High Impact)": {
        "PrimaryColour": "&H0032FF7E",
        "OutlineColour": "&H00000000",
        "BackColour": "&H60000000",
        "BorderStyle": 1,
        "Outline": 2.5,
        "Shadow": 1.5,
        "Bold": 1,
    }
}


def build_ass_style(
    style_name: Optional[str] = "✨ Soothe White Light (Soft Clean Glow)",
    font_name: str = "Kohinoor Devanagari",
    font_size: int = 22,
    margin_v: int = 35,
    fallback_color: Optional[str] = None,
    caption_border: Optional[str] = None,
    font_weight: Optional[str] = None,
    primary_color: Optional[str] = None,
    highlight_color: Optional[str] = None,
    spacing: Optional[int] = None,
    overflow: bool = False
) -> str:
    """Builds an ASS force_style string based on caption style preset and custom border/font."""
    chosen_style = None
    if style_name:
        for k, v in CAPTION_STYLES.items():
            if k == style_name or k.lower() in style_name.lower() or style_name.lower() in k.lower():
                chosen_style = v
                break

    if not chosen_style:
        chosen_style = CAPTION_STYLES["✨ Soothe White Light (Soft Clean Glow)"].copy()
        if fallback_color:
            chosen_style["PrimaryColour"] = fallback_color

    primary = primary_color if primary_color else chosen_style.get("PrimaryColour", "&H00FFFFFF")
    outline_col = chosen_style.get("OutlineColour", "&H00000000")
    back_col = chosen_style.get("BackColour", "&H00000000")
    border_style = chosen_style.get("BorderStyle", 1)
    outline = chosen_style.get("Outline", 2.0)
    shadow = chosen_style.get("Shadow", 1.0)
    bold = chosen_style.get("Bold", 1)
    if font_weight:
        bold = 1 if "bold" in font_weight.lower() else 0

    if caption_border:
        cb_lower = caption_border.lower()
        if "no border" in cb_lower or "none" in cb_lower or "zero" in cb_lower:
            outline = 0.0
            shadow = 0.5
            border_style = 1
        elif "small" in cb_lower or "thin" in cb_lower or "1.0" in cb_lower:
            outline = 1.0
            shadow = 1.0
            border_style = 1
        elif "standard" in cb_lower or "medium" in cb_lower or "2.0" in cb_lower:
            outline = 2.0
            shadow = 1.5
            border_style = 1
        elif "bold" in cb_lower or "thick" in cb_lower or "3.2" in cb_lower:
            outline = 3.2
            shadow = 2.0
            border_style = 1
        elif "heavy" in cb_lower or "extra thick" in cb_lower or "4.5" in cb_lower:
            outline = 4.5
            shadow = 2.5
            border_style = 1
        elif "box" in cb_lower or "pill" in cb_lower or "bar" in cb_lower:
            border_style = 3
            outline = 1.0
            shadow = 0.0
            back_col = "&H85000000"

    wrap_style = 2 if overflow else 0
    parts = [
        f"FontName={font_name}",
        f"FontSize={font_size}",
        f"PrimaryColour={primary}",
        f"OutlineColour={outline_col}",
        f"BackColour={back_col}",
        f"Bold={bold}",
        f"BorderStyle={border_style}",
        f"Outline={outline}",
        f"Shadow={shadow}",
        "Alignment=2",
        f"MarginV={margin_v}",
        f"WrapStyle={wrap_style}"
    ]
    if highlight_color:
        parts.append(f"SecondaryColour={highlight_color}")
    if spacing is not None and spacing > 0:
        parts.append(f"Spacing={spacing}")
    return ",".join(parts)


def extract_pad_info(crop_filter: Optional[str]) -> Tuple[int, int, int, int]:
    """
    Extracts base_w, base_h, pad_top_px, pad_bottom_px from crop_filter.
    Returns (base_w, base_h, pad_top_px, pad_bottom_px).
    """
    if not crop_filter:
        return 0, 0, 0, 0
    import re
    m = re.search(r"pad=(\d+):(\d+):(\d+):(\d+)", crop_filter)
    if m:
        bw, bh, px, py = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        m_scale = re.findall(r"scale=(\d+):(\d+)", crop_filter)
        if m_scale:
            vw, vh = int(m_scale[-1][0]), int(m_scale[-1][1])
            pad_bottom_px = max(0, bh - py - vh)
            return bw, bh, py, pad_bottom_px
        else:
            m_crop = re.findall(r"crop=(\d+):(\d+)", crop_filter)
            if m_crop:
                vw, vh = int(m_crop[-1][0]), int(m_crop[-1][1])
                pad_bottom_px = max(0, bh - py - vh)
                return bw, bh, py, pad_bottom_px
            return bw, bh, py, max(0, bh - py)
    return 0, 0, 0, 0


def get_bottom_fade_asset(
    fade_height_frac: float = 0.40,
    max_opacity: float = 0.95,
    fade_intensity: float = 1.3,
    fade_pos_y: float = 0.0,
    total_w: int = 1080,
    total_h: int = 1920,
    pad_top_px: int = 0,
    pad_bottom_px: int = 0
) -> str:
    """
    Returns path to a PNG gradient asset for bottom caption contrast.
    If not already on disk, generates it via Pillow automatically.
    Supports adjustable spread (height %), intensity (contrast curve), and vertical shift.
    Seamlessly blends into bottom frame padding (letterboxing).
    """
    h_pct = int(round(fade_height_frac * 100))
    o_pct = int(round(max_opacity * 100))
    i_pct = int(round(fade_intensity * 100))
    y_pct = int(round(fade_pos_y * 100))
    fname = f"bottom_fade_{total_w}x{total_h}_t{pad_top_px}_b{pad_bottom_px}_h{h_pct}_o{o_pct}_i{i_pct}_y{y_pct}.png"
    grad_path = ASSETS_DIR / fname
    if grad_path.exists() and grad_path.stat().st_size > 500:
        return str(grad_path)

    w, h = total_w, total_h
    video_h = max(64, h - pad_top_px - pad_bottom_px)
    fade_px = int(video_h * max(0.05, min(1.0, fade_height_frac)))
    fade_y_shift = int(video_h * max(0.0, min(0.60, fade_pos_y)))

    fade_end = max(pad_top_px + 32, (h - pad_bottom_px) - fade_y_shift)
    fade_start = max(pad_top_px, fade_end - fade_px)

    from PIL import Image
    col = Image.new("RGBA", (1, h), (0, 0, 0, 0))
    putpixel = col.putpixel

    # Exponent for intensity: higher intensity -> faster ramp to deep, rich black
    exponent = max(0.25, 1.35 / max(0.1, fade_intensity))
    max_alpha = int(round(255 * max(0.1, min(1.0, max_opacity))))

    # Fade region on video
    for y in range(fade_start, fade_end):
        norm = (y - fade_start) / max(1, (fade_end - fade_start))
        alpha = int(max_alpha * (norm ** exponent))
        putpixel((0, y), (0, 0, 0, min(255, max(0, alpha))))

    # Below fade_end down to bottom padding line: solid dark matching max_alpha
    for y in range(fade_end, h - pad_bottom_px):
        putpixel((0, y), (0, 0, 0, min(255, max_alpha)))

    # Below video (bottom padding) is solid black
    for y in range(h - pad_bottom_px, h):
        putpixel((0, y), (0, 0, 0, 255))

    full_grad = col.resize((w, h), Image.Resampling.NEAREST)
    full_grad.save(grad_path, "PNG", optimize=True)
    return str(grad_path)


def extract_filter_output_dims(crop_filter: Optional[str], in_w: int, in_h: int) -> Tuple[int, int]:
    """Extracts output width and height produced by a crop/scale/pad filter."""
    if not crop_filter:
        return in_w, in_h
    import re
    m_pad = re.search(r"pad=(\d+):(\d+)", crop_filter)
    if m_pad:
        return int(m_pad.group(1)), int(m_pad.group(2))
    m_scale = re.search(r"scale=(\d+):(\d+)", crop_filter)
    if m_scale:
        return int(m_scale.group(1)), int(m_scale.group(2))
    m_crop = re.search(r"crop=(\d+):(\d+)", crop_filter)
    if m_crop:
        return int(m_crop.group(1)), int(m_crop.group(2))
    return in_w, in_h


def compute_crop_filter(
    in_w: int,
    in_h: int,
    ratio: str = "9:16",
    pos_x: float = 0.5,
    pos_y: float = 0.5,
    scale: float = 1.0,
    pad_top: float = 0.0,
    pad_bottom: float = 0.0,
    pad_mode: str = "move_out"
) -> Tuple[str, int, int, int, int]:
    """
    Computes ffmpeg crop/scale/pad filter string ensuring even dimensions.
    pos_x: 0.0 = left, 0.5 = center, 1.0 = right
    pos_y: 0.0 = top, 0.5 = center, 1.0 = bottom
    scale: 1.0 = standard framing, >1.0 = scale up / zoom in, <1.0 = scale down / fit / pad
    pad_top: Top frame padding percentage (0.0 to 50.0%)
    pad_bottom: Bottom frame padding percentage (0.0 to 50.0%)
    pad_mode: "move_out" = clip moves out of frame (full width maintained, no shrinking),
              "letterbox" = video scales down to fit inside remaining window.
    Returns:
        filter_str, crop_w, crop_h, offset_x, offset_y
    """
    ratio = ratio.strip().lower()
    
    if ratio in ["original", "16:9", "none", "full"]:
        target_aspect = 16.0 / 9.0
    elif ratio == "9:16":
        target_aspect = 9.0 / 16.0
    elif ratio == "1:1":
        target_aspect = 1.0
    elif ratio == "4:5":
        target_aspect = 4.0 / 5.0
    elif ":" in ratio:
        rw, rh = ratio.split(":")
        target_aspect = float(rw) / float(rh)
    else:
        target_aspect = 9.0 / 16.0

    in_aspect = in_w / in_h

    if in_aspect > target_aspect:
        # Input is wider than target: fit height, crop width
        base_h = in_h
        base_w = int(round(in_h * target_aspect))
    else:
        # Input is taller than target: fit width, crop height
        base_w = in_w
        base_h = int(round(in_w / target_aspect))

    # Ensure dimensions are even (required by h264)
    base_w -= (base_w % 2)
    base_h -= (base_h % 2)

    # Frame Padding Calculation (Top and Bottom Letterboxing)
    pad_top_pct = max(0.0, min(50.0, float(pad_top)))
    pad_bottom_pct = max(0.0, min(50.0, float(pad_bottom)))

    pad_top_px = int(round(base_h * (pad_top_pct / 100.0)))
    pad_bottom_px = int(round(base_h * (pad_bottom_pct / 100.0)))
    pad_top_px -= (pad_top_px % 2)
    pad_bottom_px -= (pad_bottom_px % 2)

    avail_w = base_w
    avail_h = max(32, base_h - pad_top_px - pad_bottom_px)
    avail_h -= (avail_h % 2)

    scale = max(0.2, min(5.0, float(scale)))
    pos_x = max(0.0, min(1.0, float(pos_x)))
    pos_y = max(0.0, min(1.0, float(pos_y)))

    if pad_mode == "move_out":
        # --- MOVE OUT OF FRAME MODE (Clip maintains full scale, padding moves clip out of frame) ---
        req_w = base_w / scale
        req_h = avail_h / scale

        cw = int(round(min(float(in_w), req_w)))
        ch = int(round(min(float(in_h), req_h)))
        cw -= (cw % 2)
        ch -= (ch % 2)
        cw = max(16, min(in_w, cw))
        ch = max(16, min(in_h, ch))

        max_x = max(0, in_w - cw)
        max_y = max(0, in_h - ch)
        off_x = int(round(max_x * pos_x))
        off_y = int(round(max_y * pos_y))
        off_x -= (off_x % 2)
        off_y -= (off_y % 2)

        # If padding is 0 and pos_y != 0.5, allow vertical framing to shift clip out of frame
        if pad_top_px == 0 and pad_bottom_px == 0 and max_y == 0 and abs(pos_y - 0.5) > 0.01:
            if pos_y < 0.5:
                shift_px = int(round(base_h * (0.5 - pos_y) * 0.40))
                shift_px -= (shift_px % 2)
                ch = max(32, base_h - shift_px)
                ch -= (ch % 2)
                off_y = shift_px
                pad_bottom_px = shift_px
                avail_h = ch
            else:
                shift_px = int(round(base_h * (pos_y - 0.5) * 0.40))
                shift_px -= (shift_px % 2)
                ch = max(32, base_h - shift_px)
                ch -= (ch % 2)
                off_y = 0
                pad_top_px = shift_px
                avail_h = ch

        if scale >= 1.0:
            disp_w = base_w
            disp_h = avail_h
        else:
            disp_w = int(round(base_w * scale))
            disp_h = int(round(avail_h * scale))
            disp_w -= (disp_w % 2)
            disp_h -= (disp_h % 2)
            disp_w = max(16, min(avail_w, disp_w))
            disp_h = max(16, min(avail_h, disp_h))

        pad_in_avail_x = max(0, int(round((avail_w - disp_w) * pos_x)))
        pad_in_avail_y = max(0, int(round((avail_h - disp_h) * pos_y)))
        pad_in_avail_x -= (pad_in_avail_x % 2)
        pad_in_avail_y -= (pad_in_avail_y % 2)

        total_pad_x = pad_in_avail_x
        total_pad_y = pad_top_px + pad_in_avail_y

        parts = [f"crop={cw}:{ch}:{off_x}:{off_y}"]
        if disp_w != cw or disp_h != ch:
            parts.append(f"scale={disp_w}:{disp_h}")
        if disp_w < base_w or disp_h < base_h or total_pad_x > 0 or total_pad_y > 0:
            parts.append(f"pad={base_w}:{base_h}:{total_pad_x}:{total_pad_y}:color=black")

        return ",".join(parts), cw, ch, off_x, off_y

    else:
        # --- LETTERBOX (FIT) MODE ---
        sub_aspect = avail_w / avail_h
        if in_aspect > sub_aspect:
            crop_base_h = in_h
            crop_base_w = int(round(in_h * sub_aspect))
        else:
            crop_base_w = in_w
            crop_base_h = int(round(in_w / sub_aspect))

        crop_base_w -= (crop_base_w % 2)
        crop_base_h -= (crop_base_h % 2)

        req_w = crop_base_w / scale
        req_h = crop_base_h / scale

        cw = int(round(min(float(in_w), req_w)))
        ch = int(round(min(float(in_h), req_h)))
        cw -= (cw % 2)
        ch -= (ch % 2)
        cw = max(16, min(in_w, cw))
        ch = max(16, min(in_h, ch))

        max_x = max(0, in_w - cw)
        max_y = max(0, in_h - ch)
        off_x = int(round(max_x * pos_x))
        off_y = int(round(max_y * pos_y))
        off_x -= (off_x % 2)
        off_y -= (off_y % 2)

        scale_fit = min(avail_w / req_w, avail_h / req_h)
        disp_w = int(round(cw * scale_fit))
        disp_h = int(round(ch * scale_fit))
        disp_w -= (disp_w % 2)
        disp_h -= (disp_h % 2)
        disp_w = max(16, min(avail_w, disp_w))
        disp_h = max(16, min(avail_h, disp_h))

        pad_in_avail_x = max(0, int(round((avail_w - disp_w) * pos_x)))
        pad_in_avail_y = max(0, int(round((avail_h - disp_h) * pos_y)))
        pad_in_avail_x -= (pad_in_avail_x % 2)
        pad_in_avail_y -= (pad_in_avail_y % 2)

        total_pad_x = pad_in_avail_x
        total_pad_y = pad_top_px + pad_in_avail_y

        parts = [f"crop={cw}:{ch}:{off_x}:{off_y}"]
        if disp_w != cw or disp_h != ch:
            parts.append(f"scale={disp_w}:{disp_h}")
        if disp_w < base_w or disp_h < base_h or total_pad_x > 0 or total_pad_y > 0:
            parts.append(f"pad={base_w}:{base_h}:{total_pad_x}:{total_pad_y}:color=black")

        filter_str = ",".join(parts)
        return filter_str, cw, ch, off_x, off_y


def escape_ffmpeg_path(path: str) -> str:
    p = os.path.abspath(path).replace("\\", "/")
    return p.replace(":", "\\:").replace("'", "\\'")


def get_available_encoders() -> Dict[str, bool]:
    res = subprocess.run(["ffmpeg", "-encoders"], capture_output=True, text=True)
    out = res.stdout if res.returncode == 0 else ""
    return {
        "videotoolbox": "h264_videotoolbox" in out,
        "libx264": "libx264" in out
    }


def run_pipeline(
    video_path: str,
    output_path: str,
    crop_filter: Optional[str] = None,
    srt_path: Optional[str] = None,
    music_path: Optional[str] = None,
    music_volume: float = 0.12,
    start_sec: Optional[float] = None,
    duration_sec: Optional[float] = None,
    use_hw_accel: bool = True,
    font_name: str = "Kohinoor Devanagari",
    font_size: int = 22,
    font_color: str = "&H00FFFFFF",
    caption_style: Optional[str] = None,
    caption_border: Optional[str] = None,
    bitrate: str = "5000k",
    bottom_fade: bool = False,
    fade_height: float = 0.40,
    fade_opacity: float = 0.95,
    fade_intensity: float = 1.3,
    fade_pos_y: float = 0.0,
    sub_margin_v: Optional[int] = None,
    cancel_check: Optional[Any] = None,
    proc_callback: Optional[Any] = None,
    audio_path: Optional[str] = None,
    sub_pos_y: Optional[float] = None,
    noise_removal: bool = False,
    noise_intensity: str = "Medium",
    video_volume: float = 1.0,
    music_fade_in: float = 0.0,
    music_fade_out: float = 0.0,
    music_loop: Optional[bool] = None,
    caption_weight: Optional[str] = None,
    caption_highlight: Optional[str] = None,
    caption_spacing: Optional[int] = None,
    caption_overflow: bool = False
) -> None:
    """Runs the FFmpeg pipeline with optional duration / start for preview or full render."""
    check_dependencies()
    v_info = probe_video(video_path)
    total_dur = v_info["duration"]

    if total_dur > 0 and start_sec is not None and start_sec >= total_dur:
        raise ValueError(
            f"Requested seek start time ({start_sec:.1f}s) is at or beyond the video stream duration ({total_dur:.1f}s)."
        )
    if total_dur > 0 and start_sec is not None and duration_sec is not None:
        rem_sec = max(1.0, total_dur - start_sec)
        if duration_sec > rem_sec:
            duration_sec = rem_sec

    has_ext_audio = bool(audio_path and os.path.exists(audio_path))
    has_vid_audio = v_info["has_audio"]
    has_music = bool(music_path and os.path.exists(music_path) and music_volume > 0)
    has_srt = bool(srt_path and os.path.exists(srt_path) and os.path.getsize(srt_path) > 0)

    # Make music in loop if it is shorter than target video duration, else just combine
    if has_music and music_loop is None:
        try:
            m_info = probe_audio(music_path)
            music_dur = m_info.get("duration", 0.0)
            target_dur = duration_sec if (duration_sec and duration_sec > 0) else total_dur
            music_loop = bool(music_dur > 0 and target_dur > 0 and music_dur < target_dur)
        except Exception:
            music_loop = True
    elif music_loop is None:
        music_loop = True

    encoders = get_available_encoders()
    hw_enabled = use_hw_accel and encoders.get("videotoolbox", False)

    # 1. Inputs & Filters Setup
    temp_shifted_srt = None
    burn_file = None
    if has_srt:
        temp_shifted_srt = str(Path(output_path).parent / f".tmp_sub_{uuid.uuid4().hex[:12]}.srt")
        end_sec = ((start_sec or 0.0) + duration_sec) if duration_sec else None
        ok = prepare_shifted_subtitles(
            sub_path=srt_path,
            output_srt_path=temp_shifted_srt,
            start_sec=(start_sec or 0.0),
            end_sec=end_sec,
            overflow=caption_overflow
        )
        burn_file = temp_shifted_srt if ok else None

        if burn_file:
            # If the subtitle file path itself contains single quotes, copy to a clean temp file
            # because ffmpeg's subtitles filter cannot parse single quotes inside subtitles='...'
            if "'" in burn_file or '"' in burn_file:
                safe_copy = str(Path(output_path).parent / f".tmp_sub_{uuid.uuid4().hex[:12]}.srt")
                shutil.copy2(burn_file, safe_copy)
                burn_file = safe_copy
                if not temp_shifted_srt:
                    temp_shifted_srt = safe_copy

    filter_complex_parts = []
    v_curr = "0:v"

    if crop_filter:
        filter_complex_parts.append(f"[{v_curr}]{crop_filter}[v_crop]")
        v_curr = "v_crop"

    if bottom_fade:
        out_w, out_h = extract_filter_output_dims(crop_filter, v_info["width"], v_info["height"])
        _, _, pad_top_px, pad_bottom_px = extract_pad_info(crop_filter)
        grad_asset = get_bottom_fade_asset(
            fade_height_frac=fade_height,
            max_opacity=fade_opacity,
            fade_intensity=fade_intensity,
            fade_pos_y=fade_pos_y,
            total_w=out_w,
            total_h=out_h,
            pad_top_px=pad_top_px,
            pad_bottom_px=pad_bottom_px
        )
        esc_grad = escape_ffmpeg_path(grad_asset)
        filter_complex_parts.append(f"movie='{esc_grad}',scale={out_w}:{out_h}[v_grad]")
        filter_complex_parts.append(f"[{v_curr}][v_grad]overlay=0:0[v_fade]")
        v_curr = "v_fade"

    if burn_file:
        escaped_sub = escape_ffmpeg_path(burn_file)
        burn_ext = Path(burn_file).suffix.lower()
        if burn_ext in [".ass", ".ssa"] and (start_sec is None or start_sec == 0):
            # Native ASS filter preserves all native styling
            sub_filter = f"ass='{escaped_sub}'"
        else:
            out_w, out_h = extract_filter_output_dims(crop_filter, v_info["width"], v_info["height"])
            _, _, _, pad_b_px = extract_pad_info(crop_filter)
            if sub_margin_v is not None:
                final_margin_v = sub_margin_v
            elif sub_pos_y is not None:
                final_margin_v = max(10, min(out_h - 40, int(round(out_h * sub_pos_y))))
            else:
                final_margin_v = pad_b_px + 35
            style = build_ass_style(
                style_name=caption_style,
                font_name=font_name,
                font_size=font_size,
                margin_v=final_margin_v,
                fallback_color=font_color,
                caption_border=caption_border,
                font_weight=caption_weight,
                primary_color=font_color,
                highlight_color=caption_highlight,
                spacing=caption_spacing,
                overflow=caption_overflow
            )
            sub_filter = f"subtitles='{escaped_sub}':force_style='{style}'"
        filter_complex_parts.append(f"[{v_curr}]{sub_filter}[v_sub]")
        v_curr = "v_sub"

    if v_curr != "0:v":
        video_map = f"[{v_curr}]"
    else:
        video_map = "0:v"

    # 3. Audio Filters (Mix main voice/audio + ducked background music with robust timestamp resample)
    noise_filter_str = ""
    if noise_removal:
        n_lower = (noise_intensity or "medium").lower()
        if "low" in n_lower:
            nr, nf = 10, -25
        elif "high" in n_lower:
            nr, nf = 28, -35
        else:
            nr, nf = 18, -30
        noise_filter_str = f",highpass=f=85,lowpass=f=9500,afftdn=nr={nr}:nf={nf}:tn=1"

    vol_filter_str = f",volume={video_volume:.2f}" if abs(video_volume - 1.0) > 0.01 else ""

    music_effects = []
    if music_fade_in > 0:
        music_effects.append(f"afade=t=in:st=0:d={music_fade_in:.1f}")
    if music_fade_out > 0 and duration_sec and duration_sec > music_fade_out:
        music_effects.append(f"afade=t=out:st={(duration_sec - music_fade_out):.1f}:d={music_fade_out:.1f}")
    music_fx_str = ("," + ",".join(music_effects)) if music_effects else ""

    if has_ext_audio:
        filter_complex_parts.append(f"[1:a]asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0{noise_filter_str}{vol_filter_str}[a_main_clean]")
        if has_music:
            filter_complex_parts.append(f"[2:a]asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0,volume={music_volume:.3f}{music_fx_str}[bg_ducked]")
            filter_complex_parts.append("[a_main_clean][bg_ducked]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a_out]")
            audio_map = "[a_out]"
        else:
            audio_map = "[a_main_clean]"
    elif has_vid_audio:
        filter_complex_parts.append(f"[0:a]asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0{noise_filter_str}{vol_filter_str}[a0_clean]")
        if has_music:
            filter_complex_parts.append(f"[1:a]asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0,volume={music_volume:.3f}{music_fx_str}[bg_ducked]")
            filter_complex_parts.append("[a0_clean][bg_ducked]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a_out]")
            audio_map = "[a_out]"
        else:
            audio_map = "[a0_clean]"
    elif has_music:
        filter_complex_parts.append(f"[1:a]asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0,volume={music_volume:.3f}{music_fx_str}[a_out]")
        audio_map = "[a_out]"
    else:
        audio_map = None

    def _build_ffmpeg_cmd(seek_mode: str = "fast") -> list:
        c = [
            "ffmpeg", "-y",
            "-err_detect", "ignore_err",
            "-max_error_rate", "1.0",
            "-fflags", "+genpts+discardcorrupt"
        ]
        if seek_mode == "fast" and start_sec is not None and start_sec > 0:
            c.extend(["-ss", f"{start_sec:.3f}"])
        c.extend(["-i", video_path])

        if has_ext_audio:
            if seek_mode == "fast" and start_sec is not None and start_sec > 0:
                c.extend(["-ss", f"{start_sec:.3f}"])
            c.extend(["-i", audio_path])

        if has_music:
            if music_loop:
                c.extend(["-stream_loop", "-1"])
            c.extend(["-i", music_path])

        if seek_mode == "safe" and start_sec is not None and start_sec > 0:
            c.extend(["-ss", f"{start_sec:.3f}"])

        if filter_complex_parts:
            c.extend(["-filter_complex", ";".join(filter_complex_parts)])

        c.extend(["-map", video_map])
        if audio_map:
            c.extend(["-map", audio_map])

        if hw_enabled:
            c.extend([
                "-c:v", "h264_videotoolbox",
                "-b:v", bitrate,
                "-pix_fmt", "yuv420p"
            ])
        else:
            c.extend([
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "20",
                "-pix_fmt", "yuv420p"
            ])

        if audio_map:
            c.extend(["-c:a", "aac", "-b:a", "192k"])

        if duration_sec is not None and duration_sec > 0:
            c.extend(["-t", f"{duration_sec:.3f}"])
        elif total_dur > 0:
            c.extend(["-t", f"{total_dur:.3f}"])

        c.extend(["-avoid_negative_ts", "make_zero"])
        if music_loop:
            c.append("-shortest")
        c.append(output_path)
        return c

    print("\n" + "=" * 65)
    print(f"🎬 Source Video  : {video_path}")
    print(f"📐 Resolution    : {v_info['width']}x{v_info['height']}")
    print(f"⏱️  Duration      : {duration_sec if duration_sec else total_dur:.1f}s")
    if has_ext_audio:
        print(f"🎵 Main Audio    : {audio_path}")
    if crop_filter:
        print(f"✂️  Crop Filter   : {crop_filter}")
    if has_srt:
        print(f"💬 Subtitles     : {srt_path}")
    if has_music:
        print(f"🎵 Music Mix     : {music_path} (Vol: {int(music_volume*100)}%)")
    print(f"📁 Output File   : {output_path}")
    print("=" * 65 + "\n")

    def _execute_ffmpeg_proc(run_cmd: list) -> Tuple[int, str]:
        proc = subprocess.Popen(
            run_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True
        )
        if proc_callback:
            try:
                proc_callback(proc)
            except Exception:
                pass

        last_report = time.time()
        error_lines = []
        printed_errors = 0
        for line in iter(proc.stdout.readline, ""):
            if cancel_check and cancel_check():
                proc.kill()
                proc.wait()
                raise RuntimeError("Operation stopped by user (Master Kill).")

            line_s = line.strip()
            if "frame=" in line_s or "time=" in line_s:
                now = time.time()
                if now - last_report > 0.8:
                    print(f"\r🚀 Progress: {line_s}", end="", flush=True)
                    last_report = now
            elif any(k in line_s.lower() for k in ["error", "invalid", "failed", "unable"]):
                if printed_errors < 15:
                    print(f"\n{line_s}")
                    printed_errors += 1
                elif printed_errors == 15:
                    print("\n[Additional error lines throttled to prevent terminal flood...]")
                    printed_errors += 1
                error_lines.append(line_s)
                if len(error_lines) > 50:
                    error_lines.pop(0)

        proc.stdout.close()
        rc = proc.wait()
        if cancel_check and cancel_check():
            raise RuntimeError("Operation stopped by user (Master Kill).")
        err_out = "\n".join(error_lines[-5:]) if error_lines else f"FFmpeg exited with error code {rc}"
        return rc, err_out

    start_time = time.time()
    try:
        primary_cmd = _build_ffmpeg_cmd(seek_mode="fast")
        return_code, err_msg = _execute_ffmpeg_proc(primary_cmd)

        # If fast seek failed (common on non-keyframes / AAC offset streams), retry with safe sequential decoding
        if return_code != 0 and start_sec is not None and start_sec > 0 and not (cancel_check and cancel_check()):
            print(f"\n⚠️ Fast seek failed (exit code {return_code}). Retrying with safe sequential decoding...")
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except Exception:
                    pass
            fallback_cmd = _build_ffmpeg_cmd(seek_mode="safe")
            return_code, err_msg = _execute_ffmpeg_proc(fallback_cmd)

        if return_code != 0:
            print(f"\n❌ FFmpeg exited with error code {return_code}: {err_msg}", file=sys.stderr)
            raise RuntimeError(f"FFmpeg render failed (exit code {return_code}): {err_msg}")

        elapsed = time.time() - start_time
        print(f"\n\n✅ Done! File rendered successfully in {elapsed:.1f}s.")
        print(f"📁 Saved to: {os.path.abspath(output_path)}\n")
    finally:
        if temp_shifted_srt and os.path.exists(temp_shifted_srt):
            try:
                os.remove(temp_shifted_srt)
            except Exception:
                pass
        if cancel_check and cancel_check() and os.path.exists(output_path):
            try:
                os.remove(output_path)
            except Exception:
                pass


def concat_video_segments(
    segment_paths: list,
    output_path: str,
    use_hw_accel: bool = True,
    bitrate: str = "5000k"
) -> None:
    """Concatenates multiple video segments seamlessly, first attempting fast stream copy, then fallback re-encode."""
    if not segment_paths:
        raise ValueError("No video segments to concatenate.")
    if len(segment_paths) == 1:
        shutil.copy2(segment_paths[0], output_path)
        return

    out_dir = Path(output_path).parent
    list_path = out_dir / f".concat_list_{uuid.uuid4().hex[:8]}.txt"
    try:
        with open(list_path, "w", encoding="utf-8") as f:
            for p in segment_paths:
                esc = os.path.abspath(p).replace("'", "'\\''")
                f.write(f"file '{esc}'\n")

        # Attempt 1: Fast stream copy via concat demuxer
        cmd = [
            "ffmpeg", "-y",
            "-err_detect", "ignore_err",
            "-max_error_rate", "1.0",
            "-f", "concat",
            "-safe", "0",
            "-i", str(list_path),
            "-c", "copy",
            str(output_path)
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
            return

        # Attempt 2: Fallback filter concat
        encoders = get_available_encoders()
        hw_enabled = use_hw_accel and encoders.get("videotoolbox", False)

        fallback_cmd = [
            "ffmpeg", "-y",
            "-err_detect", "ignore_err",
            "-max_error_rate", "1.0",
            "-fflags", "+genpts+discardcorrupt"
        ]
        for p in segment_paths:
            fallback_cmd.extend(["-i", p])

        v_info = probe_video(segment_paths[0])
        has_audio = v_info.get("has_audio", False)

        if has_audio:
            filter_str = "".join(f"[{i}:v][{i}:a]" for i in range(len(segment_paths))) + f"concat=n={len(segment_paths)}:v=1:a=1[v][a]"
            maps = ["-map", "[v]", "-map", "[a]"]
            acodec = ["-c:a", "aac", "-b:a", "192k"]
        else:
            filter_str = "".join(f"[{i}:v]" for i in range(len(segment_paths))) + f"concat=n={len(segment_paths)}:v=1[v]"
            maps = ["-map", "[v]"]
            acodec = []

        if hw_enabled:
            vcodec = ["-c:v", "h264_videotoolbox", "-b:v", bitrate, "-pix_fmt", "yuv420p"]
        else:
            vcodec = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]

        fallback_cmd.extend(["-filter_complex", filter_str] + maps + vcodec + acodec + [str(output_path)])
        subprocess.run(fallback_cmd, check=True, capture_output=True)
    finally:
        if list_path.exists():
            try:
                list_path.unlink()
            except Exception:
                pass


def generate_3segment_preview(
    video_path: str,
    output_path: str,
    crop_filter: Optional[str] = None,
    srt_path: Optional[str] = None,
    music_path: Optional[str] = None,
    music_volume: float = 0.12,
    segment_duration: float = 60.0,
    percentages: Tuple[float, float, float] = (0.0, 0.45, 0.75),
    use_hw_accel: bool = True,
    font_name: str = "Kohinoor Devanagari",
    font_size: int = 22,
    font_color: str = "&H00FFFFFF",
    caption_style: Optional[str] = None,
    caption_border: Optional[str] = None,
    bitrate: str = "5000k",
    bottom_fade: bool = False,
    fade_height: float = 0.40,
    fade_opacity: float = 0.95,
    fade_intensity: float = 1.3,
    fade_pos_y: float = 0.0,
    sub_margin_v: Optional[int] = None,
    cancel_check: Optional[Any] = None,
    proc_callback: Optional[Any] = None,
    status_callback: Optional[Any] = None,
    audio_path: Optional[str] = None,
    sub_pos_y: Optional[float] = None,
    noise_removal: bool = False,
    noise_intensity: str = "Medium",
    video_volume: float = 1.0,
    music_fade_in: float = 0.0,
    music_fade_out: float = 0.0,
    music_loop: Optional[bool] = None,
    caption_weight: Optional[str] = None,
    caption_highlight: Optional[str] = None,
    caption_spacing: Optional[int] = None,
    caption_overflow: bool = False
) -> str:
    """
    Generates a 3-segment combined test preview video:
    - Segment 1: Starting at 0% (beginning of video)
    - Segment 2: Starting at 45% of total video duration
    - Segment 3: Starting at 75% of total video duration
    Each segment is rendered for `segment_duration` (default 60s / 1 min)
    with all chosen video transforms (aspect ratio crop, subtitle shift/burn, music ducking, bottom fade).
    The 3 segments are then seamlessly combined into one final preview video.
    """
    check_dependencies()
    v_info = probe_video(video_path)
    total_dur = v_info["duration"]
    if total_dur <= 0:
        raise ValueError(f"Invalid video duration: {total_dur}s")

    out_dir = Path(output_path).parent
    out_dir.mkdir(parents=True, exist_ok=True)

    starts = [total_dur * p for p in percentages]
    durations = []
    for s in starts:
        rem = max(1.0, total_dur - s)
        durations.append(min(segment_duration, rem))

    temp_segments = []
    uid = uuid.uuid4().hex[:8]

    try:
        for idx, (start_t, dur_t) in enumerate(zip(starts, durations)):
            if cancel_check and cancel_check():
                raise RuntimeError("Operation stopped by user (Master Kill).")

            seg_path = str(out_dir / f".tmp_preview_seg_{idx+1}_{uid}.mp4")
            temp_segments.append(seg_path)

            pct_label = f"{int(percentages[idx] * 100)}%"
            m, s = divmod(int(start_t), 60)
            time_str = f"{m:02d}:{s:02d}"

            print(f"\n🎬 Rendering Test Segment {idx+1}/{len(starts)}: at {pct_label} ({time_str}, duration {dur_t:.1f}s)...")

            if status_callback:
                status_callback(idx + 1, len(starts), start_t, dur_t, "rendering")

            run_pipeline(
                video_path=video_path,
                output_path=seg_path,
                crop_filter=crop_filter,
                srt_path=srt_path,
                music_path=music_path,
                music_volume=music_volume,
                start_sec=start_t,
                duration_sec=dur_t,
                use_hw_accel=use_hw_accel,
                font_name=font_name,
                font_size=font_size,
                font_color=font_color,
                caption_style=caption_style,
                caption_border=caption_border,
                bitrate=bitrate,
                bottom_fade=bottom_fade,
                fade_height=fade_height,
                fade_opacity=fade_opacity,
                fade_intensity=fade_intensity,
                fade_pos_y=fade_pos_y,
                sub_margin_v=sub_margin_v,
                cancel_check=cancel_check,
                proc_callback=proc_callback,
                audio_path=audio_path,
                sub_pos_y=sub_pos_y,
                noise_removal=noise_removal,
                noise_intensity=noise_intensity,
                video_volume=video_volume,
                music_fade_in=music_fade_in,
                music_fade_out=music_fade_out,
                music_loop=music_loop,
                caption_weight=caption_weight,
                caption_highlight=caption_highlight,
                caption_spacing=caption_spacing,
                caption_overflow=caption_overflow
            )

        if cancel_check and cancel_check():
            raise RuntimeError("Operation stopped by user (Master Kill).")

        print(f"\n⚡ Combining {len(temp_segments)} segments into: {output_path}")
        if status_callback:
            status_callback(len(starts), len(starts), 0, 0, "combining")

        concat_video_segments(temp_segments, output_path, use_hw_accel=use_hw_accel, bitrate=bitrate)
        print(f"✅ Successfully created combined 3-segment test preview: {output_path}")
        return output_path

    finally:
        for p in temp_segments:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
        if cancel_check and cancel_check() and os.path.exists(output_path):
            try:
                os.remove(output_path)
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(
        description="High-Speed Headless Video Processing Engine (Whole Video or Preview)"
    )
    parser.add_argument("-v", "--video", required=True, help="Path to input video (.mp4, .mov)")
    parser.add_argument("-o", "--output", required=True, help="Path to output video (.mp4)")
    parser.add_argument("-r", "--ratio", default="9:16", help="Aspect ratio (9:16, 1:1, 4:5, 16:9, or original)")
    parser.add_argument("--crop", help="Explicit crop filter (e.g. 'crop=608:1080:656:0'). Overrides --ratio.")
    parser.add_argument("--scale", type=float, default=1.0, help="Scale / Zoom factor (1.0=standard, >1.0=zoom in/scale up, <1.0=scale down/pad)")
    parser.add_argument("--pos-x", type=float, default=0.5, help="Horizontal crop position (0.0=left, 0.5=center, 1.0=right)")
    parser.add_argument("--pos-y", type=float, default=0.5, help="Vertical crop position (0.0=top, 0.5=center, 1.0=bottom)")
    parser.add_argument("--pad-top", type=float, default=0.0, help="Top frame padding percentage (default: 0.0)")
    parser.add_argument("--pad-bottom", type=float, default=0.0, help="Bottom frame padding percentage (default: 0.0)")
    parser.add_argument("--pad-mode", default="move_out", choices=["move_out", "letterbox"], help="Padding mode: 'move_out' keeps full scale and moves clip out of frame, 'letterbox' fits clip inside window")
    parser.add_argument("--sub-margin", type=int, default=None, help="Vertical margin for subtitles (defaults to pad_bottom + 35)")
    parser.add_argument("--sub-pos-y", type=float, default=None, help="Vertical position for subtitles as fraction from bottom (0.0 to 1.0, e.g. 0.15 for 15%%, 0.5 for center)")
    parser.add_argument("--bottom-fade", "--black-fade", dest="bottom_fade", action="store_true", default=False, help="Add smooth bottom black fade gradient for caption contrast")
    parser.add_argument("--fade-height", type=float, default=0.40, help="Fraction of frame height for bottom black fade (0.05 to 1.0, default: 0.40)")
    parser.add_argument("--fade-opacity", type=float, default=0.95, help="Maximum opacity of bottom black fade (default: 0.95)")
    parser.add_argument("--fade-intensity", type=float, default=1.3, help="Intensity / contrast multiplier for bottom black fade (default: 1.3)")
    parser.add_argument("--fade-y", "--fade-pos-y", dest="fade_pos_y", type=float, default=0.0, help="Vertical shift / position for bottom black fade (0.0 to 0.6, default: 0.0)")
    parser.add_argument("-s", "--srt", "--sub", dest="srt", help="Path to subtitle or lyrics file (.srt, .ass, .lrc, .vtt)")
    parser.add_argument("-a", "--audio", help="Path to high-quality / separate main audio file (.m4a, .mp3, .wav, .aac, .opus) to replace or supply video audio")
    parser.add_argument("--video-volume", type=float, default=1.0, help="Main video / audio volume multiplier (1.0 = normal, 1.5 = 150%%, 2.0 = 200%%)")
    parser.add_argument("-m", "--music", help="Path to background music file")
    parser.add_argument("--volume", type=float, default=0.12, help="Background music volume multiplier (default: 0.12)")
    parser.add_argument("--start", type=float, default=None, help="Start time offset in seconds")
    parser.add_argument("--duration", type=float, default=None, help="Render duration in seconds (e.g. 60 for 1-min preview)")
    parser.add_argument("--no-hw", action="store_true", help="Disable Apple Silicon VideoToolbox hardware acceleration")
    parser.add_argument("--font", default="Kohinoor Devanagari", help="Font for subtitles")
    parser.add_argument("--font-size", type=int, default=22, help="Font size for subtitles")
    parser.add_argument("--font-color", default="&H00FFFFFF", help="Font color for subtitles (&H00FFFFFF for white, &H0000FFFF for yellow)")
    parser.add_argument("--caption-style", default="✨ Soothe White Light (Soft Clean Glow)", help="Preset caption style (e.g. 'Soothe White Light', 'Viral Reel Yellow', 'Frosted Pill Box')")
    parser.add_argument("--caption-border", default="Auto (Preset Default)", help="Caption border style (No Border, Small Border, Standard Border, Bold Border, Heavy Outline, Opaque Box, Auto)")
    parser.add_argument("--caption-overflow", "--overflow", dest="caption_overflow", action="store_true", default=False, help="Set caption text to single-line overflow mode (no word wrap)")
    parser.add_argument("--multi-preview", action="store_true", help="Generate a 3-segment combined test preview (0%%, 45%%, 75%% of video length, 1 min each combined into 1 video)")
    parser.add_argument("--preview-seg-dur", type=float, default=60.0, help="Duration of each segment in multi-preview in seconds (default: 60.0s / 1 min)")
    parser.add_argument("--bitrate", default="6000k", help="Video bitrate for VideoToolbox encoding (default 6000k)")

    args = parser.parse_args()

    v_info = probe_video(args.video)

    # Determine crop filter
    if args.crop:
        crop_filter = args.crop
    elif args.ratio.lower() not in ["original", "none"]:
        crop_filter, _, _, _, _ = compute_crop_filter(
            in_w=v_info["width"],
            in_h=v_info["height"],
            ratio=args.ratio,
            pos_x=args.pos_x,
            pos_y=args.pos_y,
            scale=args.scale,
            pad_top=args.pad_top,
            pad_bottom=args.pad_bottom,
            pad_mode=args.pad_mode
        )
    else:
        crop_filter = None

    if args.multi_preview:
        generate_3segment_preview(
            video_path=args.video,
            output_path=args.output,
            crop_filter=crop_filter,
            srt_path=args.srt,
            music_path=args.music,
            music_volume=args.volume,
            segment_duration=args.preview_seg_dur,
            percentages=(0.0, 0.45, 0.75),
            use_hw_accel=not args.no_hw,
            font_name=args.font,
            font_size=args.font_size,
            font_color=args.font_color,
            caption_style=args.caption_style,
            caption_border=args.caption_border,
            bitrate=args.bitrate,
            bottom_fade=args.bottom_fade,
            fade_height=args.fade_height,
            fade_opacity=args.fade_opacity,
            fade_intensity=args.fade_intensity,
            fade_pos_y=args.fade_pos_y,
            sub_margin_v=args.sub_margin,
            audio_path=args.audio,
            sub_pos_y=args.sub_pos_y,
            video_volume=args.video_volume,
            caption_overflow=args.caption_overflow
        )
    else:
        run_pipeline(
            video_path=args.video,
            output_path=args.output,
            crop_filter=crop_filter,
            srt_path=args.srt,
            music_path=args.music,
            music_volume=args.volume,
            start_sec=args.start,
            duration_sec=args.duration,
            use_hw_accel=not args.no_hw,
            font_name=args.font,
            font_size=args.font_size,
            font_color=args.font_color,
            caption_style=args.caption_style,
            caption_border=args.caption_border,
            bitrate=args.bitrate,
            bottom_fade=args.bottom_fade,
            fade_height=args.fade_height,
            fade_opacity=args.fade_opacity,
            fade_intensity=args.fade_intensity,
            fade_pos_y=args.fade_pos_y,
            sub_margin_v=args.sub_margin,
            audio_path=args.audio,
            sub_pos_y=args.sub_pos_y,
            video_volume=args.video_volume,
            caption_overflow=args.caption_overflow
        )


if __name__ == "__main__":
    main()
