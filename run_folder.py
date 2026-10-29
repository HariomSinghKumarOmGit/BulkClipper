#!/usr/bin/env python3
"""
run_folder.py - 1-Click Folder Automation for Video Clipping

Give it a folder (or click to choose a folder via native Mac dialog).
It automatically:
  1. Detects your video file (.mp4, .mov, etc.)
  2. Detects your subtitles/lyrics (.lrc, .ass, .srt)
  3. Detects your background music (.mp3, .wav)
  4. Detects your timestamps file (.txt, .md) with titles
  5. Clips all segments at maximum Apple Silicon hardware speed (~50x)
  6. Generates keyframe thumbnails and saves each clip named by its title
  7. Opens the final output folder in Finder!
"""

import os
import sys
import time
import subprocess
from pathlib import Path
from tkinter import filedialog, Tk, messagebox

from sub_utils import parse_timestamp_entries, sanitize_filename
from process_full import probe_video, compute_crop_filter, run_pipeline
from clip_tester import extract_thumbnail
from config_manager import load_saved_config

BASE_DIR = Path(__file__).resolve().parent


def detect_folder_assets(folder_path: Path):
    video_exts = {".mp4", ".mov", ".mkv", ".m4v", ".webm"}
    sub_exts = {".lrc", ".ass", ".ssa", ".srt", ".vtt"}
    audio_exts = {".mp3", ".wav", ".m4a", ".aac", ".ogg"}

    videos = []
    subtitles = []
    audios = []
    ts_files = []

    for f in folder_path.iterdir():
        if f.name.startswith(".") or f.is_dir():
            continue
        ext = f.suffix.lower()
        if ext in video_exts:
            videos.append(f)
        elif ext in sub_exts:
            subtitles.append(f)
        elif ext in audio_exts:
            audios.append(f)
        elif ext in {".txt", ".md"}:
            try:
                with open(f, "r", encoding="utf-8", errors="ignore") as fp:
                    content = fp.read()
                entries = parse_timestamp_entries(content)
                if len(entries) > 0:
                    ts_files.append((f, len(entries), content))
            except Exception:
                pass

    # Pick best candidates
    chosen_video = max(videos, key=lambda v: v.stat().st_size) if videos else None
    chosen_sub = subtitles[0] if subtitles else None
    chosen_audio = audios[0] if audios else None
    
    # Pick timestamp file with the most parsed clips
    if ts_files:
        ts_files.sort(key=lambda x: x[1], reverse=True)
        chosen_ts_file, ts_count, ts_content = ts_files[0]
    else:
        # Check if clips_timestamps.txt exists in mycliper folder
        fallback_ts = BASE_DIR / "clips_timestamps.txt"
        if fallback_ts.exists():
            with open(fallback_ts, "r", encoding="utf-8") as fp:
                ts_content = fp.read()
            chosen_ts_file = fallback_ts
            ts_count = len(parse_timestamp_entries(ts_content))
        else:
            chosen_ts_file, ts_count, ts_content = None, 0, ""

    return {
        "video": chosen_video,
        "subtitles": chosen_sub,
        "audio": chosen_audio,
        "ts_file": chosen_ts_file,
        "ts_count": ts_count,
        "ts_content": ts_content
    }


def run_folder_system(
    folder_path_str: str,
    ratio: Optional[str] = None,
    volume: Optional[float] = None
):
    folder = Path(folder_path_str).resolve()
    if not folder.exists() or not folder.is_dir():
        print(f"Error: Folder '{folder}' does not exist.", file=sys.stderr)
        return

    # Load persistent user settings
    cfg = load_saved_config()
    s_cfg = cfg["shorts_config"]
    v_cfg = cfg["videos_config"]
    sub_cfg = cfg.get("subtitles", {})
    caption_style = sub_cfg.get("style", "✨ Soothe White Light (Soft Clean Glow)")
    font_size_str = sub_cfg.get("size", "Medium (22pt)")
    font_size = int(''.join(c for c in font_size_str if c.isdigit()) or 22)
    caption_pos = sub_cfg.get("position", "Caption: On Video Fade (Above Padding)")

    shorts_ratio = ratio or s_cfg["ratio_val"]
    music_cfg = cfg.get("music", {})
    universal_vol = volume if volume is not None else music_cfg.get("volume", s_cfg.get("vol", 0.12))

    print("\n" + "=" * 65)
    print(f"📂 1-CLICK FOLDER AUTO-RUN: {folder.name}")
    print(f"⚙️  Using Saved Config: Shorts [{shorts_ratio}], Videos [{v_cfg['ratio_val']}]")
    print(f"💬 Caption Style      : {caption_style} ({font_size}pt)")
    print(f"🎵 Background Music   : Universal Volume {int(round(universal_vol * 100))}%")
    print("=" * 65)

    assets = detect_folder_assets(folder)

    if not assets["video"]:
        # Fallback check on Desktop for default video
        desktop_vid = Path.home() / "Desktop" / "CONCENTRATION AND TRATAK_480p.mp4"
        if desktop_vid.exists():
            assets["video"] = desktop_vid
            print(f"ℹ️  Using video from Desktop: {desktop_vid.name}")
        else:
            print("❌ No video file found in folder.", file=sys.stderr)
            return

    video_path = assets["video"]
    sub_path = assets["subtitles"]
    audio_path = assets["audio"]
    ts_content = assets["ts_content"]

    print(f"🎬 Video File      : {video_path.name}")
    print(f"💬 Subtitle/Lyrics : {sub_path.name if sub_path else 'None (skipping subtitles)'}")
    print(f"🎵 Background Music: {audio_path.name if audio_path else 'None (original audio)'}")
    print(f"✂️  Timestamps File : {assets['ts_file'].name if assets['ts_file'] else 'None'}")
    print(f"📊 Total Clips     : {assets['ts_count']}")
    print("-" * 65)

    if assets["ts_count"] == 0:
        print("❌ No timestamps found to clip. Add a timestamps.txt or clips_timestamps.txt file.", file=sys.stderr)
        return

    entries = parse_timestamp_entries(ts_content)
    out_dir = folder / "approved_clips"
    out_dir.mkdir(parents=True, exist_ok=True)

    v_info = probe_video(str(video_path))
    in_w = v_info["width"]
    in_h = v_info["height"]

    shorts_crop_filter, crop_w, crop_h, _, _ = compute_crop_filter(
        in_w=in_w,
        in_h=in_h,
        ratio=shorts_ratio,
        pos_x=s_cfg["pos_x"],
        pos_y=s_cfg["pos_y"],
        scale=s_cfg["scale"],
        pad_top=s_cfg["pad_top"],
        pad_bottom=s_cfg["pad_bottom"]
    )

    videos_crop_filter, _, _, _, _ = compute_crop_filter(
        in_w=in_w,
        in_h=in_h,
        ratio=v_cfg["ratio_val"],
        pos_x=v_cfg["pos_x"],
        pos_y=v_cfg["pos_y"],
        scale=v_cfg["scale"],
        pad_top=v_cfg["pad_top"],
        pad_bottom=v_cfg["pad_bottom"]
    )

    s_fade_h = s_cfg.get("fade_height", 0.38)
    s_fade_o = s_cfg.get("fade_opacity", 0.92)
    v_fade_h = v_cfg.get("fade_height", 0.38)
    v_fade_o = v_cfg.get("fade_opacity", 0.92)

    print(f"📐 Shorts Crop (≤3m): {shorts_ratio} ({crop_w}x{crop_h}) [Scale: {s_cfg['scale']:.2f}x, Pad: T:{s_cfg['pad_top']:.1f}% B:{s_cfg['pad_bottom']:.1f}%, Vol: {int(shorts_vol*100)}%]")
    print(f"📐 Long Video (>3m) : {v_cfg['ratio_val']} [Scale: {v_cfg['scale']:.2f}x, Pad: T:{v_cfg['pad_top']:.1f}% B:{v_cfg['pad_bottom']:.1f}%, Vol: {int(videos_vol*100)}%]")
    print(f"🖤 Bottom Black Fade: Shorts={'ON' if s_cfg['bottom_fade'] else 'OFF'} (H:{int(s_fade_h*100)}%, O:{int(s_fade_o*100)}%), Videos={'ON' if v_cfg['bottom_fade'] else 'OFF'}")
    print(f"📁 Destination      : {out_dir}")
    print("=" * 65 + "\n")

    start_total_time = time.time()
    total = len(entries)

    for i, entry in enumerate(entries, 1):
        clean_t = entry["clean_title"]
        out_vid = out_dir / f"{clean_t}.mp4"
        out_thumb = out_dir / f"{clean_t}.png"

        dur = entry["duration"]
        is_long = dur > 180.0
        chosen_crop = videos_crop_filter if is_long else shorts_crop_filter
        crop_tag = f"{v_cfg['ratio_val']} Video" if is_long else f"{shorts_ratio} Shorts"
        clip_vol = universal_vol
        clip_fade = v_cfg["bottom_fade"] if is_long else s_cfg["bottom_fade"]
        clip_fade_h = v_fade_h if is_long else s_fade_h
        clip_fade_o = v_fade_o if is_long else s_fade_o
        clip_sub_margin = 35 if "Inside Black" in caption_pos else None

        print(f"[{i:02d}/{total:02d}] ⚡ Clipping [{crop_tag}]: {entry['title']} ({dur:.1f}s)...")

        t0 = time.time()
        run_pipeline(
            video_path=str(video_path),
            output_path=str(out_vid),
            crop_filter=chosen_crop,
            srt_path=str(sub_path) if sub_path else None,
            music_path=str(audio_path) if audio_path else None,
            music_volume=clip_vol,
            start_sec=entry["start_sec"],
            duration_sec=dur,
            use_hw_accel=True,
            caption_style=caption_style,
            font_size=font_size,
            bottom_fade=clip_fade,
            fade_height=clip_fade_h,
            fade_opacity=clip_fade_o,
            sub_margin_v=clip_sub_margin
        )

        extract_thumbnail(str(out_vid), str(out_thumb), offset_sec=min(5.0, max(0.5, dur / 2.0)))
        print(f"       ✅ Saved in {time.time() - t0:.1f}s -> {out_vid.name}")

    total_elapsed = time.time() - start_total_time
    print("\n" + "=" * 65)
    print(f"🎉 COMPLETED ALL {total} CLIPS in {total_elapsed:.1f} seconds!")
    print(f"📁 Saved to: {out_dir}")
    print("=" * 65 + "\n")

    # Open Finder
    subprocess.run(["open", str(out_dir)], check=False)

    # Open Finder
    subprocess.run(["open", str(out_dir)], check=False)


def main():
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        target_folder = sys.argv[1]
    else:
        # Open native macOS folder chooser
        root = Tk()
        root.withdraw()
        target_folder = filedialog.askdirectory(
            title="Select Folder With Video, Subtitles & Timestamps",
            initialdir=str(Path.home() / "Desktop")
        )
        root.destroy()

    if not target_folder:
        print("No folder selected.")
        return

    run_folder_system(target_folder)


if __name__ == "__main__":
    main()
