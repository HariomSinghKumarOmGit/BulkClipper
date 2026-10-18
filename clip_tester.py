#!/usr/bin/env python3
"""
clip_tester.py - Multimedia Clipping & Production Engine

Automates:
  1. High-precision video slicing via FFmpeg
  2. SRT transcript re-indexing and time-shifting relative to 00:00:00 using pysrt
  3. Audio ducking and mixing (spoken voice crisp + background music at ~10-15%)
  4. Devanagari & Latin subtitle burning with clean formatting and outline
  5. Default thumbnail frame capture saved matching the clip title
  6. Interactive CLI approval workflow (Approve / Reject) & Batch processing
"""

import os
import sys
import re
import json
import time
import shutil
import logging
import argparse
import subprocess
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any

try:
    import pysrt
except ImportError:
    print("Error: 'pysrt' package is required. Run 'pip install pysrt' to install.", file=sys.stderr)
    sys.exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("ClipTester")


# ==============================================================================
# 1. TIME UTILITIES & TIMESTAMP PARSING
# ==============================================================================

def timestamp_to_seconds(ts: str) -> float:
    """
    Converts timestamp string (HH:MM:SS.mmm, HH:MM:SS, MM:SS, or SS) to float seconds.
    """
    ts = ts.strip().replace(",", ".")
    parts = ts.split(":")
    if len(parts) == 3:
        h, m, s = parts
        return float(h) * 3600 + float(m) * 60 + float(s)
    elif len(parts) == 2:
        m, s = parts
        return float(m) * 60 + float(s)
    elif len(parts) == 1:
        return float(parts[0])
    raise ValueError(f"Invalid timestamp format: '{ts}'")


def seconds_to_srt_time(seconds: float) -> pysrt.SubRipTime:
    """Converts seconds (float) to pysrt.SubRipTime object."""
    ms = int(round(seconds * 1000))
    return pysrt.SubRipTime(milliseconds=max(0, ms))


def sanitize_filename(name: str) -> str:
    """
    Sanitizes title for safe file naming while preserving Hindi/Devanagari characters.
    Replaces colons and separators with hyphens.
    """
    # Clean AI citations
    s = re.sub(r'\[cite:\s*\d+\]', '', name, flags=re.IGNORECASE)
    s = re.sub(r'\[\d+\]', '', s)
    # Replace colons and pipe characters with ' - '
    s = re.sub(r'[:|]+', ' - ', s)
    # Remove prohibited filesystem characters, quotes, brackets
    s = re.sub(r"""['"`‘’“”\\/*?<>|\[\]{}\x00-\x1f]""", '', s)
    # Normalize consecutive whitespace
    s = re.sub(r'\s+', ' ', s).strip('. -')
    return s if s else "untitled_clip"


def parse_timestamp_entries(text: str) -> List[Dict[str, Any]]:
    """
    Parses timestamp segments and titles from text.
    Supports markdown bullets, list items, AI citations.
    """
    ts_pattern = re.compile(
        r'(?P<start>(?:\d{1,2}:)?\d{1,2}:\d{2}(?:[.,]\d+)?)\s*[-–—]\s*(?P<end>(?:\d{1,2}:)?\d{1,2}:\d{2}(?:[.,]\d+)?)'
    )
    entries = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r'^(?:[-*+•]|\d+[.)])\s*', '', line)
        m = ts_pattern.search(line)
        if not m:
            continue
        start_raw = m.group('start')
        end_raw = m.group('end')
        rest = line[m.end():].strip()
        title_raw = re.sub(r'^[\s\-–—:|`]+', '', rest)
        title_raw = re.sub(r'[\s`]+$', '', title_raw)
        title_raw = re.sub(r'\[cite:\s*\d+\]', '', title_raw, flags=re.IGNORECASE)
        title_raw = re.sub(r'\[\d+\]', '', title_raw).strip()

        try:
            start_sec = timestamp_to_seconds(start_raw)
            end_sec = timestamp_to_seconds(end_raw)
            if end_sec <= start_sec:
                continue

            duration = end_sec - start_sec

            # Auto-correct LLM hour-jump bug (e.g. 01:01:30 - 02:02:30 -> duration was 3660s)
            s_parts = start_raw.split(':')
            e_parts = end_raw.split(':')
            if len(s_parts) == 3 and len(e_parts) == 3 and duration >= 3500:
                try:
                    h_diff = int(e_parts[0]) - int(s_parts[0])
                    m_diff = abs(int(e_parts[1]) - int(s_parts[1]))
                    if h_diff > 0 and m_diff <= 3:
                        cand_dur = (end_sec - h_diff * 3600) - start_sec
                        if 10 <= cand_dur <= 180:
                            end_sec = end_sec - (h_diff * 3600)
                            end_raw = f"{s_parts[0]}:{e_parts[1]}:{e_parts[2]}"
                            duration = cand_dur
                except Exception:
                    pass

            clean_t = sanitize_filename(title_raw)
            entries.append({
                "start_str": start_raw,
                "end_str": end_raw,
                "start_sec": start_sec,
                "end_sec": end_sec,
                "duration": duration,
                "title": title_raw,
                "clean_title": clean_t
            })
        except Exception as e:
            logger.warning(f"Failed parsing segment {start_raw} - {end_raw}: {e}")

    return entries


# ==============================================================================
# 2. SUBTITLE TIME-SHIFTING & ALIGNMENT
# ==============================================================================

def shift_and_slice_srt(
    master_srt_path: str,
    output_srt_path: str,
    start_sec: float,
    end_sec: float
) -> bool:
    """
    Extracts subtitles falling within [start_sec, end_sec] and shifts their
    timestamps relative to 00:00:00 (new_start = original_start - start_sec).
    Returns True if any subtitles were extracted, False if none matched.
    """
    from sub_utils import load_any_subtitles
    subs = load_any_subtitles(master_srt_path)

    start_ms = int(round(start_sec * 1000))
    end_ms = int(round(end_sec * 1000))

    new_subs = pysrt.SubRipFile()
    index = 1

    for item in subs:
        item_start_ms = item.start.ordinal
        item_end_ms = item.end.ordinal

        # Check if subtitle overlaps with the target clip interval
        if item_end_ms <= start_ms or item_start_ms >= end_ms:
            continue

        # Shift relative to clip start
        rel_start_ms = max(0, item_start_ms - start_ms)
        rel_end_ms = min(end_ms - start_ms, item_end_ms - start_ms)

        if rel_end_ms <= rel_start_ms:
            continue

        item.start = pysrt.SubRipTime(milliseconds=rel_start_ms)
        item.end = pysrt.SubRipTime(milliseconds=rel_end_ms)
        item.index = index
        index += 1
        new_subs.append(item)

    if len(new_subs) == 0:
        logger.info("No subtitles found in the specified trim window.")
        # Create empty SRT file
        new_subs.save(output_srt_path, encoding="utf-8")
        return False

    new_subs.save(output_srt_path, encoding="utf-8")
    logger.info(f"Re-aligned {len(new_subs)} subtitle cue(s) relative to 00:00:00 -> '{output_srt_path}'")
    return True


# ==============================================================================
# 3. MULTIMEDIA ENGINE (FFMPEG & FFPROBE)
# ==============================================================================

def check_ffmpeg() -> None:
    """Verifies FFmpeg and ffprobe are installed and accessible."""
    for tool in ["ffmpeg", "ffprobe"]:
        if shutil.which(tool) is None:
            raise RuntimeError(f"Required multimedia tool '{tool}' not found in PATH.")


def get_video_streams_info(video_path: str) -> Dict[str, Any]:
    """Inspects video file using ffprobe to detect video/audio streams and duration."""
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", video_path
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffprobe failed for '{video_path}': {res.stderr}")
    data = json.loads(res.stdout)
    has_audio = any(s.get("codec_type") == "audio" for s in data.get("streams", []))
    duration = float(data.get("format", {}).get("duration", 0.0))
    return {"has_audio": has_audio, "duration": duration}


def get_best_devanagari_font() -> str:
    """Selects the best available font for Devanagari text on macOS / Linux."""
    preferred = [
        "Kohinoor Devanagari",
        "Devanagari Sangam MN",
        "ITF Devanagari",
        "Devanagari MT",
        "Noto Sans Devanagari",
        "Arial Unicode MS",
        "Arial"
    ]
    if sys.platform == "darwin":
        # Check available fonts via fc-list
        try:
            res = subprocess.run(["fc-list", ":", "family"], capture_output=True, text=True)
            if res.returncode == 0:
                output = res.stdout
                for font in preferred:
                    if font.lower() in output.lower():
                        return font
        except Exception:
            pass
    return "Kohinoor Devanagari" if sys.platform == "darwin" else "Noto Sans Devanagari"


def escape_ffmpeg_filter_path(path: str) -> str:
    """
    Escapes paths for use inside FFmpeg filter strings (subtitles=...).
    Handles colons and backslashes.
    """
    # Replace backslashes with forward slashes
    p = path.replace("\\", "/")
    # Escape colons, single quotes, brackets
    p = p.replace(":", "\\:").replace("'", "\\'")
    return p


def render_clip(
    video_path: str,
    start_sec: float,
    duration_sec: float,
    output_path: str,
    srt_path: Optional[str] = None,
    music_path: Optional[str] = None,
    music_volume: float = 0.12,
    font_name: Optional[str] = None,
    font_size: int = 22,
    font_color: str = "&H00FFFFFF",  # ASS White
    outline_color: str = "&H00000000",  # ASS Black
    preset: str = "veryfast",
    crf: int = 18
) -> None:
    """
    Executes FFmpeg pipeline:
      - Precision slice: -ss {start_sec} -t {duration_sec}
      - Subtitles burning with Devanagari styling
      - Looped background music with volume ducking (volume={music_volume})
      - Audio mixing: amix with normalize=0 to preserve crisp speech dialogue
    """
    vid_info = get_video_streams_info(video_path)
    has_vid_audio = vid_info["has_audio"]
    has_music = music_path is not None and os.path.exists(music_path) and music_volume > 0
    has_srt = srt_path is not None and os.path.exists(srt_path) and os.path.getsize(srt_path) > 0

    if not font_name:
        font_name = get_best_devanagari_font()

    inputs = []
    # Input 0: Main Video sliced accurately
    inputs.extend(["-ss", f"{start_sec:.3f}", "-t", f"{duration_sec:.3f}", "-i", video_path])

    # Input 1: Background Music (looped indefinitely if present)
    if has_music:
        inputs.extend(["-stream_loop", "-1", "-i", music_path])

    filter_chains = []

    # 1. Video filter chain (Burn subtitles if available)
    if has_srt:
        escaped_srt = escape_ffmpeg_filter_path(os.path.abspath(srt_path))
        style = (
            f"FontName={font_name},FontSize={font_size},"
            f"PrimaryColour={font_color},OutlineColour={outline_color},"
            f"BorderStyle=1,Outline=2.5,Shadow=1,Alignment=2,MarginV=35"
        )
        filter_chains.append(f"[0:v]subtitles='{escaped_srt}':force_style='{style}'[v_out]")
        video_map = "[v_out]"
    else:
        video_map = "0:v"

    # 2. Audio filter chain
    audio_map = "0:a"
    if has_music and has_vid_audio:
        # Scale background music, mix with video audio (speech stays at 100% via normalize=0)
        filter_chains.append(f"[1:a]volume={music_volume:.3f}[bg_ducked]")
        filter_chains.append(f"[0:a][bg_ducked]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a_out]")
        audio_map = "[a_out]"
    elif has_music and not has_vid_audio:
        # No audio in video, only background music
        filter_chains.append(f"[1:a]volume={music_volume:.3f}[a_out]")
        audio_map = "[a_out]"
    elif not has_music and has_vid_audio:
        audio_map = "0:a"
    else:
        audio_map = None

    cmd = ["ffmpeg", "-y"] + inputs

    if filter_chains:
        cmd.extend(["-filter_complex", ";".join(filter_chains)])

    cmd.extend(["-map", video_map])
    if audio_map:
        cmd.extend(["-map", audio_map])

    # Video & Audio encodings
    cmd.extend([
        "-c:v", "libx264",
        "-preset", preset,
        "-crf", str(crf),
        "-pix_fmt", "yuv420p"
    ])
    if audio_map:
        cmd.extend(["-c:a", "aac", "-b:a", "192k"])

    cmd.extend(["-shortest", output_path])

    logger.info(f"Rendering clip ({duration_sec:.1f}s)...")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        logger.error(f"FFmpeg render error: {res.stderr[-800:]}")
        raise RuntimeError("FFmpeg render failed. See logs above for details.")


def extract_thumbnail(
    clip_path: str,
    output_thumb_path: str,
    offset_sec: float = 2.0
) -> None:
    """
    Extracts a sharp PNG keyframe from the clip at offset_sec (or midway if clip is short).
    """
    info = get_video_streams_info(clip_path)
    dur = info["duration"]
    target_sec = min(offset_sec, max(0.2, dur / 2.0))

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{target_sec:.3f}",
        "-i", clip_path,
        "-vframes", "1",
        "-q:v", "2",
        output_thumb_path
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        logger.error(f"Failed to extract thumbnail: {res.stderr[-500:]}")
        raise RuntimeError("Thumbnail extraction failed.")


def open_media_in_os(file_path: str) -> None:
    """Opens file using the default OS viewer (macOS: open, Linux: xdg-open, Windows: start)."""
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", file_path], check=False)
        elif sys.platform.startswith("linux"):
            subprocess.run(["xdg-open", file_path], check=False)
        elif sys.platform == "win32":
            os.startfile(file_path)
    except Exception as e:
        logger.warning(f"Could not open media file automatically: {e}")


# ==============================================================================
# 4. SINGLE CLIP & APPROVAL PIPELINE
# ==============================================================================

def process_single_clip(
    video_path: str,
    start_sec: float,
    end_sec: float,
    title: str,
    srt_path: Optional[str] = None,
    music_path: Optional[str] = None,
    music_volume: float = 0.12,
    thumb_offset: float = 2.0,
    font_name: Optional[str] = None,
    output_dir: str = "./approved_clips",
    auto_approve: bool = False,
    open_thumb: bool = True
) -> bool:
    """
    Processes one clip segment:
      1. Time-shifts subtitles to 00:00:00 (if SRT provided)
      2. Renders video with audio ducking and subtitles
      3. Generates keyframe thumbnail
      4. Interactive review: prompt user to Approve or Reject
    Returns True if approved, False if rejected or skipped.
    """
    clean_title = sanitize_filename(title)
    duration = end_sec - start_sec

    # Set up temp workspace
    temp_dir = Path("./temp_clip_cache")
    temp_dir.mkdir(parents=True, exist_ok=True)

    temp_video = temp_dir / f"render_{int(time.time())}.mp4"
    temp_thumb = temp_dir / f"thumb_{int(time.time())}.png"
    temp_srt = temp_dir / f"shift_{int(time.time())}.srt"

    logger.info("=" * 70)
    logger.info(f"Target Clip: '{clean_title}'")
    logger.info(f"Time Range : {start_sec:.2f}s -> {end_sec:.2f}s (Duration: {duration:.2f}s)")
    logger.info("=" * 70)

    start_clock = time.time()

    # Step B: Shift SRT
    has_subtitles = False
    if srt_path and os.path.exists(srt_path):
        has_subtitles = shift_and_slice_srt(
            master_srt_path=srt_path,
            output_srt_path=str(temp_srt),
            start_sec=start_sec,
            end_sec=end_sec
        )

    # Step C: Render Video & Mix Audio
    render_clip(
        video_path=video_path,
        start_sec=start_sec,
        duration_sec=duration,
        output_path=str(temp_video),
        srt_path=str(temp_srt) if has_subtitles else None,
        music_path=music_path,
        music_volume=music_volume,
        font_name=font_name
    )

    # Step D: Thumbnail Generation
    extract_thumbnail(
        clip_path=str(temp_video),
        output_thumb_path=str(temp_thumb),
        offset_sec=thumb_offset
    )

    elapsed = time.time() - start_clock
    logger.info(f"Processing finished in {elapsed:.2f} seconds.")

    # Step E: Interactive Review & Approval
    approved = False
    if auto_approve:
        logger.info("Auto-approve flag active: Keeping clip.")
        approved = True
    else:
        if open_thumb:
            logger.info("Opening generated thumbnail for preview...")
            open_media_in_os(str(temp_thumb))

        print("\n" + "-" * 60)
        print(f"🎬 Title:     {clean_title}")
        print(f"⏱️  Duration:  {duration:.1f}s (from {start_sec:.1f}s to {end_sec:.1f}s)")
        print(f"📁 Video:     {temp_video}")
        print(f"🖼️  Thumbnail: {temp_thumb}")
        print("-" * 60)

        while True:
            choice = input("👉 [A]pprove and keep clip / [R]eject and delete? (a/r): ").strip().lower()
            if choice in ["a", "approve", "y", "yes", ""]:
                approved = True
                break
            elif choice in ["r", "reject", "n", "no"]:
                approved = False
                break
            else:
                print("Please enter 'a' to approve or 'r' to reject.")

    # Save or Cleanup
    out_dir_path = Path(output_dir)
    out_dir_path.mkdir(parents=True, exist_ok=True)

    final_video = out_dir_path / f"{clean_title}.mp4"
    final_thumb = out_dir_path / f"{clean_title}.png"

    if approved:
        # Move temporary files to output directory
        shutil.move(str(temp_video), str(final_video))
        shutil.move(str(temp_thumb), str(final_thumb))
        logger.info(f"✅ Approved! Saved:")
        logger.info(f"   Video:     {final_video}")
        logger.info(f"   Thumbnail: {final_thumb}")
    else:
        logger.warning(f"❌ Rejected. Discarding clip '{clean_title}'.")
        if temp_video.exists():
            temp_video.unlink()
        if temp_thumb.exists():
            temp_thumb.unlink()

    # Clean up temp SRT
    if temp_srt.exists():
        temp_srt.unlink()

    return approved


# ==============================================================================
# 5. CLI INTERFACE & DISCOVERY
# ==============================================================================

def interactive_wizard() -> None:
    """Interactive wizard if user runs script without arguments."""
    print("=" * 65)
    print("        🎬 AUTOMATED VIDEO CLIPPER & AUDIO MIXER        ")
    print("=" * 65)

    video_path = input("Enter Input Video path (.mp4 / .mov): ").strip().strip("'\"")
    while not os.path.exists(video_path):
        print(f"File not found: {video_path}")
        video_path = input("Enter Input Video path: ").strip().strip("'\"")

    srt_path = input("Enter Master SRT path (leave blank if none): ").strip().strip("'\"")
    if not srt_path or not os.path.exists(srt_path):
        srt_path = None

    music_path = input("Enter Background Music path (leave blank if none): ").strip().strip("'\"")
    if not music_path or not os.path.exists(music_path):
        music_path = None

    mode = input("\nSelect Mode:\n  [1] Single Clip Test\n  [2] Batch Timestamps (from file or text)\nChoose (1 or 2): ").strip()

    if mode == "2":
        batch_input = input("Enter timestamps file path (or paste text directly): ").strip().strip("'\"")
        if os.path.exists(batch_input):
            with open(batch_input, "r", encoding="utf-8") as f:
                text = f.read()
        else:
            text = batch_input

        entries = parse_timestamp_entries(text)
        if not entries:
            print("No valid timestamps found. Exiting.")
            return

        print(f"\nFound {len(entries)} clip segment(s):")
        for i, e in enumerate(entries, 1):
            print(f"  {i}. [{e['start_str']} - {e['end_str']}] {e['title']}")

        auto = input("\nAuto-approve all clips without prompt? (y/n, default n): ").strip().lower() == "y"
        for e in entries:
            process_single_clip(
                video_path=video_path,
                start_sec=e["start_sec"],
                end_sec=e["end_sec"],
                title=e["title"],
                srt_path=srt_path,
                music_path=music_path,
                auto_approve=auto
            )
    else:
        start_str = input("Enter Start Time (e.g. 00:00:00): ").strip()
        end_str = input("Enter End Time (e.g. 00:00:21): ").strip()
        title = input("Enter Title: ").strip() or "test_clip"

        start_sec = timestamp_to_seconds(start_str)
        end_sec = timestamp_to_seconds(end_str)

        process_single_clip(
            video_path=video_path,
            start_sec=start_sec,
            end_sec=end_sec,
            title=title,
            srt_path=srt_path,
            music_path=music_path
        )


def main():
    check_ffmpeg()

    parser = argparse.ArgumentParser(
        description="Modular CLI for video slicing, SRT shifting, audio ducking, and thumbnail extraction."
    )
    parser.add_argument("-v", "--video", help="Input video file path (.mp4, .mov)")
    parser.add_argument("-s", "--srt", help="Master transcript SRT file path")
    parser.add_argument("-m", "--music", help="Background music audio file path (.mp3, .wav)")
    parser.add_argument("--start", help="Clip start timestamp (e.g. 00:05:00 or 00:05:00.000)")
    parser.add_argument("--end", help="Clip end timestamp (e.g. 00:06:00 or 00:06:00.000)")
    parser.add_argument("-t", "--title", default="test_clip", help="Title for the clip (used for output file naming)")
    parser.add_argument("--volume", type=float, default=0.12, help="Background music volume multiplier (default: 0.12)")
    parser.add_argument("--thumb-offset", type=float, default=2.0, help="Thumbnail offset in seconds into clip (default: 2.0)")
    parser.add_argument("-f", "--batch-file", help="Path to text file containing timestamps & titles")
    parser.add_argument("--batch-text", help="Raw string containing timestamps & titles")
    parser.add_argument("-o", "--output-dir", default="./approved_clips", help="Directory to store approved clips (default: ./approved_clips)")
    parser.add_argument("-y", "--auto-approve", action="store_true", help="Auto-approve all clips without interactive prompt")
    parser.add_argument("--no-open-thumb", action="store_true", help="Do not automatically launch image viewer for thumbnail preview")
    parser.add_argument("--font", help="Specific font name to use for subtitles (e.g. 'Kohinoor Devanagari')")

    args = parser.parse_args()

    # If no video passed, launch interactive wizard
    if not args.video and not args.batch_file and not args.batch_text:
        interactive_wizard()
        return

    if not args.video:
        parser.error("--video is required when running in non-interactive mode.")

    # Batch Mode
    raw_batch = ""
    if args.batch_file:
        with open(args.batch_file, "r", encoding="utf-8") as f:
            raw_batch = f.read()
    elif args.batch_text:
        raw_batch = args.batch_text

    if raw_batch:
        entries = parse_timestamp_entries(raw_batch)
        if not entries:
            logger.error("No valid timestamp entries could be parsed from batch input.")
            sys.exit(1)

        logger.info(f"Loaded {len(entries)} batch entry(s). Starting execution...")
        approved_count = 0
        for i, entry in enumerate(entries, 1):
            logger.info(f"[{i}/{len(entries)}] Processing: {entry['title']}")
            ok = process_single_clip(
                video_path=args.video,
                start_sec=entry["start_sec"],
                end_sec=entry["end_sec"],
                title=entry["title"],
                srt_path=args.srt,
                music_path=args.music,
                music_volume=args.volume,
                thumb_offset=args.thumb_offset,
                font_name=args.font,
                output_dir=args.output_dir,
                auto_approve=args.auto_approve,
                open_thumb=not args.no_open_thumb
            )
            if ok:
                approved_count += 1

        logger.info("=" * 70)
        logger.info(f"Batch Processing Complete: {approved_count}/{len(entries)} clip(s) approved.")
        logger.info(f"Output files stored in: {os.path.abspath(args.output_dir)}")
        logger.info("=" * 70)
        return

    # Single Clip Test Mode
    if not args.start or not args.end:
        parser.error("Both --start and --end are required for single clip mode.")

    start_sec = timestamp_to_seconds(args.start)
    end_sec = timestamp_to_seconds(args.end)

    process_single_clip(
        video_path=args.video,
        start_sec=start_sec,
        end_sec=end_sec,
        title=args.title,
        srt_path=args.srt,
        music_path=args.music,
        music_volume=args.volume,
        thumb_offset=args.thumb_offset,
        font_name=args.font,
        output_dir=args.output_dir,
        auto_approve=args.auto_approve,
        open_thumb=not args.no_open_thumb
    )


if __name__ == "__main__":
    main()
