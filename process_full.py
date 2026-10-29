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
    duration = float(data.get("format", {}).get("duration", 0.0))
    
    return {
        "width": width,
        "height": height,
        "has_audio": has_audio,
        "duration": duration
    }


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
    fallback_color: Optional[str] = None
) -> str:
    """Builds an ASS force_style string based on caption style preset."""
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

    primary = chosen_style.get("PrimaryColour", "&H00FFFFFF")
    outline_col = chosen_style.get("OutlineColour", "&H00000000")
    back_col = chosen_style.get("BackColour", "&H00000000")
    border_style = chosen_style.get("BorderStyle", 1)
    outline = chosen_style.get("Outline", 2.0)
    shadow = chosen_style.get("Shadow", 1.0)
    bold = chosen_style.get("Bold", 1)

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
        f"MarginV={margin_v}"
    ]
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
    fade_height_frac: float = 0.38,
    max_opacity: float = 0.92,
    total_w: int = 1080,
    total_h: int = 1920,
    pad_top_px: int = 0,
    pad_bottom_px: int = 0
) -> str:
    """
    Returns path to a PNG gradient asset for bottom caption contrast.
    If not already on disk, generates it via Pillow automatically.
    Seamlessly blends into bottom frame padding (letterboxing).
    """
    h_pct = int(round(fade_height_frac * 100))
    o_pct = int(round(max_opacity * 100))
    fname = f"bottom_fade_{total_w}x{total_h}_t{pad_top_px}_b{pad_bottom_px}_h{h_pct}_o{o_pct}.png"
    grad_path = ASSETS_DIR / fname
    if grad_path.exists() and grad_path.stat().st_size > 500:
        return str(grad_path)

    w, h = total_w, total_h
    video_h = max(64, h - pad_top_px - pad_bottom_px)
    fade_px = int(video_h * max(0.1, min(0.8, fade_height_frac)))
    fade_start = max(pad_top_px, (h - pad_bottom_px) - fade_px)
    fade_end = h - pad_bottom_px

    from PIL import Image
    col = Image.new("RGBA", (1, h), (0, 0, 0, 0))
    putpixel = col.putpixel

    # Fade region on video
    for y in range(fade_start, fade_end):
        norm = (y - fade_start) / max(1, (fade_end - fade_start))
        alpha = int(255 * max_opacity * (norm ** 1.35))
        putpixel((0, y), (0, 0, 0, min(255, max(0, alpha))))

    # Below video (bottom padding) is solid black
    for y in range(fade_end, h):
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
    pad_bottom: float = 0.0
) -> Tuple[str, int, int, int, int]:
    """
    Computes ffmpeg crop/scale/pad filter string ensuring even dimensions.
    pos_x: 0.0 = left, 0.5 = center, 1.0 = right
    pos_y: 0.0 = top, 0.5 = center, 1.0 = bottom
    scale: 1.0 = standard framing, >1.0 = scale up / zoom in, <1.0 = scale down / fit / pad
    pad_top: Top frame padding percentage (0.0 to 40.0%)
    pad_bottom: Bottom frame padding percentage (0.0 to 50.0%)
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
    pad_top_pct = max(0.0, min(40.0, float(pad_top)))
    pad_bottom_pct = max(0.0, min(50.0, float(pad_bottom)))

    pad_top_px = int(round(base_h * (pad_top_pct / 100.0)))
    pad_bottom_px = int(round(base_h * (pad_bottom_pct / 100.0)))
    pad_top_px -= (pad_top_px % 2)
    pad_bottom_px -= (pad_bottom_px % 2)

    avail_w = base_w
    avail_h = max(64, base_h - pad_top_px - pad_bottom_px)
    avail_h -= (avail_h % 2)
    sub_aspect = avail_w / avail_h

    if in_aspect > sub_aspect:
        crop_base_h = in_h
        crop_base_w = int(round(in_h * sub_aspect))
    else:
        crop_base_w = in_w
        crop_base_h = int(round(in_w / sub_aspect))

    crop_base_w -= (crop_base_w % 2)
    crop_base_h -= (crop_base_h % 2)

    scale = max(0.2, min(5.0, float(scale)))
    pos_x = max(0.0, min(1.0, float(pos_x)))
    pos_y = max(0.0, min(1.0, float(pos_y)))

    if scale >= 0.999:
        # Scale up / Zoom in (or standard 1.0x)
        cw = int(round(crop_base_w / scale))
        ch = int(round(crop_base_h / scale))
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

        parts = [f"crop={cw}:{ch}:{off_x}:{off_y}"]
        if cw != avail_w or ch != avail_h:
            parts.append(f"scale={avail_w}:{avail_h}")
        if pad_top_px > 0 or pad_bottom_px > 0:
            parts.append(f"pad={base_w}:{base_h}:0:{pad_top_px}:color=black")

        filter_str = ",".join(parts)
        return filter_str, cw, ch, off_x, off_y
    else:
        # Scale down / Zoom out: fit video within target available window with black padding
        scaled_w = int(round(avail_w * scale))
        scaled_h = int(round(scaled_w * (in_h / in_w)))
        if scaled_h > avail_h:
            scaled_h = int(round(avail_h * scale))
            scaled_w = int(round(scaled_h * (in_w / in_h)))

        scaled_w -= (scaled_w % 2)
        scaled_h -= (scaled_h % 2)
        scaled_w = max(16, min(avail_w, scaled_w))
        scaled_h = max(16, min(avail_h, scaled_h))

        pad_x = max(0, int(round((avail_w - scaled_w) * pos_x)))
        pad_y = max(0, int(round((avail_h - scaled_h) * pos_y)))
        pad_x -= (pad_x % 2)
        pad_y -= (pad_y % 2)

        total_pad_x = pad_x
        total_pad_y = pad_top_px + pad_y

        filter_str = f"scale={scaled_w}:{scaled_h},pad={base_w}:{base_h}:{total_pad_x}:{total_pad_y}:color=black"
        return filter_str, scaled_w, scaled_h, total_pad_x, total_pad_y


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
    bitrate: str = "5000k",
    bottom_fade: bool = False,
    fade_height: float = 0.38,
    fade_opacity: float = 0.92,
    sub_margin_v: Optional[int] = None,
    cancel_check: Optional[Any] = None,
    proc_callback: Optional[Any] = None
) -> None:
    """Runs the FFmpeg pipeline with optional duration / start for preview or full render."""
    check_dependencies()
    v_info = probe_video(video_path)
    total_dur = v_info["duration"]
    has_vid_audio = v_info["has_audio"]
    has_music = music_path and os.path.exists(music_path) and music_volume > 0
    has_srt = srt_path and os.path.exists(srt_path) and os.path.getsize(srt_path) > 0

    encoders = get_available_encoders()
    hw_enabled = use_hw_accel and encoders.get("videotoolbox", False)

    # 1. Inputs
    cmd = ["ffmpeg", "-y"]
    if start_sec is not None and start_sec > 0:
        cmd.extend(["-ss", f"{start_sec:.3f}"])
    if duration_sec is not None and duration_sec > 0:
        cmd.extend(["-t", f"{duration_sec:.3f}"])

    cmd.extend(["-i", video_path])

    if has_music:
        cmd.extend(["-stream_loop", "-1", "-i", music_path])

    # 2. Video Filters (Crop -> Bottom Black Fade -> Subtitles)
    temp_shifted_srt = None
    burn_file = None
    if has_srt:
        sub_ext = Path(srt_path).suffix.lower()
        # If time-sliced or format is .lrc / .vtt, prepare shifted/converted SRT
        if (start_sec is not None and (start_sec > 0 or duration_sec is not None)) or sub_ext in [".lrc", ".vtt"]:
            temp_shifted_srt = str(Path(output_path).parent / f".tmp_sub_{uuid.uuid4().hex[:12]}.srt")
            end_sec = ((start_sec or 0.0) + duration_sec) if duration_sec else None
            ok = prepare_shifted_subtitles(
                sub_path=srt_path,
                output_srt_path=temp_shifted_srt,
                start_sec=(start_sec or 0.0),
                end_sec=end_sec
            )
            burn_file = temp_shifted_srt if ok else None
        else:
            burn_file = srt_path

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
            _, _, _, pad_b_px = extract_pad_info(crop_filter)
            final_margin_v = (sub_margin_v if sub_margin_v is not None else (pad_b_px + 35))
            style = build_ass_style(
                style_name=caption_style,
                font_name=font_name,
                font_size=font_size,
                margin_v=final_margin_v,
                fallback_color=font_color
            )
            sub_filter = f"subtitles='{escaped_sub}':force_style='{style}'"
        filter_complex_parts.append(f"[{v_curr}]{sub_filter}[v_sub]")
        v_curr = "v_sub"

    if v_curr != "0:v":
        video_map = f"[{v_curr}]"
    else:
        video_map = "0:v"

    # 3. Audio Filters (Mix main voice + ducked background music)
    if has_music and has_vid_audio:
        filter_complex_parts.append(f"[1:a]volume={music_volume:.3f}[bg_ducked]")
        filter_complex_parts.append(f"[0:a][bg_ducked]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a_out]")
        audio_map = "[a_out]"
    elif has_music and not has_vid_audio:
        filter_complex_parts.append(f"[1:a]volume={music_volume:.3f}[a_out]")
        audio_map = "[a_out]"
    elif not has_music and has_vid_audio:
        audio_map = "0:a"
    else:
        audio_map = None

    if filter_complex_parts:
        cmd.extend(["-filter_complex", ";".join(filter_complex_parts)])

    cmd.extend(["-map", video_map])
    if audio_map:
        cmd.extend(["-map", audio_map])

    # 4. Encoding Configuration
    if hw_enabled:
        print("⚡ Using Apple Silicon Hardware Acceleration (h264_videotoolbox)")
        cmd.extend([
            "-c:v", "h264_videotoolbox",
            "-b:v", bitrate,
            "-pix_fmt", "yuv420p"
        ])
    else:
        print("💻 Using CPU Encoder (libx264 veryfast)")
        cmd.extend([
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-pix_fmt", "yuv420p"
        ])

    if audio_map:
        cmd.extend(["-c:a", "aac", "-b:a", "192k"])

    cmd.extend(["-shortest", output_path])

    print("\n" + "=" * 65)
    print(f"🎬 Source Video  : {video_path}")
    print(f"📐 Resolution    : {v_info['width']}x{v_info['height']}")
    print(f"⏱️  Duration      : {duration_sec if duration_sec else total_dur:.1f}s")
    if crop_filter:
        print(f"✂️  Crop Filter   : {crop_filter}")
    if has_srt:
        print(f"💬 Subtitles     : {srt_path}")
    if has_music:
        print(f"🎵 Music Mix     : {music_path} (Vol: {int(music_volume*100)}%)")
    print(f"📁 Output File   : {output_path}")
    print("=" * 65 + "\n")

    start_time = time.time()
    try:
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

        last_report = time.time()
        error_lines = []
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
                print(f"\n{line_s}")
                error_lines.append(line_s)

        proc.stdout.close()
        return_code = proc.wait()

        if cancel_check and cancel_check():
            raise RuntimeError("Operation stopped by user (Master Kill).")

        if return_code != 0:
            err_msg = "\n".join(error_lines[-5:]) if error_lines else f"FFmpeg exited with error code {return_code}"
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

        fallback_cmd = ["ffmpeg", "-y"]
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
    bitrate: str = "5000k",
    bottom_fade: bool = False,
    fade_height: float = 0.38,
    fade_opacity: float = 0.92,
    sub_margin_v: Optional[int] = None,
    cancel_check: Optional[Any] = None,
    proc_callback: Optional[Any] = None,
    status_callback: Optional[Any] = None
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
                bitrate=bitrate,
                bottom_fade=bottom_fade,
                fade_height=fade_height,
                fade_opacity=fade_opacity,
                sub_margin_v=sub_margin_v,
                cancel_check=cancel_check,
                proc_callback=proc_callback
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
    parser.add_argument("--sub-margin", type=int, default=None, help="Vertical margin for subtitles (defaults to pad_bottom + 35)")
    parser.add_argument("--bottom-fade", "--black-fade", dest="bottom_fade", action="store_true", default=False, help="Add smooth bottom black fade gradient for caption contrast")
    parser.add_argument("--fade-height", type=float, default=0.38, help="Fraction of frame height for bottom black fade (default: 0.38)")
    parser.add_argument("--fade-opacity", type=float, default=0.92, help="Maximum opacity of bottom black fade (default: 0.92)")
    parser.add_argument("-s", "--srt", "--sub", dest="srt", help="Path to subtitle or lyrics file (.srt, .ass, .lrc, .vtt)")
    parser.add_argument("-m", "--music", help="Path to background music file")
    parser.add_argument("--volume", type=float, default=0.12, help="Background music volume multiplier (default: 0.12)")
    parser.add_argument("--start", type=float, default=None, help="Start time offset in seconds")
    parser.add_argument("--duration", type=float, default=None, help="Render duration in seconds (e.g. 60 for 1-min preview)")
    parser.add_argument("--no-hw", action="store_true", help="Disable Apple Silicon VideoToolbox hardware acceleration")
    parser.add_argument("--font", default="Kohinoor Devanagari", help="Font for subtitles")
    parser.add_argument("--font-size", type=int, default=22, help="Font size for subtitles")
    parser.add_argument("--font-color", default="&H00FFFFFF", help="Font color for subtitles (&H00FFFFFF for white, &H0000FFFF for yellow)")
    parser.add_argument("--caption-style", default="✨ Soothe White Light (Soft Clean Glow)", help="Preset caption style (e.g. 'Soothe White Light', 'Viral Reel Yellow', 'Frosted Pill Box')")
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
            pad_bottom=args.pad_bottom
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
            bitrate=args.bitrate,
            bottom_fade=args.bottom_fade,
            fade_height=args.fade_height,
            fade_opacity=args.fade_opacity,
            sub_margin_v=args.sub_margin
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
            bitrate=args.bitrate,
            bottom_fade=args.bottom_fade,
            fade_height=args.fade_height,
            fade_opacity=args.fade_opacity,
            sub_margin_v=args.sub_margin
        )


if __name__ == "__main__":
    main()
