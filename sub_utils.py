#!/usr/bin/env python3
"""
sub_utils.py - Universal Subtitle & Lyric Format Handler

Supports:
  - SRT (.srt) - SubRip
  - ASS / SSA (.ass, .ssa) - Advanced SubStation Alpha
  - LRC (.lrc) - Lyrics format (with timestamps [mm:ss.xx])
  - VTT (.vtt) - WebVTT
"""

import os
import re
import tempfile
import subprocess
from pathlib import Path
from typing import Optional, List, Tuple

import pysrt


def parse_lrc_content(text: str) -> pysrt.SubRipFile:
    """Parses LRC lyrics text into a pysrt.SubRipFile object with computed durations."""
    # Pattern matches [mm:ss.xx] or [hh:mm:ss.xx]
    pattern = re.compile(r'\[(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?:[.,](\d{2,3}))?\](.*)')
    entries = []

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("[ti:", "[ar:", "[al:", "[by:", "[offset:")):
            continue

        match = pattern.match(line)
        if match:
            h = int(match.group(1)) if match.group(1) else 0
            m = int(match.group(2))
            s = int(match.group(3))
            ms_raw = match.group(4) or "0"
            ms = int(ms_raw) * 10 if len(ms_raw) == 2 else int(ms_raw)

            start_sec = h * 3600 + m * 60 + s + (ms / 1000.0)
            text_cue = match.group(5).strip()
            if text_cue:
                entries.append((start_sec, text_cue))

    entries.sort(key=lambda x: x[0])

    subs = pysrt.SubRipFile()
    for i, (t_start, text_cue) in enumerate(entries):
        # Determine end time: up to next line, or +4.0s for the last line
        if i + 1 < len(entries):
            next_start = entries[i + 1][0]
            t_end = min(next_start, t_start + 6.0)
            # If next line starts immediately, give at least 0.2s gap
            if t_end <= t_start:
                t_end = t_start + 1.0
        else:
            t_end = t_start + 4.0

        item = pysrt.SubRipItem(
            index=i + 1,
            start=pysrt.SubRipTime(milliseconds=int(round(t_start * 1000))),
            end=pysrt.SubRipTime(milliseconds=int(round(t_end * 1000))),
            text=text_cue
        )
        subs.append(item)

    return subs


def load_any_subtitles(file_path: str) -> pysrt.SubRipFile:
    """
    Loads subtitles or lyrics from .srt, .ass, .ssa, .lrc, or .vtt into pysrt.SubRipFile.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Subtitle file '{file_path}' does not exist.")

    ext = path.suffix.lower()

    # 1. Handle LRC format
    if ext == ".lrc":
        for enc in ["utf-8", "utf-8-sig", "latin-1", "cp1252"]:
            try:
                with open(file_path, "r", encoding=enc) as f:
                    content = f.read()
                return parse_lrc_content(content)
            except UnicodeDecodeError:
                continue
        raise ValueError(f"Could not decode LRC file '{file_path}'")

    # 2. Handle ASS / SSA format
    elif ext in [".ass", ".ssa", ".vtt"]:
        # Convert via ffmpeg to temporary srt
        with tempfile.NamedTemporaryFile(suffix=".srt", delete=False) as tmp:
            tmp_srt = tmp.name

        try:
            cmd = ["ffmpeg", "-y", "-i", file_path, tmp_srt]
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0 and os.path.exists(tmp_srt):
                subs = pysrt.open(tmp_srt, encoding="utf-8")
                return subs
            else:
                raise RuntimeError(f"FFmpeg failed converting {ext} file: {res.stderr}")
        finally:
            if os.path.exists(tmp_srt):
                os.remove(tmp_srt)

    # 3. Handle SRT format
    else:
        for enc in ["utf-8", "utf-8-sig", "latin-1", "cp1252"]:
            try:
                return pysrt.open(file_path, encoding=enc)
            except Exception:
                continue
        raise ValueError(f"Could not load SRT file '{file_path}' with common encodings.")


def prepare_shifted_subtitles(
    sub_path: str,
    output_srt_path: str,
    start_sec: float = 0.0,
    end_sec: Optional[float] = None
) -> bool:
    """
    Reads .srt, .ass, or .lrc, shifts timestamps relative to 00:00:00,
    filters cues within [start_sec, end_sec], and saves as clean .srt.
    Returns True if cues exist, False if empty.
    """
    subs = load_any_subtitles(sub_path)
    start_ms = int(round(start_sec * 1000))
    end_ms = int(round(end_sec * 1000)) if end_sec is not None else None

    shifted = pysrt.SubRipFile()
    index = 1

    for item in subs:
        item_start_ms = item.start.ordinal
        item_end_ms = item.end.ordinal

        if end_ms is not None and (item_end_ms <= start_ms or item_start_ms >= end_ms):
            continue
        elif end_ms is None and item_end_ms <= start_ms:
            continue

        rel_start_ms = max(0, item_start_ms - start_ms)
        if end_ms is not None:
            rel_end_ms = min(end_ms - start_ms, item_end_ms - start_ms)
        else:
            rel_end_ms = item_end_ms - start_ms

        if rel_end_ms <= rel_start_ms:
            continue

        item.start = pysrt.SubRipTime(milliseconds=rel_start_ms)
        item.end = pysrt.SubRipTime(milliseconds=rel_end_ms)
        item.index = index
        index += 1
        shifted.append(item)

    if len(shifted) == 0:
        shifted.save(output_srt_path, encoding="utf-8")
        return False

    shifted.save(output_srt_path, encoding="utf-8")
    return True


def timestamp_to_seconds(ts: str) -> float:
    """Converts HH:MM:SS.mmm, HH:MM:SS, MM:SS, or SS to float seconds."""
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


def sanitize_filename(name: str) -> str:
    """Sanitizes title for safe file naming while preserving Hindi Unicode characters."""
    s = re.sub(r'\[cite:\s*\d+\]', '', name, flags=re.IGNORECASE)
    s = re.sub(r'\[\d+\]', '', s)
    s = re.sub(r'[:|]+', ' - ', s)
    # Remove quotes (single, double, backtick, curly), brackets, and invalid filesystem characters
    s = re.sub(r"""['"`‘’“”\\/*?<>|\[\]{}\x00-\x1f]""", '', s)
    s = re.sub(r'\s+', ' ', s).strip('. -')
    return s if s else "untitled_clip"


def auto_fix_timestamp_text(text: str) -> Tuple[str, int]:
    """
    Detects and fixes common LLM timestamp hallucination errors in raw text.
    For example, when an LLM outputs '01:01:30 - 02:02:30' thinking it is a 1-minute clip,
    it mistakenly incremented both the hour and minute digits, creating a 3660s (1 hour) clip.
    Rewrites erroneous end timestamps to match the start hour.
    """
    ts_pattern = re.compile(
        r'(?P<start>(?:\d{1,2}:)?\d{1,2}:\d{2}(?:[.,]\d+)?)\s*(?P<dash>[-–—])\s*(?P<end>(?:\d{1,2}:)?\d{1,2}:\d{2}(?:[.,]\d+)?)'
    )
    lines = text.splitlines()
    fixed_lines = []
    fix_count = 0

    for line in lines:
        m = ts_pattern.search(line)
        if not m:
            fixed_lines.append(line)
            continue
        start_raw = m.group('start')
        end_raw = m.group('end')
        dash = m.group('dash')
        s_parts = start_raw.split(':')
        e_parts = end_raw.split(':')

        if len(s_parts) == 3 and len(e_parts) == 3:
            try:
                s_sec = timestamp_to_seconds(start_raw)
                e_sec = timestamp_to_seconds(end_raw)
                dur = e_sec - s_sec
                h_diff = int(e_parts[0]) - int(s_parts[0])
                m_diff = abs(int(e_parts[1]) - int(s_parts[1]))
                if dur >= 3500 and h_diff > 0 and m_diff <= 3:
                    cand_dur = (e_sec - h_diff * 3600) - s_sec
                    if 10 <= cand_dur <= 180:
                        new_end = f"{s_parts[0]}:{e_parts[1]}:{e_parts[2]}"
                        line = line[:m.start()] + f"{start_raw} {dash} {new_end}" + line[m.end():]
                        fix_count += 1
            except Exception:
                pass
        fixed_lines.append(line)

    return "\n".join(fixed_lines), fix_count


def parse_timestamp_entries(text: str) -> List[dict]:
    """
    Parses timestamp segments and titles from raw text, markdown bullets, or lists.
    Supports:
      * `00:00:00 - 00:00:21` — Title
      - `00:00:00 - 00:00:21` — Title[cite: 1]
      00:00:00 - 00:00:21 — Title
      00:00:00 - 00:00:21 : Title
    Auto-corrects accidental LLM hour-jump hallucinations (e.g. 01:01:30 - 02:02:30).
    Preserves all legitimate short (<3 min) and long (>3 min) videos.
    """
    ts_pattern = re.compile(
        r'(?P<start>(?:\d{1,2}:)?\d{1,2}:\d{2}(?:[.,]\d+)?)\s*[-–—]\s*(?P<end>(?:\d{1,2}:)?\d{1,2}:\d{2}(?:[.,]\d+)?)'
    )
    entries = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # Strip leading bullet/list markers: -, *, +, numbers like 1., 1)
        line = re.sub(r'^(?:[-*+•]|\d+[.)])\s*', '', line)
        m = ts_pattern.search(line)
        if not m:
            continue
        start_raw = m.group('start')
        end_raw = m.group('end')
        rest = line[m.end():].strip()
        # Clean leading separator after timestamps
        title_raw = re.sub(r'^[\s\-–—:|`]+', '', rest)
        title_raw = re.sub(r'[\s`]+$', '', title_raw)
        # Strip AI citations like [cite: 1] or [1]
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

            clean_title = sanitize_filename(title_raw)
            entries.append({
                "start_str": start_raw,
                "end_str": end_raw,
                "start_sec": start_sec,
                "end_sec": end_sec,
                "duration": duration,
                "title": title_raw,
                "clean_title": clean_title
            })
        except Exception:
            continue
    return entries
