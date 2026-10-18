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
    ratio: str = "9:16",
    volume: float = 0.12,
    scale: float = 1.0,
    pos_x: float = 0.5,
    pos_y: float = 0.5,
    bottom_fade: bool = True,
    pad_top: float = 0.0,
    pad_bottom: float = 0.0
):
    folder = Path(folder_path_str).resolve()
    if not folder.exists() or not folder.is_dir():
        print(f"Error: Folder '{folder}' does not exist.", file=sys.stderr)
        return

    print("\n" + "=" * 65)
    print(f"📂 1-CLICK FOLDER AUTO-RUN: {folder.name}")
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
    in_aspect = (in_w / in_h) if in_h else (16.0 / 9.0)

    shorts_crop_filter, crop_w, crop_h, _, _ = compute_crop_filter(
        in_w=in_w,
        in_h=in_h,
        ratio=ratio,
        pos_x=pos_x,
        pos_y=pos_y,
        scale=scale,
        pad_top=pad_top,
        pad_bottom=pad_bottom
    )
    if abs(in_aspect - (16.0 / 9.0)) < 0.02:
        yt_crop_filter = None
    else:
        yt_crop_filter, _, _, _, _ = compute_crop_filter(in_w=in_w, in_h=in_h, ratio="16:9", pos_x=0.5, pos_y=0.5, scale=1.0)

    print(f"📐 Shorts Crop (≤3m): {ratio} ({crop_w}x{crop_h}) [Scale: {scale:.2f}x, Pos: ({pos_x:.2f}, {pos_y:.2f}), Pad: (T:{pad_top:.1f}%, B:{pad_bottom:.1f}%)]")
    print(f"📐 Long Video (>3m) : 16:9 Original (Unscaled YouTube format)")
    print(f"🖤 Bottom Black Fade: {'Enabled' if bottom_fade else 'Disabled'}")
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
        chosen_crop = yt_crop_filter if is_long else shorts_crop_filter
        crop_tag = "16:9 Original (Unscaled)" if is_long else f"{ratio} Shorts"

        print(f"[{i:02d}/{total:02d}] ⚡ Clipping [{crop_tag}]: {entry['title']} ({dur:.1f}s)...")

        t0 = time.time()
        run_pipeline(
            video_path=str(video_path),
            output_path=str(out_vid),
            crop_filter=chosen_crop,
            srt_path=str(sub_path) if sub_path else None,
            music_path=str(audio_path) if audio_path else None,
            music_volume=volume,
            start_sec=entry["start_sec"],
            duration_sec=dur,
            use_hw_accel=True,
            bottom_fade=bottom_fade and not is_long
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
