#!/usr/bin/env python3
"""
studio_app.py - Native Offline macOS Desktop App for Video Clipping & Cropping

Features:
  - 100% Offline native desktop GUI (No web browser, no servers, no ports)
  - Native macOS Finder file selection for Video, SRT, and Background Music
  - Interactive Aspect Ratio selection (9:16 Shorts/Reels, 1:1 Square, 4:5, 16:9)
  - Real-time Visual Framing Preview with live crop rectangle over actual video frame
  - Audio ducking slider (keeps voice clear, background music at ~10-15%)
  - 1-Minute Hardware-Accelerated Preview (automatically opens in native Mac player)
  - Instant CLI command generator & background runner for whole video (even 10+ hours)
"""

import os
import sys
import time
import json
import shutil
import queue
import threading
import subprocess
from pathlib import Path

for extra_path in ["/opt/homebrew/bin", "/usr/local/bin"]:
    if extra_path not in os.environ.get("PATH", "") and os.path.exists(extra_path):
        os.environ["PATH"] = extra_path + ":" + os.environ.get("PATH", "")
from typing import Optional, Dict, Any, Tuple
from tkinter import filedialog, messagebox

import copy
import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFont

# Import core multimedia processor & utilities
from process_full import (
    probe_video, probe_audio, compute_crop_filter, run_pipeline, get_available_encoders,
    generate_3segment_preview, combine_video_audio, CAPTION_STYLES
)
from sub_utils import (
    parse_timestamp_entries, sanitize_filename, auto_fix_timestamp_text,
    reflow_subtitles, generate_speech_timed_srt, load_any_subtitles,
    find_matching_subtitles, clean_and_deoverlap_subtitles
)
from clip_tester import extract_thumbnail
from config_manager import load_saved_config, save_saved_config
from yt_downloader import (
    YouTubeDownloader, DOWNLOADS_DIR, AUDIO_DOWNLOADS_DIR,
    cleanup_expired_youtube_videos, DEFAULT_EXPIRY_DAYS
)

# Set appearance and theme
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

BASE_DIR = Path(__file__).resolve().parent
PREVIEW_DIR = BASE_DIR / "offline_previews"
PREVIEW_DIR.mkdir(parents=True, exist_ok=True)

AVAILABLE_FONTS = [
    "Kohinoor Devanagari",
    "Arial",
    "Helvetica Neue",
    "Impact",
    "Avenir Next",
    "Futura",
    "Trebuchet MS",
    "Verdana",
    "Georgia",
    "Devanagari MT",
]

BORDER_OPTIONS = [
    "Auto (Preset Default)",
    "No Border (Clean Text)",
    "Small Border (Thin 1.0px)",
    "Standard Border (Medium 2.0px)",
    "Bold Border (Thick 3.2px)",
    "Heavy Outline (Extra Thick 4.5px)",
    "Opaque Box / Pill",
]

FONT_SIZE_OPTIONS = [
    "14pt (Tiny)",
    "16pt (Small)",
    "18pt (Compact)",
    "20pt (Standard)",
    "22pt (Medium)",
    "24pt (Medium+)",
    "26pt (Large)",
    "28pt (Large+)",
    "30pt (Extra Large)",
    "34pt (Huge)",
    "38pt (Mega)",
    "42pt (Jumbo)",
]

FONT_FILE_MAP = {
    "Kohinoor Devanagari": ["/System/Library/Fonts/Kohinoor.ttc", "/System/Library/Fonts/KohinoorTelugu.ttc"],
    "Arial": ["/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/ArialHB.ttc"],
    "Helvetica Neue": ["/System/Library/Fonts/HelveticaNeue.ttc", "/System/Library/Fonts/Helvetica.ttc"],
    "Impact": ["/System/Library/Fonts/Supplemental/Impact.ttf"],
    "Avenir Next": ["/System/Library/Fonts/Avenir Next Condensed.ttc", "/System/Library/Fonts/Avenir.ttc"],
    "Futura": ["/System/Library/Fonts/Supplemental/Futura.ttc"],
    "Trebuchet MS": ["/System/Library/Fonts/Supplemental/Trebuchet MS.ttf"],
    "Verdana": ["/System/Library/Fonts/Supplemental/Verdana.ttf"],
    "Georgia": ["/System/Library/Fonts/Supplemental/Georgia.ttf"],
    "Devanagari MT": ["/System/Library/Fonts/Supplemental/Devanagari Sangam MN.ttc", "/System/Library/Fonts/Supplemental/ITFDevanagari.ttc"],
}


def get_pillow_font(font_name: str, size: int) -> ImageFont.FreeTypeFont:
    paths = FONT_FILE_MAP.get(font_name, []) + [
        "/System/Library/Fonts/Kohinoor.ttc",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default()
    except Exception:
        return None


class StudioClipperApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Studio Video Clipper & Hardware Cropper (Offline Mac App)")
        self.geometry("1160x780")
        self.minsize(1050, 700)

        # State variables
        self.video_path: Optional[str] = None
        self.audio_path: Optional[str] = None
        self.srt_path: Optional[str] = None
        self.music_path: Optional[str] = None
        self.video_info: Dict[str, Any] = {}
        self.base_frame_image: Optional[Image.Image] = None
        self.raw_frame_path = str(BASE_DIR / ".temp_raw_frame.jpg")

        # YouTube Downloader state
        self.yt_downloader: Optional[YouTubeDownloader] = None
        self.is_yt_downloading: bool = False
        self.yt_msg_queue: queue.Queue = queue.Queue()

        # Load Persistent Configuration
        saved_cfg = load_saved_config()
        self.shorts_config = saved_cfg["shorts_config"]
        self.videos_config = saved_cfg["videos_config"]
        self.active_mode = saved_cfg.get("active_mode", "shorts")

        # YouTube Integration State (Revealed inside Shorts & Videos when toggle is ON)
        yt_cfg = saved_cfg.get("youtube_config", {})
        self.yt_enabled: bool = yt_cfg.get("enabled", False)
        self.yt_auto_captions_var = ctk.BooleanVar(value=yt_cfg.get("auto_captions", True))
        self.yt_noise_removal_var = ctk.BooleanVar(value=yt_cfg.get("noise_removal", True))
        self.yt_noise_intensity: str = yt_cfg.get("noise_intensity", "Medium")
        self.yt_auto_delete_days: float = float(yt_cfg.get("auto_delete_after_days", DEFAULT_EXPIRY_DAYS))

        self.yt_downloader: Optional[YouTubeDownloader] = None
        self.is_yt_downloading: bool = False
        self.is_yt_sub_downloading: bool = False
        self.is_yt_audio_downloading: bool = False
        self.yt_msg_queue: queue.Queue = queue.Queue()
        self.yt_audio_player_proc: Optional[subprocess.Popen] = None
        self.yt_is_playing_audio: bool = False

        # Start background cleanup of YouTube loaded videos older than 2 days on launch
        def _startup_yt_cleanup():
            try:
                cleanup_expired_youtube_videos(max_age_days=self.yt_auto_delete_days, verbose=False)
            except Exception:
                pass
        threading.Thread(target=_startup_yt_cleanup, daemon=True).start()

        # Schedule recurring cleanup check (every 1 hour)
        self.after(3600000, self._periodic_yt_cleanup)

        # Active mode profile (Sections 1 & 2)
        active_cfg = self.shorts_config if self.active_mode == "shorts" else self.videos_config
        self.current_ratio = active_cfg["ratio_val"]
        self.current_scale = active_cfg["scale"]
        self.current_pos_x = active_cfg["pos_x"]
        self.current_pos_y = active_cfg["pos_y"]
        self.current_pad_top = active_cfg["pad_top"]
        self.current_pad_bottom = active_cfg["pad_bottom"]
        self.bottom_fade_var = ctk.BooleanVar(value=active_cfg["bottom_fade"])
        self.fade_height = active_cfg.get("fade_height", 0.38)
        self.fade_opacity = active_cfg.get("fade_opacity", 0.92)

        # Audio track pairing & Main Video Volume configuration (Section 1)
        audio_cfg = saved_cfg.get("audio", {})
        self.saved_audio_enabled = audio_cfg.get("enabled", True)
        self.saved_audio_path = audio_cfg.get("path", "")
        self.current_video_vol = float(audio_cfg.get("video_volume", 1.0))

        # Universal Sections 3 (Subtitles) & 4 (Music & Volume)
        sub_cfg = saved_cfg.get("subtitles", {})
        self.saved_caption_style = sub_cfg.get("style", "✨ Soothe White Light (Soft Clean Glow)")
        self.saved_font_name = sub_cfg.get("font", "Kohinoor Devanagari")
        self.saved_font_size = sub_cfg.get("size", "Medium (22pt)")
        self.saved_caption_border = sub_cfg.get("border", "Auto (Preset Default)")
        self.saved_caption_pos = sub_cfg.get("position", "Caption: On Video Fade (Above Padding)")
        default_pos_y = 0.06 if "Inside Black" in self.saved_caption_pos else 0.15
        self.sub_pos_y = float(sub_cfg.get("pos_y", default_pos_y))
        self.saved_caption_overflow = bool(sub_cfg.get("overflow", False))
        self.caption_overflow_var = ctk.BooleanVar(value=self.saved_caption_overflow)
        self.saved_sub_enabled = sub_cfg.get("enabled", True)
        self.saved_srt_path = sub_cfg.get("path", "")

        music_cfg = saved_cfg.get("music", {})
        self.saved_music_enabled = music_cfg.get("enabled", True)
        self.saved_music_path = music_cfg.get("path", "")
        self.current_vol = music_cfg.get("volume", active_cfg.get("vol", 0.12))

        self.saved_skip_existing = saved_cfg.get("skip_existing", True)

        self.preview_mode = "reel"
        self.is_rendering = False
        self.is_cancelled = False
        self.current_proc = None
        self.skip_existing_var = ctk.BooleanVar(value=self.saved_skip_existing)

        self.encoders = get_available_encoders()
        self.hw_label = "Apple Silicon VideoToolbox (Hardware Acceleration)" if self.encoders.get("videotoolbox") else "CPU (libx264)"

        self._build_ui()

        # Section 1: Restore saved video for active mode if it exists
        saved_vid = active_cfg.get("video_path", "")
        if saved_vid and os.path.exists(saved_vid):
            self._load_video(saved_vid)
        else:
            default_video = Path.home() / "Desktop" / "CONCENTRATION AND TRATAK_480p.mp4"
            if default_video.exists():
                self._load_video(str(default_video))

        # Restore paired audio, universal subtitle & music paths if valid
        if hasattr(self, "saved_audio_path") and self.saved_audio_path and os.path.exists(self.saved_audio_path):
            self.audio_path = self.saved_audio_path
            self.audio_entry.delete(0, "end")
            self.audio_entry.insert(0, self.saved_audio_path)
            self._update_audio_info(self.saved_audio_path)

        if hasattr(self, "saved_srt_path") and self.saved_srt_path and os.path.exists(self.saved_srt_path):
            self.srt_path = self.saved_srt_path
            self.srt_entry.delete(0, "end")
            self.srt_entry.insert(0, self.saved_srt_path)

        if hasattr(self, "saved_music_path") and self.saved_music_path and os.path.exists(self.saved_music_path):
            self.music_path = self.saved_music_path
            self.music_entry.delete(0, "end")
            self.music_entry.insert(0, self.saved_music_path)

    def _build_ui(self):
        # Configure layout (2 columns: left controls column minsize=460, right preview column expand)
        self.grid_columnconfigure(0, weight=0, minsize=460)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # ==============================================================
        # LEFT COLUMN: CONTROLS & FILE PICKERS (Scrollable to Bottom)
        # ==============================================================
        self.left_frame = ctk.CTkScrollableFrame(self, width=460, corner_radius=10)
        self.left_frame.grid(row=0, column=0, padx=(16, 8), pady=16, sticky="nsew")

        # Ensure smooth mousewheel/trackpad scrolling throughout left_frame even over sliders & controls
        def _custom_check_scroll(widget):
            if widget == self.left_frame._parent_canvas:
                return True
            if isinstance(widget, (ctk.CTkScrollbar, ctk.CTkTextbox)):
                return False
            if isinstance(widget, ctk.CTkScrollableFrame):
                return widget._parent_canvas == self.left_frame._parent_canvas
            if widget.master is not None:
                return _custom_check_scroll(widget.master)
            return False

        self.left_frame._check_if_valid_scroll = _custom_check_scroll

        # -------------------------------------------------------------
        # SHORTS vs VIDEOS MODE SWITCHER CARD + 1-CLICK & YOUTUBE TOGGLE
        # -------------------------------------------------------------
        mode_card = ctk.CTkFrame(
            self.left_frame,
            fg_color="#182030",
            border_width=1,
            border_color="#334155",
            corner_radius=18
        )
        mode_card.pack(fill="x", pady=(0, 14), padx=2)

        pill_box = ctk.CTkFrame(mode_card, fg_color="#0f172a", corner_radius=16)
        pill_box.pack(side="left", padx=(6, 4), pady=4)

        self.btn_mode_shorts = ctk.CTkButton(
            pill_box,
            text="Shorts",
            width=82,
            height=30,
            corner_radius=14,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#cbd5e1",
            text_color="#0f172a",
            hover_color="#e2e8f0",
            command=lambda: self._switch_mode("shorts")
        )
        self.btn_mode_shorts.pack(side="left", padx=2, pady=2)

        self.btn_mode_videos = ctk.CTkButton(
            pill_box,
            text="Videos",
            width=82,
            height=30,
            corner_radius=14,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="transparent",
            text_color="#94a3b8",
            hover_color="#242c3d",
            command=lambda: self._switch_mode("videos")
        )
        self.btn_mode_videos.pack(side="left", padx=2, pady=2)

        # 1-Click Folder Button
        self.btn_1click_folder = ctk.CTkButton(
            mode_card,
            text="⚡ 1-Click",
            width=80,
            height=30,
            corner_radius=15,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#2563eb",
            text_color="#ffffff",
            hover_color="#1d4ed8",
            command=self._choose_folder_and_run
        )
        self.btn_1click_folder.pack(side="left", padx=(4, 4), pady=4)
        self.btn_auto_badge = self.btn_1click_folder

        # YouTube ON/OFF Toggle Button (OFF by default, prominent red border)
        self.btn_youtube_toggle = ctk.CTkButton(
            mode_card,
            text="▶ YouTube ON" if self.yt_enabled else "▶ YouTube OFF",
            width=114,
            height=30,
            corner_radius=15,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#dc2626" if self.yt_enabled else "#1e293b",
            text_color="#ffffff" if self.yt_enabled else "#f87171",
            hover_color="#b91c1c" if self.yt_enabled else "#334155",
            border_width=1,
            border_color="#ef4444" if self.yt_enabled else "#dc2626",
            command=self._toggle_youtube_mode
        )
        self.btn_youtube_toggle.pack(side="left", padx=(4, 6), pady=4)

        # Standard Clipper Container (Holds Sections 1 to 5 for Shorts & Videos)
        self.clipper_container = ctk.CTkFrame(self.left_frame, fg_color="transparent")
        self.clipper_container.pack(fill="x", expand=True)

        # Build YouTube Tools Card inside clipper_container (revealed when YouTube is ON)
        self._build_yt_tools_card()

        # 1. Source Video Selection
        self.sec1_label = self._create_section_label(self.clipper_container, "1. Source Video (.mp4, .mov, .mkv)")
        if self.yt_enabled:
            self.yt_tools_card.pack(fill="x", pady=(0, 14), before=self.sec1_label)
        
        vid_box = ctk.CTkFrame(self.clipper_container, fg_color="transparent")
        vid_box.pack(fill="x", pady=(0, 6))

        self.video_entry = ctk.CTkEntry(vid_box, placeholder_text="Select a video file or paste path...")
        self.video_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.video_entry.bind("<Return>", lambda e: self._on_video_entry_submitted())
        self.video_entry.bind("<FocusOut>", lambda e: self._on_video_entry_submitted())

        self.btn_paste_vid = ctk.CTkButton(
            vid_box,
            text="📋 Paste",
            width=64,
            fg_color="#334155",
            hover_color="#475569",
            command=self._paste_and_load_video
        )
        self.btn_paste_vid.pack(side="left", padx=(0, 6))

        self.btn_browse_vid = ctk.CTkButton(vid_box, text="📁 Choose File", width=105, command=self._browse_video)
        self.btn_browse_vid.pack(side="right")

        self.meta_label = ctk.CTkLabel(
            self.clipper_container,
            text="No video loaded yet",
            text_color="gray",
            font=ctk.CTkFont(size=12)
        )
        self.meta_label.pack(anchor="w", pady=(0, 6))

        # --- Main Video Sound / Volume Boost (Section 1) ---
        self.vid_vol_frame = ctk.CTkFrame(self.clipper_container, fg_color="#181c27", corner_radius=8)
        self.vid_vol_frame.pack(fill="x", pady=(0, 10), padx=2)

        vid_vol_header = ctk.CTkFrame(self.vid_vol_frame, fg_color="transparent")
        vid_vol_header.pack(fill="x", padx=10, pady=(8, 4))

        vol_pct = int(round(self.current_video_vol * 100))
        if self.current_video_vol == 0:
            vol_text = "🔇 Main Video Sound: Muted (0%)"
        elif abs(self.current_video_vol - 1.0) < 0.01:
            vol_text = "🔊 Main Video Sound: 100% (Normal)"
        elif self.current_video_vol > 1.0:
            vol_text = f"🔊 Main Video Sound: {vol_pct}% ({self.current_video_vol:.2f}x Boost 🚀)"
        else:
            vol_text = f"🔉 Main Video Sound: {vol_pct}% ({self.current_video_vol:.2f}x)"

        self.video_vol_label_var = ctk.StringVar(value=vol_text)
        self.video_vol_label = ctk.CTkLabel(
            vid_vol_header,
            textvariable=self.video_vol_label_var,
            font=ctk.CTkFont(size=12, weight="bold")
        )
        self.video_vol_label.pack(side="left")

        # Preset buttons (Reset 100%, 150%, 200%)
        btn_preset_box = ctk.CTkFrame(vid_vol_header, fg_color="transparent")
        btn_preset_box.pack(side="right")

        self.btn_vvol_200 = ctk.CTkButton(
            btn_preset_box,
            text="200%",
            width=46,
            height=22,
            font=ctk.CTkFont(size=11),
            fg_color="#2b3245",
            hover_color="#3b445c",
            command=lambda: self._set_video_vol(2.0)
        )
        self.btn_vvol_200.pack(side="right", padx=(4, 0))

        self.btn_vvol_150 = ctk.CTkButton(
            btn_preset_box,
            text="150%",
            width=46,
            height=22,
            font=ctk.CTkFont(size=11),
            fg_color="#2b3245",
            hover_color="#3b445c",
            command=lambda: self._set_video_vol(1.5)
        )
        self.btn_vvol_150.pack(side="right", padx=(4, 0))

        self.btn_reset_vvol = ctk.CTkButton(
            btn_preset_box,
            text="Reset 100%",
            width=70,
            height=22,
            font=ctk.CTkFont(size=11),
            fg_color="#374151",
            hover_color="#4b5563",
            command=lambda: self._set_video_vol(1.0)
        )
        self.btn_reset_vvol.pack(side="right")

        # Slider row with - / + nudge buttons
        vid_vol_ctrl = ctk.CTkFrame(self.vid_vol_frame, fg_color="transparent")
        vid_vol_ctrl.pack(fill="x", padx=10, pady=(2, 6))

        self.btn_vvol_minus = ctk.CTkButton(
            vid_vol_ctrl,
            text="-",
            width=32,
            height=28,
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color="#2b3245",
            hover_color="#3b445c",
            corner_radius=6
        )
        self.btn_vvol_minus.pack(side="left", padx=(0, 6))

        self.video_vol_slider = ctk.CTkSlider(
            vid_vol_ctrl,
            from_=0.0,
            to=3.0,
            number_of_steps=60,
            command=self._on_video_vol_slider_changed
        )
        self.video_vol_slider.set(self.current_video_vol)
        self.video_vol_slider.pack(side="left", fill="x", expand=True)

        self.btn_vvol_plus = ctk.CTkButton(
            vid_vol_ctrl,
            text="+",
            width=32,
            height=28,
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color="#2b3245",
            hover_color="#3b445c",
            corner_radius=6
        )
        self.btn_vvol_plus.pack(side="right", padx=(6, 0))

        self._attach_button_repeat(self.btn_vvol_minus, self._video_vol_step_minus)
        self._attach_button_repeat(self.btn_vvol_plus, self._video_vol_step_plus)
        self._disable_slider_scroll(self.video_vol_slider, self.left_frame)

        # Helper hint
        self.video_vol_hint = ctk.CTkLabel(
            self.vid_vol_frame,
            text="💡 Adjust to boost quiet speech/dialogue (e.g. 150% = 1.5x louder, 200% = 2x boost)",
            font=ctk.CTkFont(size=11),
            text_color="#94a3b8"
        )
        self.video_vol_hint.pack(anchor="w", padx=10, pady=(0, 8))

        # Audio Pairing controls (for videos downloaded without high-quality audio)
        audio_sub_frame = ctk.CTkFrame(self.clipper_container, fg_color="#181c27", corner_radius=8)
        audio_sub_frame.pack(fill="x", pady=(0, 14), padx=2)

        audio_header_box = ctk.CTkFrame(audio_sub_frame, fg_color="transparent")
        audio_header_box.pack(fill="x", padx=10, pady=(8, 4))

        self.audio_switch_var = ctk.BooleanVar(value=self.saved_audio_enabled)
        self.audio_switch = ctk.CTkSwitch(
            audio_header_box,
            text="🎵 Combine High-Quality Audio (.m4a, .mp3, .wav)",
            font=ctk.CTkFont(size=12, weight="bold"),
            variable=self.audio_switch_var,
            command=self._on_audio_switch_changed
        )
        self.audio_switch.pack(side="left")

        audio_box = ctk.CTkFrame(audio_sub_frame, fg_color="transparent")
        audio_box.pack(fill="x", padx=10, pady=(0, 6))

        self.audio_entry = ctk.CTkEntry(
            audio_box,
            placeholder_text="Select audio track to replace/add to video..."
        )
        self.audio_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self.btn_browse_audio = ctk.CTkButton(
            audio_box,
            text="🎵 Choose Audio",
            width=110,
            command=self._browse_audio
        )
        self.btn_browse_audio.pack(side="right")

        audio_action_box = ctk.CTkFrame(audio_sub_frame, fg_color="transparent")
        audio_action_box.pack(fill="x", padx=10, pady=(0, 8))

        self.audio_meta_label = ctk.CTkLabel(
            audio_action_box,
            text="No audio file selected (video audio will be used)",
            text_color="#94a3b8",
            font=ctk.CTkFont(size=11)
        )
        self.audio_meta_label.pack(side="left", anchor="w")

        self.btn_quick_combine = ctk.CTkButton(
            audio_action_box,
            text="⚡ Combine Now",
            width=110,
            height=26,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#3b82f6",
            hover_color="#2563eb",
            command=self._quick_combine_audio_video
        )
        self.btn_quick_combine.pack(side="right")

        # 2. Aspect Ratio & Crop Positioning
        self.section_2_label = self._create_section_label(self.clipper_container, "2. Aspect Ratio & Crop Framing (📱 Shorts)")

        self.ratio_segmented = ctk.CTkSegmentedButton(
            self.clipper_container,
            values=["9:16 (Shorts/Reels)", "1:1 (Square)", "4:5 (Portrait)", "16:9 (Original)"],
            command=self._on_ratio_changed
        )
        self.ratio_segmented.set("9:16 (Shorts/Reels)")
        self.ratio_segmented.pack(fill="x", pady=(0, 6))

        self.ratio_info_label = ctk.CTkLabel(
            self.clipper_container,
            text="💡 Selected ratio & framing apply to Shorts (≤3m). Clips >3m automatically use Videos settings.",
            font=ctk.CTkFont(size=11),
            text_color="#9ca3af",
            wraplength=340,
            justify="left"
        )
        self.ratio_info_label.pack(anchor="w", pady=(0, 10))

        # --- A. Scale / Zoom Framing ---
        scale_header_frame = ctk.CTkFrame(self.clipper_container, fg_color="transparent")
        scale_header_frame.pack(fill="x", pady=(0, 2))

        self.scale_label_var = ctk.StringVar(value="Scale / Zoom: 1.00x (Standard Fill)")
        self.scale_label = ctk.CTkLabel(scale_header_frame, textvariable=self.scale_label_var, font=ctk.CTkFont(size=12, weight="bold"))
        self.scale_label.pack(side="left")

        self.btn_reset_scale = ctk.CTkButton(
            scale_header_frame,
            text="Reset 1.0x",
            width=70,
            height=22,
            font=ctk.CTkFont(size=11),
            fg_color="#374151",
            hover_color="#4b5563",
            command=self._reset_scale
        )
        self.btn_reset_scale.pack(side="right")

        scale_ctrl_frame = ctk.CTkFrame(self.clipper_container, fg_color="transparent")
        scale_ctrl_frame.pack(fill="x", pady=(0, 10))

        self.btn_scale_minus = ctk.CTkButton(
            scale_ctrl_frame,
            text="-",
            width=32,
            height=28,
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color="#2b3245",
            hover_color="#3b445c",
            corner_radius=6
        )
        self.btn_scale_minus.pack(side="left", padx=(0, 6))

        self.scale_slider = ctk.CTkSlider(
            scale_ctrl_frame,
            from_=0.5,
            to=3.0,
            number_of_steps=100,
            command=self._on_scale_slider_changed
        )
        self.scale_slider.set(1.0)
        self.scale_slider.pack(side="left", fill="x", expand=True)

        self.btn_scale_plus = ctk.CTkButton(
            scale_ctrl_frame,
            text="+",
            width=32,
            height=28,
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color="#2b3245",
            hover_color="#3b445c",
            corner_radius=6
        )
        self.btn_scale_plus.pack(side="right", padx=(6, 0))

        self._attach_button_repeat(self.btn_scale_minus, self._scale_step_minus)
        self._attach_button_repeat(self.btn_scale_plus, self._scale_step_plus)
        self._disable_slider_scroll(self.scale_slider, self.left_frame)

        # --- B. Horizontal Framing (X) ---
        pos_header_frame = ctk.CTkFrame(self.clipper_container, fg_color="transparent")
        pos_header_frame.pack(fill="x", pady=(0, 2))

        self.pos_label_var = ctk.StringVar(value="Horizontal Framing (X): Center (50%)")
        self.pos_label = ctk.CTkLabel(pos_header_frame, textvariable=self.pos_label_var, font=ctk.CTkFont(size=12))
        self.pos_label.pack(side="left")

        self.btn_center_pos_x = ctk.CTkButton(
            pos_header_frame,
            text="Center",
            width=55,
            height=22,
            font=ctk.CTkFont(size=11),
            fg_color="#374151",
            hover_color="#4b5563",
            command=self._reset_pos_x
        )
        self.btn_center_pos_x.pack(side="right")

        pos_ctrl_frame = ctk.CTkFrame(self.clipper_container, fg_color="transparent")
        pos_ctrl_frame.pack(fill="x", pady=(0, 10))

        self.btn_pos_minus = ctk.CTkButton(
            pos_ctrl_frame,
            text="-",
            width=32,
            height=28,
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color="#2b3245",
            hover_color="#3b445c",
            corner_radius=6
        )
        self.btn_pos_minus.pack(side="left", padx=(0, 6))

        self.pos_slider = ctk.CTkSlider(
            pos_ctrl_frame,
            from_=0.0,
            to=1.0,
            number_of_steps=100,
            command=self._on_pos_slider_changed
        )
        self.pos_slider.set(0.5)
        self.pos_slider.pack(side="left", fill="x", expand=True)

        self.btn_pos_plus = ctk.CTkButton(
            pos_ctrl_frame,
            text="+",
            width=32,
            height=28,
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color="#2b3245",
            hover_color="#3b445c",
            corner_radius=6
        )
        self.btn_pos_plus.pack(side="right", padx=(6, 0))

        self._attach_button_repeat(self.btn_pos_minus, self._pos_step_minus)
        self._attach_button_repeat(self.btn_pos_plus, self._pos_step_plus)
        self._disable_slider_scroll(self.pos_slider, self.left_frame)

        # --- C. Vertical Framing (Y) ---
        pos_y_header_frame = ctk.CTkFrame(self.clipper_container, fg_color="transparent")
        pos_y_header_frame.pack(fill="x", pady=(0, 2))

        self.pos_y_label_var = ctk.StringVar(value="Vertical Framing (Y): Center (50%)")
        self.pos_y_label = ctk.CTkLabel(pos_y_header_frame, textvariable=self.pos_y_label_var, font=ctk.CTkFont(size=12))
        self.pos_y_label.pack(side="left")

        self.btn_center_pos_y = ctk.CTkButton(
            pos_y_header_frame,
            text="Center",
            width=55,
            height=22,
            font=ctk.CTkFont(size=11),
            fg_color="#374151",
            hover_color="#4b5563",
            command=self._reset_pos_y
        )
        self.btn_center_pos_y.pack(side="right")

        pos_y_ctrl_frame = ctk.CTkFrame(self.clipper_container, fg_color="transparent")
        pos_y_ctrl_frame.pack(fill="x", pady=(0, 10))

        self.btn_pos_y_minus = ctk.CTkButton(
            pos_y_ctrl_frame,
            text="-",
            width=32,
            height=28,
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color="#2b3245",
            hover_color="#3b445c",
            corner_radius=6
        )
        self.btn_pos_y_minus.pack(side="left", padx=(0, 6))

        self.pos_y_slider = ctk.CTkSlider(
            pos_y_ctrl_frame,
            from_=0.0,
            to=1.0,
            number_of_steps=100,
            command=self._on_pos_y_slider_changed
        )
        self.pos_y_slider.set(0.5)
        self.pos_y_slider.pack(side="left", fill="x", expand=True)

        self.btn_pos_y_plus = ctk.CTkButton(
            pos_y_ctrl_frame,
            text="+",
            width=32,
            height=28,
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color="#2b3245",
            hover_color="#3b445c",
            corner_radius=6
        )
        self.btn_pos_y_plus.pack(side="right", padx=(6, 0))

        self._attach_button_repeat(self.btn_pos_y_minus, self._pos_y_step_minus)
        self._attach_button_repeat(self.btn_pos_y_plus, self._pos_y_step_plus)
        self._disable_slider_scroll(self.pos_y_slider, self.left_frame)

        # --- D. Frame Padding (Shift Clip Up / Letterbox) ---
        pad_header_frame = ctk.CTkFrame(self.clipper_container, fg_color="transparent")
        pad_header_frame.pack(fill="x", pady=(0, 2))

        ctk.CTkLabel(
            pad_header_frame,
            text="Frame Padding (Shift Clip Up / Letterbox):",
            font=ctk.CTkFont(size=12, weight="bold")
        ).pack(side="left")

        # Quick Preset Buttons:
        pad_preset_frame = ctk.CTkFrame(self.clipper_container, fg_color="transparent")
        pad_preset_frame.pack(fill="x", pady=(2, 8))

        self.btn_preset_shifted = ctk.CTkButton(
            pad_preset_frame,
            text="🎬 Shift Up (8% / 14%)",
            height=26,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#4338ca",
            hover_color="#3730a3",
            command=self._preset_shifted_pad
        )
        self.btn_preset_shifted.pack(side="left", padx=(0, 4), expand=True, fill="x")

        self.btn_preset_centered = ctk.CTkButton(
            pad_preset_frame,
            text="↕️ Centered (10% / 10%)",
            height=26,
            font=ctk.CTkFont(size=11),
            fg_color="#374151",
            hover_color="#4b5563",
            command=self._preset_centered_pad
        )
        self.btn_preset_centered.pack(side="left", padx=2, expand=True, fill="x")

        self.btn_preset_zero = ctk.CTkButton(
            pad_preset_frame,
            text="Full (0%)",
            height=26,
            font=ctk.CTkFont(size=11),
            fg_color="#374151",
            hover_color="#4b5563",
            command=self._reset_padding
        )
        self.btn_preset_zero.pack(side="left", padx=(4, 0), expand=True, fill="x")

        # Top Padding Slider with - / + buttons
        pad_top_subhead = ctk.CTkFrame(self.clipper_container, fg_color="transparent")
        pad_top_subhead.pack(fill="x", pady=(0, 2))
        self.pad_top_label_var = ctk.StringVar(value="Top Padding: 0%")
        ctk.CTkLabel(pad_top_subhead, textvariable=self.pad_top_label_var, font=ctk.CTkFont(size=11)).pack(side="left")

        pad_top_ctrl = ctk.CTkFrame(self.clipper_container, fg_color="transparent")
        pad_top_ctrl.pack(fill="x", pady=(0, 8))

        self.btn_pad_top_minus = ctk.CTkButton(pad_top_ctrl, text="-", width=32, height=26, font=ctk.CTkFont(size=14, weight="bold"), fg_color="#2b3245", hover_color="#3b445c", corner_radius=6)
        self.btn_pad_top_minus.pack(side="left", padx=(0, 6))

        self.pad_top_slider = ctk.CTkSlider(pad_top_ctrl, from_=0.0, to=30.0, number_of_steps=60, command=self._on_pad_top_slider_changed)
        self.pad_top_slider.set(0.0)
        self.pad_top_slider.pack(side="left", fill="x", expand=True)

        self.btn_pad_top_plus = ctk.CTkButton(pad_top_ctrl, text="+", width=32, height=26, font=ctk.CTkFont(size=14, weight="bold"), fg_color="#2b3245", hover_color="#3b445c", corner_radius=6)
        self.btn_pad_top_plus.pack(side="right", padx=(6, 0))

        self._attach_button_repeat(self.btn_pad_top_minus, self._pad_top_step_minus)
        self._attach_button_repeat(self.btn_pad_top_plus, self._pad_top_step_plus)
        self._disable_slider_scroll(self.pad_top_slider, self.left_frame)

        # Bottom Padding Slider with - / + buttons
        pad_bottom_subhead = ctk.CTkFrame(self.clipper_container, fg_color="transparent")
        pad_bottom_subhead.pack(fill="x", pady=(0, 2))
        self.pad_bottom_label_var = ctk.StringVar(value="Bottom Padding: 0%")
        ctk.CTkLabel(pad_bottom_subhead, textvariable=self.pad_bottom_label_var, font=ctk.CTkFont(size=11)).pack(side="left")

        pad_bottom_ctrl = ctk.CTkFrame(self.clipper_container, fg_color="transparent")
        pad_bottom_ctrl.pack(fill="x", pady=(0, 10))

        self.btn_pad_bottom_minus = ctk.CTkButton(pad_bottom_ctrl, text="-", width=32, height=26, font=ctk.CTkFont(size=14, weight="bold"), fg_color="#2b3245", hover_color="#3b445c", corner_radius=6)
        self.btn_pad_bottom_minus.pack(side="left", padx=(0, 6))

        self.pad_bottom_slider = ctk.CTkSlider(pad_bottom_ctrl, from_=0.0, to=40.0, number_of_steps=80, command=self._on_pad_bottom_slider_changed)
        self.pad_bottom_slider.set(0.0)
        self.pad_bottom_slider.pack(side="left", fill="x", expand=True)

        self.btn_pad_bottom_plus = ctk.CTkButton(pad_bottom_ctrl, text="+", width=32, height=26, font=ctk.CTkFont(size=14, weight="bold"), fg_color="#2b3245", hover_color="#3b445c", corner_radius=6)
        self.btn_pad_bottom_plus.pack(side="right", padx=(6, 0))

        self._attach_button_repeat(self.btn_pad_bottom_minus, self._pad_bottom_step_minus)
        self._attach_button_repeat(self.btn_pad_bottom_plus, self._pad_bottom_step_plus)
        self._disable_slider_scroll(self.pad_bottom_slider, self.left_frame)

        # --- E. Bottom Black Fade (Caption Contrast) Switch & Controls ---
        fade_box = ctk.CTkFrame(self.clipper_container, fg_color="#182030", border_width=1, border_color="#374151", corner_radius=8)
        fade_box.pack(fill="x", pady=(0, 14))

        self.fade_switch = ctk.CTkSwitch(
            fade_box,
            text="🖤 Bottom Black Fade (Caption Contrast)",
            variable=self.bottom_fade_var,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#60a5fa",
            command=self._on_fade_switch_changed
        )
        self.fade_switch.pack(anchor="w", padx=10, pady=(8, 4))

        ctk.CTkLabel(
            fade_box,
            text="Adds smooth dark gradient from bottom below captions like Reels/Shorts for maximum text contrast.",
            font=ctk.CTkFont(size=11),
            text_color="#94a3b8",
            wraplength=320,
            justify="left"
        ).pack(anchor="w", padx=10, pady=(0, 6))

        # Collapsible controls frame for adjusting fade
        self.fade_ctrl_frame = ctk.CTkFrame(fade_box, fg_color="transparent")
        if self.bottom_fade_var.get():
            self.fade_ctrl_frame.pack(fill="x", padx=10, pady=(0, 8))

        # 1. Fade Spread / Height Slider
        self.fade_height_label_var = ctk.StringVar(value=f"Fade Spread: {int(round(self.fade_height * 100))}% (Coverage)")
        ctk.CTkLabel(self.fade_ctrl_frame, textvariable=self.fade_height_label_var, font=ctk.CTkFont(size=11)).pack(anchor="w", pady=(2, 1))

        fh_ctrl = ctk.CTkFrame(self.fade_ctrl_frame, fg_color="transparent")
        fh_ctrl.pack(fill="x", pady=(0, 6))

        self.btn_fh_minus = ctk.CTkButton(fh_ctrl, text="-", width=28, height=24, font=ctk.CTkFont(size=14, weight="bold"), fg_color="#2b3245", hover_color="#3b445c", corner_radius=6)
        self.btn_fh_minus.pack(side="left", padx=(0, 6))

        self.fade_height_slider = ctk.CTkSlider(fh_ctrl, from_=0.15, to=0.70, number_of_steps=55, command=self._on_fade_height_slider_changed)
        self.fade_height_slider.set(self.fade_height)
        self.fade_height_slider.pack(side="left", fill="x", expand=True)

        self.btn_fh_plus = ctk.CTkButton(fh_ctrl, text="+", width=28, height=24, font=ctk.CTkFont(size=14, weight="bold"), fg_color="#2b3245", hover_color="#3b445c", corner_radius=6)
        self.btn_fh_plus.pack(side="right", padx=(6, 0))

        self._attach_button_repeat(self.btn_fh_minus, self._fade_height_step_minus)
        self._attach_button_repeat(self.btn_fh_plus, self._fade_height_step_plus)
        self._disable_slider_scroll(self.fade_height_slider, self.left_frame)

        # 2. Fade Darkness / Opacity Slider
        self.fade_opacity_label_var = ctk.StringVar(value=f"Fade Darkness: {int(round(self.fade_opacity * 100))}% (Contrast)")
        ctk.CTkLabel(self.fade_ctrl_frame, textvariable=self.fade_opacity_label_var, font=ctk.CTkFont(size=11)).pack(anchor="w", pady=(2, 1))

        fo_ctrl = ctk.CTkFrame(self.fade_ctrl_frame, fg_color="transparent")
        fo_ctrl.pack(fill="x", pady=(0, 6))

        self.btn_fo_minus = ctk.CTkButton(fo_ctrl, text="-", width=28, height=24, font=ctk.CTkFont(size=14, weight="bold"), fg_color="#2b3245", hover_color="#3b445c", corner_radius=6)
        self.btn_fo_minus.pack(side="left", padx=(0, 6))

        self.fade_opacity_slider = ctk.CTkSlider(fo_ctrl, from_=0.30, to=1.0, number_of_steps=70, command=self._on_fade_opacity_slider_changed)
        self.fade_opacity_slider.set(self.fade_opacity)
        self.fade_opacity_slider.pack(side="left", fill="x", expand=True)

        self.btn_fo_plus = ctk.CTkButton(fo_ctrl, text="+", width=28, height=24, font=ctk.CTkFont(size=14, weight="bold"), fg_color="#2b3245", hover_color="#3b445c", corner_radius=6)
        self.btn_fo_plus.pack(side="right", padx=(6, 0))

        self._attach_button_repeat(self.btn_fo_minus, self._fade_opacity_step_minus)
        self._attach_button_repeat(self.btn_fo_plus, self._fade_opacity_step_plus)
        self._disable_slider_scroll(self.fade_opacity_slider, self.left_frame)

        # 3. Quick Presets Row
        fade_presets_row = ctk.CTkFrame(self.fade_ctrl_frame, fg_color="transparent")
        fade_presets_row.pack(fill="x", pady=(2, 4))
        ctk.CTkButton(fade_presets_row, text="Subtle (25%)", height=24, font=ctk.CTkFont(size=10), fg_color="#2b3245", hover_color="#3b445c", command=lambda: self._set_fade_preset(0.25, 0.80)).pack(side="left", expand=True, fill="x", padx=(0, 4))
        ctk.CTkButton(fade_presets_row, text="Standard (38%)", height=24, font=ctk.CTkFont(size=10), fg_color="#374151", hover_color="#4b5563", command=lambda: self._set_fade_preset(0.38, 0.92)).pack(side="left", expand=True, fill="x", padx=(0, 4))
        ctk.CTkButton(fade_presets_row, text="Deep (52%)", height=24, font=ctk.CTkFont(size=10), fg_color="#2b3245", hover_color="#3b445c", command=lambda: self._set_fade_preset(0.52, 1.0)).pack(side="left", expand=True, fill="x")

        # 3. Subtitles & Captions / Lyrics
        self._create_section_label(self.clipper_container, "3. Subtitles & Lyrics (.srt, .ass, .lrc)")
        
        self.sub_switch_var = ctk.BooleanVar(value=self.saved_sub_enabled)
        self.sub_switch = ctk.CTkSwitch(
            self.clipper_container,
            text="Burn Subtitles / Lyrics into Video",
            variable=self.sub_switch_var,
            command=lambda: (self._update_cli_display(), self._save_persistent_config())
        )
        self.sub_switch.pack(anchor="w", pady=(0, 8))

        sub_box = ctk.CTkFrame(self.clipper_container, fg_color="transparent")
        sub_box.pack(fill="x", pady=(0, 6))

        self.srt_entry = ctk.CTkEntry(sub_box, placeholder_text="Select subtitle or lyrics (.srt, .ass, .lrc)...")
        self.srt_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self.btn_browse_srt = ctk.CTkButton(sub_box, text="📁 Choose File", width=110, command=self._browse_srt)
        self.btn_browse_srt.pack(side="right")

        # Caption Style Menu (Presets from marketplace: Soothe White Light, Viral Yellow, etc.)
        ctk.CTkLabel(self.clipper_container, text="Caption Style Preset:", font=ctk.CTkFont(size=12, weight="bold"), text_color="#cbd5e1").pack(anchor="w", pady=(4, 2))

        caption_styles_list = list(CAPTION_STYLES.keys())
        self.caption_style_menu = ctk.CTkOptionMenu(
            self.clipper_container,
            values=caption_styles_list,
            command=self._on_caption_style_changed
        )
        # Verify saved style is in list
        if self.saved_caption_style in caption_styles_list:
            self.caption_style_menu.set(self.saved_caption_style)
        else:
            self.caption_style_menu.set("✨ Soothe White Light (Soft Clean Glow)")
        self.caption_style_menu.pack(fill="x", pady=(0, 8))

        # Font Family & Border Options Row
        font_border_row = ctk.CTkFrame(self.clipper_container, fg_color="transparent")
        font_border_row.pack(fill="x", pady=(0, 8))

        # Font column
        font_col = ctk.CTkFrame(font_border_row, fg_color="transparent")
        font_col.pack(side="left", fill="x", expand=True, padx=(0, 4))
        ctk.CTkLabel(font_col, text="Caption Font Family:", font=ctk.CTkFont(size=11, weight="bold"), text_color="#cbd5e1").pack(anchor="w", pady=(0, 2))
        self.font_menu = ctk.CTkOptionMenu(
            font_col,
            values=AVAILABLE_FONTS,
            command=lambda _: (self._render_visual_crop(), self._update_cli_display(), self._save_persistent_config())
        )
        if getattr(self, "saved_font_name", None) in AVAILABLE_FONTS:
            self.font_menu.set(self.saved_font_name)
        else:
            self.font_menu.set("Kohinoor Devanagari")
        self.font_menu.pack(fill="x")

        # Border column
        border_col = ctk.CTkFrame(font_border_row, fg_color="transparent")
        border_col.pack(side="left", fill="x", expand=True, padx=(4, 0))
        ctk.CTkLabel(border_col, text="Caption Border / Outline:", font=ctk.CTkFont(size=11, weight="bold"), text_color="#cbd5e1").pack(anchor="w", pady=(0, 2))
        self.border_menu = ctk.CTkOptionMenu(
            border_col,
            values=BORDER_OPTIONS,
            command=lambda _: (self._render_visual_crop(), self._update_cli_display(), self._save_persistent_config())
        )
        if getattr(self, "saved_caption_border", None) in BORDER_OPTIONS:
            self.border_menu.set(self.saved_caption_border)
        else:
            self.border_menu.set("Auto (Preset Default)")
        self.border_menu.pack(fill="x")

        # Caption Size (with [-] and [+] nudge buttons)
        size_row = ctk.CTkFrame(self.clipper_container, fg_color="transparent")
        size_row.pack(fill="x", pady=(0, 8))

        ctk.CTkLabel(size_row, text="Caption Size:", font=ctk.CTkFont(size=11, weight="bold"), text_color="#cbd5e1").pack(side="left", padx=(0, 10))

        size_ctrl_box = ctk.CTkFrame(size_row, fg_color="transparent")
        size_ctrl_box.pack(side="right")

        self.btn_size_minus = ctk.CTkButton(
            size_ctrl_box,
            text="-",
            width=28,
            height=28,
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color="#2b3245",
            hover_color="#3b445c",
            corner_radius=6
        )
        self.btn_size_minus.pack(side="left", padx=(0, 4))

        self.size_menu = ctk.CTkOptionMenu(
            size_ctrl_box,
            values=FONT_SIZE_OPTIONS,
            width=140,
            command=lambda _: (self._render_visual_crop(), self._update_cli_display(), self._save_persistent_config())
        )
        init_size = self.saved_font_size if hasattr(self, "saved_font_size") else "Medium (22pt)"
        matched_init = False
        for opt in FONT_SIZE_OPTIONS:
            if init_size in opt or opt in init_size:
                self.size_menu.set(opt)
                matched_init = True
                break
        if not matched_init:
            digits = ''.join(c for c in init_size if c.isdigit())
            if digits:
                self.size_menu.set(f"{digits}pt")
            else:
                self.size_menu.set("22pt (Medium)")
        self.size_menu.pack(side="left", padx=(0, 4))

        self.btn_size_plus = ctk.CTkButton(
            size_ctrl_box,
            text="+",
            width=28,
            height=28,
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color="#2b3245",
            hover_color="#3b445c",
            corner_radius=6
        )
        self.btn_size_plus.pack(side="left")

        self._attach_button_repeat(self.btn_size_minus, lambda: self._step_font_size(-2))
        self._attach_button_repeat(self.btn_size_plus, lambda: self._step_font_size(2))

        # Subtitle Vertical Position (Y) Card
        sub_pos_card = ctk.CTkFrame(self.clipper_container, fg_color="#181c27", corner_radius=8)
        sub_pos_card.pack(fill="x", pady=(0, 14), padx=2)

        sub_pos_header = ctk.CTkFrame(sub_pos_card, fg_color="transparent")
        sub_pos_header.pack(fill="x", padx=10, pady=(8, 4))

        pct_val = int(round(self.sub_pos_y * 100))
        self.sub_pos_label_var = ctk.StringVar(value=f"Vertical Position (Y): {pct_val}%")
        ctk.CTkLabel(
            sub_pos_header,
            textvariable=self.sub_pos_label_var,
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#cbd5e1"
        ).pack(side="left")

        SUB_POS_PRESETS = [
            "✨ Above Padding (15%)",
            "🖤 Inside Bottom Bar (6%)",
            "🎯 Screen Center (50%)",
            "⬆️ Upper Third (75%)",
            "🔝 Top Edge (85%)",
            "⚙️ Custom Position"
        ]
        self.sub_pos_menu = ctk.CTkOptionMenu(
            sub_pos_header,
            values=SUB_POS_PRESETS,
            width=175,
            command=self._on_sub_pos_preset_selected
        )
        self.sub_pos_menu.pack(side="right")
        self.caption_pos_menu = self.sub_pos_menu  # backward-compatibility alias

        # Slider row with [-] and [+] nudge buttons
        sub_pos_ctrl_frame = ctk.CTkFrame(sub_pos_card, fg_color="transparent")
        sub_pos_ctrl_frame.pack(fill="x", padx=10, pady=(0, 10))

        self.btn_sub_pos_minus = ctk.CTkButton(
            sub_pos_ctrl_frame,
            text="-",
            width=30,
            height=28,
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color="#2b3245",
            hover_color="#3b445c",
            corner_radius=6
        )
        self.btn_sub_pos_minus.pack(side="left", padx=(0, 6))

        self.sub_pos_slider = ctk.CTkSlider(
            sub_pos_ctrl_frame,
            from_=0.0,
            to=0.90,
            number_of_steps=90,
            command=self._on_sub_pos_slider_changed
        )
        self.sub_pos_slider.set(self.sub_pos_y)
        self.sub_pos_slider.pack(side="left", fill="x", expand=True)

        self.btn_sub_pos_plus = ctk.CTkButton(
            sub_pos_ctrl_frame,
            text="+",
            width=30,
            height=28,
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color="#2b3245",
            hover_color="#3b445c",
            corner_radius=6
        )
        self.btn_sub_pos_plus.pack(side="right", padx=(6, 0))

        self._attach_button_repeat(self.btn_sub_pos_minus, self._step_sub_pos_minus)
        self._attach_button_repeat(self.btn_sub_pos_plus, self._step_sub_pos_plus)
        self._disable_slider_scroll(self.sub_pos_slider, self.left_frame)
        self._sync_sub_pos_menu_preset(pct_val)

        # Subtitle Text Flow / Layout Card (Word Wrap vs Overflow)
        flow_card = ctk.CTkFrame(self.clipper_container, fg_color="#181c27", corner_radius=8)
        flow_card.pack(fill="x", pady=(0, 14), padx=2)

        flow_header = ctk.CTkFrame(flow_card, fg_color="transparent")
        flow_header.pack(fill="x", padx=10, pady=(8, 4))

        self.overflow_label_var = ctk.StringVar(
            value="Caption Flow: ⋯ Overflow (Single Line)" if self.saved_caption_overflow else "Caption Flow: ⤹ Word Wrap (Multi-Line)"
        )
        ctk.CTkLabel(
            flow_header,
            textvariable=self.overflow_label_var,
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#cbd5e1"
        ).pack(side="left")

        flow_ctrl_frame = ctk.CTkFrame(flow_card, fg_color="transparent")
        flow_ctrl_frame.pack(fill="x", padx=10, pady=(0, 10))

        self.caption_overflow_switch = ctk.CTkSwitch(
            flow_ctrl_frame,
            text="Overflow Mode",
            variable=self.caption_overflow_var,
            command=self._on_caption_overflow_toggle
        )
        self.caption_overflow_switch.pack(side="left")

        flow_btn_box = ctk.CTkFrame(flow_ctrl_frame, fg_color="transparent")
        flow_btn_box.pack(side="right")

        self.btn_flow_wrap = ctk.CTkButton(
            flow_btn_box,
            text="⤹ Word Wrap",
            width=90,
            height=28,
            font=ctk.CTkFont(size=10, weight="bold"),
            fg_color="#2563eb" if not self.saved_caption_overflow else "#2b3245",
            hover_color="#1d4ed8" if not self.saved_caption_overflow else "#3b445c",
            corner_radius=6,
            command=lambda: self._set_caption_overflow(False)
        )
        self.btn_flow_wrap.pack(side="left", padx=(0, 6))

        self.btn_flow_overflow = ctk.CTkButton(
            flow_btn_box,
            text="⋯ Overflow",
            width=90,
            height=28,
            font=ctk.CTkFont(size=10, weight="bold"),
            fg_color="#2563eb" if self.saved_caption_overflow else "#2b3245",
            hover_color="#1d4ed8" if self.saved_caption_overflow else "#3b445c",
            corner_radius=6,
            command=lambda: self._set_caption_overflow(True)
        )
        self.btn_flow_overflow.pack(side="left")

        # 4. Background Music & Ducking (.mp3, .wav, .m4a)
        self._create_section_label(self.clipper_container, "4. Background Music & Ducking (.mp3, .wav, .m4a)")

        self.music_switch_var = ctk.BooleanVar(value=self.saved_music_enabled)
        self.music_switch = ctk.CTkSwitch(
            self.clipper_container,
            text="Mix Background Music Track",
            variable=self.music_switch_var,
            command=lambda: (self._update_cli_display(), self._save_persistent_config())
        )
        self.music_switch.pack(anchor="w", pady=(0, 8))

        # Main Core Action: Local File Selection
        music_box = ctk.CTkFrame(self.clipper_container, fg_color="transparent")
        music_box.pack(fill="x", pady=(0, 6))

        self.music_entry = ctk.CTkEntry(music_box, placeholder_text="Select background music file (.mp3, .wav, .m4a)...")
        self.music_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self.btn_browse_music = ctk.CTkButton(
            music_box,
            text="📁 Choose Music",
            width=110,
            command=self._browse_music
        )
        self.btn_browse_music.pack(side="right")
        self.music_entry.bind("<Return>", self._on_music_entry_changed)
        self.music_entry.bind("<FocusOut>", self._on_music_entry_changed)

        # YouTube Music Downloader Card (Directly in Section 4)
        yt_music_card = ctk.CTkFrame(self.clipper_container, fg_color="#181c27", corner_radius=8, border_width=1, border_color="#2c3344")
        yt_music_card.pack(fill="x", pady=(4, 10))

        yt_m_hdr = ctk.CTkLabel(
            yt_music_card,
            text="⬇️ Download Music from YouTube (Optional)",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#94a3b8"
        )
        yt_m_hdr.pack(anchor="w", padx=10, pady=(6, 4))

        murl_row = ctk.CTkFrame(yt_music_card, fg_color="transparent")
        murl_row.pack(fill="x", padx=10, pady=(0, 6))

        self.yt_music_url_entry = ctk.CTkEntry(
            murl_row,
            placeholder_text="Paste YouTube Audio / Music URL...",
            font=ctk.CTkFont(size=11)
        )
        self.yt_music_url_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))

        btn_paste_murl = ctk.CTkButton(
            murl_row,
            text="📋 Paste",
            width=65,
            height=26,
            font=ctk.CTkFont(size=11),
            fg_color="#334155",
            hover_color="#475569",
            command=self._paste_yt_music_url
        )
        btn_paste_murl.pack(side="left")

        mact_row = ctk.CTkFrame(yt_music_card, fg_color="transparent")
        mact_row.pack(fill="x", padx=10, pady=(0, 6))

        self.btn_yt_music_dl = ctk.CTkButton(
            mact_row,
            text="🎵 Download Music",
            height=26,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#2563eb",
            hover_color="#1d4ed8",
            command=self._start_yt_audio_download
        )
        self.btn_yt_music_dl.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.btn_yt_audio_preview = ctk.CTkButton(
            mact_row,
            text="▶ Play Preview",
            width=100,
            height=26,
            font=ctk.CTkFont(size=11),
            fg_color="#10b981",
            hover_color="#059669",
            command=self._toggle_yt_audio_preview
        )
        self.btn_yt_audio_preview.pack(side="left")

        self.yt_music_status_label = ctk.CTkLabel(
            yt_music_card,
            text="Downloaded music automatically loads into Section 4",
            font=ctk.CTkFont(size=10),
            text_color="#94a3b8"
        )
        self.yt_music_status_label.pack(anchor="w", padx=10, pady=(0, 6))

        # Ducking volume slider with - / + nudge buttons
        vol_pct = int(round(self.current_vol * 100))
        self.vol_label_var = ctk.StringVar(value=f"Background Volume: {vol_pct}% (Speech stays crisp)")
        self.vol_label = ctk.CTkLabel(self.clipper_container, textvariable=self.vol_label_var, font=ctk.CTkFont(size=12))
        self.vol_label.pack(anchor="w", pady=(0, 2))

        vol_ctrl_frame = ctk.CTkFrame(self.clipper_container, fg_color="transparent")
        vol_ctrl_frame.pack(fill="x", pady=(0, 14))

        self.btn_vol_minus = ctk.CTkButton(
            vol_ctrl_frame,
            text="-",
            width=32,
            height=28,
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color="#2b3245",
            hover_color="#3b445c",
            corner_radius=6
        )
        self.btn_vol_minus.pack(side="left", padx=(0, 6))

        self.vol_slider = ctk.CTkSlider(
            vol_ctrl_frame,
            from_=0.0,
            to=0.5,
            number_of_steps=50,
            command=self._on_vol_slider_changed
        )
        self.vol_slider.set(self.current_vol)
        self.vol_slider.pack(side="left", fill="x", expand=True)

        self.btn_vol_plus = ctk.CTkButton(
            vol_ctrl_frame,
            text="+",
            width=32,
            height=28,
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color="#2b3245",
            hover_color="#3b445c",
            corner_radius=6
        )
        self.btn_vol_plus.pack(side="right", padx=(6, 0))

        self._attach_button_repeat(self.btn_vol_minus, self._vol_step_minus)
        self._attach_button_repeat(self.btn_vol_plus, self._vol_step_plus)
        self._disable_slider_scroll(self.vol_slider, self.left_frame)

        # 5. Preview & Render Actions
        self._create_section_label(self.clipper_container, "5. Render Controls")

        offset_box = ctk.CTkFrame(self.clipper_container, fg_color="transparent")
        offset_box.pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(offset_box, text="Start Preview Offset (sec):", font=ctk.CTkFont(size=12)).pack(side="left", padx=(0, 10))
        self.offset_entry = ctk.CTkEntry(offset_box, width=80)
        self.offset_entry.insert(0, "0")
        self.offset_entry.pack(side="left")

        self.btn_preview = ctk.CTkButton(
            self.clipper_container,
            text="🎬 1-Min Single Preview (at Offset)",
            height=38,
            fg_color="#6366f1",
            hover_color="#4f46e5",
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._start_generate_preview
        )
        self.btn_preview.pack(fill="x", pady=(0, 6))

        self.btn_multi_preview = ctk.CTkButton(
            self.clipper_container,
            text="🎞️ 3-Segment Preview (0%, 45%, 75% • 3 Min)",
            height=40,
            fg_color="#7c3aed",
            hover_color="#6d28d9",
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._start_generate_multi_preview
        )
        self.btn_multi_preview.pack(fill="x", pady=(0, 10))

        # Bottom scroll clearance spacer (ensures full visibility of buttons when scrolling to bottom)
        ctk.CTkFrame(self.clipper_container, height=36, fg_color="transparent").pack(fill="x", pady=(0, 10))




        # ==============================================================
        # ==============================================================
        # RIGHT COLUMN: TABS (Visual Preview OR Batch Auto-Clipper)
        # ==============================================================
        self.right_tabs = ctk.CTkTabview(self, corner_radius=10)
        self.right_tabs.grid(row=0, column=1, padx=(8, 16), pady=16, sticky="nsew")

        self.tab_preview = self.right_tabs.add("Crop Framing & Preview")
        self.tab_batch = self.right_tabs.add("✂️ Batch Auto-Clipper")

        # --- TAB 1: PREVIEW & HEADLESS WHOLE VIDEO ---
        self._create_section_label(self.tab_preview, "Visual Crop Framing (Live Preview)")

        preview_mode_frame = ctk.CTkFrame(self.tab_preview, fg_color="transparent")
        preview_mode_frame.pack(fill="x", padx=10, pady=(0, 6))

        self.preview_mode_segmented = ctk.CTkSegmentedButton(
            preview_mode_frame,
            values=["📱 Final Framed View", "📐 Source Cutout View"],
            command=self._on_preview_mode_changed
        )
        self.preview_mode_segmented.set("📱 Final Framed View")
        self.preview_mode_segmented.pack(side="left")

        self.canvas_label = ctk.CTkLabel(
            self.tab_preview,
            text="Select a video to see live crop preview",
            width=500,
            height=260,
            fg_color="#13161f",
            corner_radius=8
        )
        self.canvas_label.pack(fill="x", padx=10, pady=(0, 4))

        # Mouse hold-and-drag interaction on preview canvas
        self.canvas_label.bind("<Button-1>", self._on_preview_drag_start)
        self.canvas_label.bind("<B1-Motion>", self._on_preview_drag_motion)
        self.canvas_label.bind("<MouseWheel>", self._on_canvas_mousewheel)

        self.canvas_hint_label = ctk.CTkLabel(
            self.tab_preview,
            text="✋ Drag preview to pan (X/Y) | Scroll mouse wheel to Zoom (Scale Up/Down)",
            font=ctk.CTkFont(size=11),
            text_color="#94a3b8"
        )
        self.canvas_hint_label.pack(anchor="w", padx=10, pady=(0, 8))

        self.filter_info_label = ctk.CTkLabel(
            self.tab_preview,
            text="FFmpeg Crop Filter: None",
            font=ctk.CTkFont(family="Courier", size=12),
            text_color="#818cf8"
        )
        self.filter_info_label.pack(anchor="w", padx=10, pady=(0, 10))

        self._create_section_label(self.tab_preview, "🚀 Run Whole Video on Command Line (Without UI)")
        info_txt = ctk.CTkLabel(
            self.tab_preview,
            text="Processes full video (even 10+ hours) at maximum Apple Silicon hardware speed:",
            text_color="gray",
            font=ctk.CTkFont(size=11),
            wraplength=480,
            justify="left"
        )
        info_txt.pack(anchor="w", padx=10, pady=(0, 4))

        self.cli_textbox = ctk.CTkTextbox(self.tab_preview, height=75, font=ctk.CTkFont(family="Courier", size=11))
        self.cli_textbox.pack(fill="x", padx=10, pady=(0, 8))

        btn_box = ctk.CTkFrame(self.tab_preview, fg_color="transparent")
        btn_box.pack(fill="x", padx=10, pady=(0, 10))

        self.btn_copy_cli = ctk.CTkButton(
            btn_box,
            text="📋 Copy Command",
            width=130,
            command=self._copy_cli_command
        )
        self.btn_copy_cli.pack(side="left", padx=(0, 10))

        self.btn_run_full = ctk.CTkButton(
            btn_box,
            text="⚡ Run Whole Video in Background",
            fg_color="#10b981",
            hover_color="#059669",
            command=self._start_run_full_video
        )
        self.btn_run_full.pack(side="left")

        self.btn_cancel_full = ctk.CTkButton(
            btn_box,
            text="🛑 Master Kill",
            fg_color="#ef4444",
            hover_color="#dc2626",
            width=110,
            command=self._master_kill_process
        )
        self.btn_cancel_full.pack(side="left", padx=(10, 0))

        self.progress_bar = ctk.CTkProgressBar(self.tab_preview)
        self.progress_bar.set(0)
        self.progress_bar.pack(fill="x", padx=10, pady=(6, 4))

        self.status_label = ctk.CTkLabel(
            self.tab_preview,
            text="Status: Ready",
            font=ctk.CTkFont(size=12),
            text_color="gray"
        )
        self.status_label.pack(anchor="w", padx=10, pady=(0, 6))

        # --- TAB 2: BATCH AUTO-CLIPPER ---
        self._create_section_label(self.tab_batch, "Paste Timestamps & Titles (or Load .txt/.md):")
        
        batch_info = ctk.CTkLabel(
            self.tab_batch,
            text="Paste your list (Markdown bullets, timestamps, and Hindi titles). Each clip is named by its title.\n💡 Shorts (≤3m) use your selected ratio. Clips >3m are automatically kept in 16:9 YouTube format (unscaled original video with background music & subs).",
            text_color="#9ca3af",
            font=ctk.CTkFont(size=11),
            wraplength=520,
            justify="left"
        )
        batch_info.pack(anchor="w", padx=10, pady=(0, 6))

        self.batch_textbox = ctk.CTkTextbox(self.tab_batch, height=220, font=ctk.CTkFont(size=12))
        self.batch_textbox.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        self.batch_textbox.bind("<KeyRelease>", self._on_batch_text_changed)

        # Pre-load clips_timestamps.txt if available
        sample_ts = BASE_DIR / "clips_timestamps.txt"
        if sample_ts.exists():
            try:
                with open(sample_ts, "r", encoding="utf-8") as f:
                    self.batch_textbox.insert("1.0", f.read())
            except Exception:
                pass

        batch_ctrl_frame = ctk.CTkFrame(self.tab_batch, fg_color="transparent")
        batch_ctrl_frame.pack(fill="x", padx=10, pady=(0, 8))

        self.btn_load_ts = ctk.CTkButton(
            batch_ctrl_frame,
            text="📁 Load File (.txt/.md)",
            width=140,
            command=self._browse_batch_timestamps
        )
        self.btn_load_ts.pack(side="left", padx=(0, 8))

        self.btn_fix_ts = ctk.CTkButton(
            batch_ctrl_frame,
            text="🪄 Fix Hour Typos",
            width=135,
            fg_color="#059669",
            hover_color="#047857",
            command=self._fix_batch_hour_typos
        )
        self.btn_fix_ts.pack(side="left", padx=(0, 8))

        self.btn_clear_ts = ctk.CTkButton(
            batch_ctrl_frame,
            text="Clear",
            width=70,
            fg_color="#374151",
            hover_color="#4b5563",
            command=self._clear_batch_timestamps
        )
        self.btn_clear_ts.pack(side="left", padx=(0, 8))

        self.batch_count_label = ctk.CTkLabel(
            batch_ctrl_frame,
            text="Detected: 0 clips",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#818cf8"
        )
        self.batch_count_label.pack(side="right")

        # Options row (Skip completed clips)
        batch_opt_frame = ctk.CTkFrame(self.tab_batch, fg_color="transparent")
        batch_opt_frame.pack(fill="x", padx=10, pady=(0, 6))

        self.cb_skip_existing = ctk.CTkCheckBox(
            batch_opt_frame,
            text="⚡ Skip already finished clips (Instant Resume)",
            variable=self.skip_existing_var,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#10b981"
        )
        self.cb_skip_existing.pack(side="left")

        # Action buttons
        batch_action_frame = ctk.CTkFrame(self.tab_batch, fg_color="transparent")
        batch_action_frame.pack(fill="x", padx=10, pady=(0, 8))

        self.btn_run_batch = ctk.CTkButton(
            batch_action_frame,
            text="🚀 Clip All Segments (Hardware Accelerated)",
            fg_color="#6366f1",
            hover_color="#4f46e5",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=38,
            command=self._start_run_batch_clips
        )
        self.btn_run_batch.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.btn_cancel_batch = ctk.CTkButton(
            batch_action_frame,
            text="🛑 Master Kill",
            fg_color="#ef4444",
            hover_color="#dc2626",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=38,
            width=120,
            command=self._master_kill_process
        )
        self.btn_cancel_batch.pack(side="left", padx=(0, 6))

        self.btn_open_approved = ctk.CTkButton(
            batch_action_frame,
            text="📁 Open Clips",
            width=105,
            height=38,
            command=self._open_approved_folder
        )
        self.btn_open_approved.pack(side="right")

        self.batch_progress_bar = ctk.CTkProgressBar(self.tab_batch)
        self.batch_progress_bar.set(0)
        self.batch_progress_bar.pack(fill="x", padx=10, pady=(4, 4))

        self.batch_status_label = ctk.CTkLabel(
            self.tab_batch,
            text="Ready to batch process clips",
            font=ctk.CTkFont(size=12),
            text_color="gray"
        )
        self.batch_status_label.pack(anchor="w", padx=10, pady=(0, 6))

        self._on_batch_text_changed()

    def _create_section_label(self, parent, text: str):
        lbl = ctk.CTkLabel(parent, text=text, font=ctk.CTkFont(size=13, weight="bold"))
        lbl.pack(anchor="w", pady=(6, 4))
        return lbl

    # ==============================================================
    # YOUTUBE INTEGRATION TOOLS (INLINE IN SHORTS & VIDEOS PANELS)
    # ==============================================================
    def _build_yt_tools_card(self):
        """Constructs the compact YouTube Tools Card inside self.clipper_container."""
        self.yt_tools_card = ctk.CTkFrame(
            self.clipper_container,
            fg_color="#141824",
            border_width=1,
            border_color="#dc2626",
            corner_radius=12
        )
        # Unpacked by default (revealed when YouTube toggle is ON)

        # Header Title
        hdr = ctk.CTkFrame(self.yt_tools_card, fg_color="transparent")
        hdr.pack(fill="x", padx=12, pady=(10, 6))

        lbl_yt_title = ctk.CTkLabel(
            hdr,
            text="🔴 YOUTUBE INTEGRATION TOOLS",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#f87171"
        )
        lbl_yt_title.pack(side="left")

        self.yt_active_mode_lbl = ctk.CTkLabel(
            hdr,
            text="Active Target: " + ("📱 Shorts" if self.active_mode == "shorts" else "🎬 Videos"),
            font=ctk.CTkFont(size=11),
            text_color="#94a3b8"
        )
        self.yt_active_mode_lbl.pack(side="right")

        # -----------------------------------------------------------------
        # A. YOUTUBE VIDEO IMPORT
        # -----------------------------------------------------------------
        sec_a_lbl = ctk.CTkLabel(self.yt_tools_card, text="A. YOUTUBE VIDEO IMPORT", font=ctk.CTkFont(size=12, weight="bold"))
        sec_a_lbl.pack(anchor="w", padx=12, pady=(4, 4))

        url_row = ctk.CTkFrame(self.yt_tools_card, fg_color="transparent")
        url_row.pack(fill="x", padx=12, pady=(0, 6))

        self.yt_url_entry = ctk.CTkEntry(
            url_row,
            placeholder_text="Paste YouTube Video or Shorts URL...",
            font=ctk.CTkFont(size=12)
        )
        self.yt_url_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))

        btn_paste_url = ctk.CTkButton(
            url_row,
            text="📋 Paste",
            width=65,
            height=28,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#334155",
            hover_color="#475569",
            command=self._paste_yt_url
        )
        btn_paste_url.pack(side="left")

        # Quality + Download Button Row
        act_row = ctk.CTkFrame(self.yt_tools_card, fg_color="transparent")
        act_row.pack(fill="x", padx=12, pady=(0, 6))

        lbl_q = ctk.CTkLabel(act_row, text="Quality:", font=ctk.CTkFont(size=11))
        lbl_q.pack(side="left", padx=(0, 6))

        self.yt_quality_menu = ctk.CTkOptionMenu(
            act_row,
            values=["Highest Available (4K/1080p)", "1080p Full HD", "720p HD"],
            width=175,
            height=28,
            font=ctk.CTkFont(size=11)
        )
        self.yt_quality_menu.set("Highest Available (4K/1080p)")
        self.yt_quality_menu.pack(side="left", padx=(0, 8))

        self.btn_yt_download = ctk.CTkButton(
            act_row,
            text="⬇️ Download & Import",
            height=28,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#dc2626",
            hover_color="#b91c1c",
            command=self._start_yt_download
        )
        self.btn_yt_download.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.btn_yt_cancel = ctk.CTkButton(
            act_row,
            text="✖ Cancel",
            width=60,
            height=28,
            font=ctk.CTkFont(size=11),
            fg_color="#475569",
            hover_color="#64748b",
            state="disabled",
            command=self._cancel_yt_download
        )
        self.btn_yt_cancel.pack(side="left")

        self.yt_progress_bar = ctk.CTkProgressBar(self.yt_tools_card)
        self.yt_progress_bar.set(0.0)
        self.yt_progress_bar.pack(fill="x", padx=12, pady=(4, 4))

        self.yt_status_label = ctk.CTkLabel(
            self.yt_tools_card,
            text="Paste a YouTube link above and click Download & Import",
            font=ctk.CTkFont(size=11),
            text_color="#94a3b8"
        )
        self.yt_status_label.pack(anchor="w", padx=12, pady=(0, 8))

        # -----------------------------------------------------------------
        # B. CAPTIONS & SUBTITLES
        # -----------------------------------------------------------------
        cap_hdr = ctk.CTkFrame(self.yt_tools_card, fg_color="transparent")
        cap_hdr.pack(fill="x", padx=12, pady=(4, 4))

        sec_b_lbl = ctk.CTkLabel(cap_hdr, text="B. CAPTIONS & SUBTITLES", font=ctk.CTkFont(size=12, weight="bold"))
        sec_b_lbl.pack(side="left")

        self.yt_auto_captions_switch = ctk.CTkSwitch(
            cap_hdr,
            text="Auto Captions",
            font=ctk.CTkFont(size=11, weight="bold"),
            variable=self.yt_auto_captions_var,
            command=self._on_yt_auto_captions_toggled
        )
        self.yt_auto_captions_switch.pack(side="right")

        self.yt_cap_box = ctk.CTkFrame(self.yt_tools_card, fg_color="transparent")
        self.yt_cap_box.pack(fill="x", padx=12, pady=(0, 8))

        sub_url_row = ctk.CTkFrame(self.yt_cap_box, fg_color="transparent")
        sub_url_row.pack(fill="x", pady=(0, 5))

        self.yt_sub_url_entry = ctk.CTkEntry(
            sub_url_row,
            placeholder_text="YouTube Subtitle URL (defaults to video URL)...",
            font=ctk.CTkFont(size=11)
        )
        self.yt_sub_url_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))

        btn_paste_sub = ctk.CTkButton(
            sub_url_row,
            text="📋 Paste",
            width=65,
            height=26,
            font=ctk.CTkFont(size=11),
            fg_color="#334155",
            hover_color="#475569",
            command=self._paste_yt_sub_url
        )
        btn_paste_sub.pack(side="left")

        sub_btn_row = ctk.CTkFrame(self.yt_cap_box, fg_color="transparent")
        sub_btn_row.pack(fill="x", pady=(0, 4))

        lbl_lang = ctk.CTkLabel(sub_btn_row, text="Lang:", font=ctk.CTkFont(size=11))
        lbl_lang.pack(side="left", padx=(0, 4))

        self.yt_sub_lang_menu = ctk.CTkOptionMenu(
            sub_btn_row,
            values=["Auto-Detect / English (en)", "Hindi (hi)", "Spanish (es)", "French (fr)", "German (de)", "All Available"],
            width=150,
            height=26,
            font=ctk.CTkFont(size=10)
        )
        self.yt_sub_lang_menu.set("Auto-Detect / English (en)")
        self.yt_sub_lang_menu.pack(side="left", padx=(0, 6))

        self.btn_download_srt = ctk.CTkButton(
            sub_btn_row,
            text="📥 Download SRT",
            height=26,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#2563eb",
            hover_color="#1d4ed8",
            command=self._start_download_subtitles_srt
        )
        self.btn_download_srt.pack(side="left", fill="x", expand=True, padx=(0, 4))

        self.btn_download_txt = ctk.CTkButton(
            sub_btn_row,
            text="📄 Download TXT",
            height=26,
            font=ctk.CTkFont(size=11),
            fg_color="#334155",
            hover_color="#475569",
            command=self._start_download_subtitles_txt
        )
        self.btn_download_txt.pack(side="left")

        self.yt_sub_status_label = ctk.CTkLabel(
            self.yt_cap_box,
            text="SRT automatically imports into Section 3 (Caption Editor)",
            font=ctk.CTkFont(size=10),
            text_color="#4ade80"
        )
        self.yt_sub_status_label.pack(anchor="w", pady=(0, 4))

        # -----------------------------------------------------------------
        # C. AUDIO ENHANCEMENT (NOISE REMOVAL)
        # -----------------------------------------------------------------
        enh_hdr = ctk.CTkFrame(self.yt_tools_card, fg_color="transparent")
        enh_hdr.pack(fill="x", padx=12, pady=(4, 4))

        sec_d_lbl = ctk.CTkLabel(enh_hdr, text="D. AUDIO ENHANCEMENT", font=ctk.CTkFont(size=12, weight="bold"))
        sec_d_lbl.pack(side="left")

        self.yt_noise_switch = ctk.CTkSwitch(
            enh_hdr,
            text="Auto Noise Removal",
            font=ctk.CTkFont(size=11, weight="bold"),
            variable=self.yt_noise_removal_var
        )
        self.yt_noise_switch.pack(side="right")

        noi_row = ctk.CTkFrame(self.yt_tools_card, fg_color="transparent")
        noi_row.pack(fill="x", padx=12, pady=(0, 10))

        lbl_int = ctk.CTkLabel(noi_row, text="Denoise Intensity:", font=ctk.CTkFont(size=11))
        lbl_int.pack(side="left", padx=(0, 8))

        self.yt_noise_intensity_seg = ctk.CTkSegmentedButton(
            noi_row,
            values=["Low", "Medium", "High"],
            height=24,
            command=self._on_yt_noise_intensity_changed
        )
        self.yt_noise_intensity_seg.set(self.yt_noise_intensity)
        self.yt_noise_intensity_seg.pack(side="left", fill="x", expand=True)

        # -----------------------------------------------------------------
        # E. STORAGE & 2-DAY AUTO-RETENTION
        # -----------------------------------------------------------------
        sec_e_lbl = ctk.CTkLabel(self.yt_tools_card, text="E. STORAGE & RETENTION (2-DAY AUTO-EXPIRY)", font=ctk.CTkFont(size=12, weight="bold"))
        sec_e_lbl.pack(anchor="w", padx=12, pady=(4, 2))

        ret_info_lbl = ctk.CTkLabel(
            self.yt_tools_card,
            text="⏱️ Auto-Retention: YouTube loaded videos & assets older than 2 days are automatically deleted to save disk space.",
            font=ctk.CTkFont(size=10),
            text_color="#94a3b8"
        )
        ret_info_lbl.pack(anchor="w", padx=12, pady=(0, 6))

        ret_btn_row = ctk.CTkFrame(self.yt_tools_card, fg_color="transparent")
        ret_btn_row.pack(fill="x", padx=12, pady=(0, 10))

        self.btn_clean_yt_old = ctk.CTkButton(
            ret_btn_row,
            text="🗑️ Clean 2d+ Old Videos",
            height=26,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#334155",
            hover_color="#475569",
            command=self._manual_clean_old_yt_videos
        )
        self.btn_clean_yt_old.pack(side="left", padx=(0, 6))

        self.btn_open_yt_folder = ctk.CTkButton(
            ret_btn_row,
            text="📂 Open Downloads Folder",
            height=26,
            font=ctk.CTkFont(size=11),
            fg_color="#1e293b",
            hover_color="#334155",
            command=self._open_downloads_folder
        )
        self.btn_open_yt_folder.pack(side="left")

        self.yt_cleanup_status_lbl = ctk.CTkLabel(
            ret_btn_row,
            text="",
            font=ctk.CTkFont(size=10),
            text_color="#38bdf8"
        )
        self.yt_cleanup_status_lbl.pack(side="left", padx=(8, 0))

    # -----------------------------------------------------------------
    # YOUTUBE ACTIONS & WORKFLOW INTEGRATION
    # -----------------------------------------------------------------
    def _toggle_youtube_mode(self):
        """Toggles YouTube integration tools on/off in both Shorts and Videos panels."""
        self.yt_enabled = not getattr(self, "yt_enabled", False)
        if self.yt_enabled:
            self.btn_youtube_toggle.configure(
                text="▶ YouTube ON",
                fg_color="#dc2626",
                hover_color="#b91c1c",
                text_color="#ffffff",
                border_color="#ef4444"
            )
            if hasattr(self, "yt_tools_card") and hasattr(self, "sec1_label"):
                self.yt_tools_card.pack(fill="x", pady=(0, 14), before=self.sec1_label)
        else:
            self.btn_youtube_toggle.configure(
                text="▶ YouTube OFF",
                fg_color="#1e293b",
                hover_color="#334155",
                text_color="#f87171",
                border_color="#dc2626"
            )
            if hasattr(self, "yt_tools_card"):
                self.yt_tools_card.pack_forget()

        self._update_scroll_region()
        self._save_persistent_config()

    def _paste_yt_url(self):
        try:
            clip = self.clipboard_get().strip()
            if clip:
                self.yt_url_entry.delete(0, "end")
                self.yt_url_entry.insert(0, clip)
                if not self.yt_sub_url_entry.get().strip():
                    self.yt_sub_url_entry.delete(0, "end")
                    self.yt_sub_url_entry.insert(0, clip)
        except Exception:
            pass

    def _paste_yt_sub_url(self):
        try:
            clip = self.clipboard_get().strip()
            if clip:
                self.yt_sub_url_entry.delete(0, "end")
                self.yt_sub_url_entry.insert(0, clip)
        except Exception:
            pass

    def _paste_yt_music_url(self):
        try:
            clip = self.clipboard_get().strip()
            if clip:
                self.yt_music_url_entry.delete(0, "end")
                self.yt_music_url_entry.insert(0, clip)
        except Exception:
            pass

    def _open_downloads_folder(self):
        DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(["open", str(DOWNLOADS_DIR)])
        except Exception as e:
            messagebox.showerror("Error Opening Folder", str(e))

    def _manual_clean_old_yt_videos(self):
        try:
            if hasattr(self, "btn_clean_yt_old"):
                self.btn_clean_yt_old.configure(state="disabled", text="⏳ Cleaning...")
            if hasattr(self, "yt_cleanup_status_lbl"):
                self.yt_cleanup_status_lbl.configure(text="Scanning files...", text_color="#38bdf8")

            def _clean_worker():
                days = getattr(self, "yt_auto_delete_days", DEFAULT_EXPIRY_DAYS)
                res = cleanup_expired_youtube_videos(max_age_days=days, verbose=True)
                count = res.get("deleted_count", 0)
                freed = res.get("freed_str", "0 MB")

                def _finish():
                    if hasattr(self, "btn_clean_yt_old"):
                        self.btn_clean_yt_old.configure(state="normal", text="🗑️ Clean 2d+ Old Videos")
                    if hasattr(self, "yt_cleanup_status_lbl"):
                        if count > 0:
                            msg = f"Cleaned {count} file(s) (> {days:.0f}d old), freed {freed}"
                            self.yt_cleanup_status_lbl.configure(text=f"✅ {msg}", text_color="#4ade80")
                        else:
                            self.yt_cleanup_status_lbl.configure(
                                text=f"✅ No files older than {days:.0f} days found",
                                text_color="#38bdf8"
                            )
                    # If current video was deleted, reset UI if needed
                    if hasattr(self, "video_path") and self.video_path and not os.path.exists(self.video_path):
                        self.video_path = None
                        if hasattr(self, "btn_select_video"):
                            self.btn_select_video.configure(text="Choose Video File")
                        if hasattr(self, "lbl_selected_video"):
                            self.lbl_selected_video.configure(text="No video selected")
                    if hasattr(self, "srt_path") and self.srt_path and not os.path.exists(self.srt_path):
                        self.srt_path = None
                        if hasattr(self, "srt_entry"):
                            self.srt_entry.delete(0, "end")
                    if hasattr(self, "music_path") and self.music_path and not os.path.exists(self.music_path):
                        self.music_path = None
                        if hasattr(self, "music_entry"):
                            self.music_entry.delete(0, "end")

                self.after(0, _finish)

            threading.Thread(target=_clean_worker, daemon=True).start()
        except Exception as e:
            if hasattr(self, "btn_clean_yt_old"):
                self.btn_clean_yt_old.configure(state="normal", text="🗑️ Clean 2d+ Old Videos")
            if hasattr(self, "yt_cleanup_status_lbl"):
                self.yt_cleanup_status_lbl.configure(text=f"❌ Error: {e}", text_color="#f87171")

    def _periodic_yt_cleanup(self):
        try:
            def _worker():
                days = getattr(self, "yt_auto_delete_days", DEFAULT_EXPIRY_DAYS)
                cleanup_expired_youtube_videos(max_age_days=days, verbose=False)
            threading.Thread(target=_worker, daemon=True).start()
        except Exception:
            pass
        finally:
            self.after(3600000, self._periodic_yt_cleanup)

    def _start_yt_download(self):
        url = self.yt_url_entry.get().strip()
        if not url:
            messagebox.showwarning("Missing Link", "Please paste or enter a YouTube video URL.")
            return
        if not (url.startswith("http://") or url.startswith("https://")):
            messagebox.showwarning("Invalid Link", "Please enter a valid link starting with https://")
            return
        if self.is_yt_downloading:
            messagebox.showinfo("In Progress", "A YouTube download is already in progress.")
            return

        if not self.yt_sub_url_entry.get().strip():
            self.yt_sub_url_entry.delete(0, "end")
            self.yt_sub_url_entry.insert(0, url)

        q_sel = self.yt_quality_menu.get()
        quality = "best"
        if "1080p" in q_sel:
            quality = "1080p"
        elif "720p" in q_sel:
            quality = "720p"

        self.is_yt_downloading = True
        self.btn_yt_download.configure(state="disabled", text="⏳ Starting...")
        self.btn_yt_cancel.configure(state="normal")
        self.yt_progress_bar.set(0.0)
        self.yt_status_label.configure(text="Connecting to YouTube...", text_color="#38bdf8")

        while not self.yt_msg_queue.empty():
            try:
                self.yt_msg_queue.get_nowait()
            except Exception:
                break

        auto_cap = self.yt_auto_captions_var.get()

        self.yt_downloader = YouTubeDownloader(
            output_dir=DOWNLOADS_DIR,
            progress_callback=lambda d: self.yt_msg_queue.put(("progress", d)),
            completion_callback=lambda f: self.yt_msg_queue.put(("complete", f)),
            error_callback=lambda e: self.yt_msg_queue.put(("error", e)),
            auto_cleanup_days=getattr(self, "yt_auto_delete_days", DEFAULT_EXPIRY_DAYS)
        )
        self.yt_downloader.start_download_async(
            url,
            quality=quality,
            extract_captions=auto_cap
        )
        self.after(50, self._check_yt_queue)

    def _check_yt_queue(self):
        try:
            while not self.yt_msg_queue.empty():
                msg_type, payload = self.yt_msg_queue.get_nowait()
                if msg_type == "progress":
                    self._on_yt_progress(payload)
                elif msg_type == "complete":
                    self._on_yt_complete(payload)
                elif msg_type == "error":
                    self._on_yt_error(payload)
        except Exception:
            pass

        if self.is_yt_downloading or not self.yt_msg_queue.empty():
            self.after(50, self._check_yt_queue)

    def _on_yt_progress(self, data: Dict[str, Any]):
        status = data.get("status")
        pct = data.get("percent", 0.0)
        self.yt_progress_bar.set(pct / 100.0)

        if status == "downloading":
            self.btn_yt_download.configure(state="disabled", text=f"⏳ {pct:.0f}%")
            stream = data.get("stream", "Stream")
            speed = data.get("speed", "")
            eta = data.get("eta", "")
            size = data.get("size", "")
            stat_text = f"⬇️ {stream}: {pct:.1f}%"
            details = [x for x in [size, speed, f"ETA {eta}" if eta and eta != "--:--" else ""] if x]
            if details:
                stat_text += f" ({', '.join(details)})"
            self.yt_status_label.configure(text=stat_text, text_color="#38bdf8")
        elif status in ["processing", "merging"]:
            self.btn_yt_download.configure(state="disabled", text="⚡ Merging...")
            self.yt_status_label.configure(
                text=data.get("message", "⚡ Merging highest quality Video & Audio with FFmpeg locally..."),
                text_color="#f59e0b"
            )
        elif status in ["starting", "fetching_info"]:
            self.yt_status_label.configure(text=data.get("message", "Fetching info..."), text_color="#94a3b8")
        elif status == "retrying":
            self.btn_yt_download.configure(state="disabled", text="🔄 Resuming...")
            self.yt_status_label.configure(
                text=data.get("message", "🔄 Network stalled. Resuming download..."),
                text_color="#f59e0b"
            )
        elif status == "cancelled":
            self.yt_status_label.configure(text="Download cancelled", text_color="#ef4444")

    def _on_yt_complete(self, filepath: str):
        self.is_yt_downloading = False
        self.btn_yt_download.configure(state="normal", text="⬇️ Download & Import")
        self.btn_yt_cancel.configure(state="disabled")
        self.yt_progress_bar.set(1.0)
        self.yt_status_label.configure(
            text=f"✅ Video Downloaded & Loaded into {self.active_mode.title()}!",
            text_color="#4ade80"
        )
        if os.path.exists(filepath):
            # Load into existing active Shorts/Videos editor
            self._load_video(filepath)

            # Auto-check subtitles if enabled
            if self.yt_auto_captions_var.get():
                sub_path = find_matching_subtitles(filepath)
                if sub_path and os.path.exists(sub_path):
                    self.srt_entry.delete(0, "end")
                    self.srt_entry.insert(0, sub_path)
                    self.sub_switch_var.set(True)
                    self.srt_path = sub_path
                    self._render_visual_crop()
                    self.yt_sub_status_label.configure(text=f"✅ Subtitles Auto-Imported: {Path(sub_path).name}", text_color="#4ade80")
                else:
                    # Speech-to-Text Fallback using Whisper AI
                    auto_srt = str(Path(filepath).with_suffix(".srt"))
                    self.yt_sub_status_label.configure(text="🎙️ Transcribing dialogue with Whisper AI...", text_color="#38bdf8")
                    def _gen_stt():
                        ok = generate_speech_timed_srt(filepath, auto_srt)
                        if ok and os.path.exists(auto_srt):
                            def _load_stt():
                                self.srt_entry.delete(0, "end")
                                self.srt_entry.insert(0, auto_srt)
                                self.sub_switch_var.set(True)
                                self.srt_path = auto_srt
                                self._render_visual_crop()
                                self.yt_sub_status_label.configure(text="✅ Whisper AI Captions Generated & Loaded!", text_color="#4ade80")
                            self.after(0, _load_stt)
                        else:
                            self.after(0, lambda: self.yt_sub_status_label.configure(text="⚠️ No captions available", text_color="#f87171"))
                    threading.Thread(target=_gen_stt, daemon=True).start()

    def _on_yt_error(self, err_msg: str):
        self.is_yt_downloading = False
        self.btn_yt_download.configure(state="normal", text="⬇️ Download & Import")
        self.btn_yt_cancel.configure(state="disabled")
        self.yt_status_label.configure(text=f"❌ Failed: {err_msg[:70]}", text_color="#f87171")
        friendly_msg = err_msg
        if "timed out" in err_msg.lower() or "read operation" in err_msg.lower():
            friendly_msg = (
                f"{err_msg}\n\n"
                "💡 Tip: YouTube's connection stalled or experienced network latency.\n"
                "Any partially downloaded chunks have been preserved. Clicking 'Download & Import' again will automatically resume right where it left off."
            )
        messagebox.showerror("YouTube Download Error", f"Failed to download video:\n\n{friendly_msg}")

    def _cancel_yt_download(self):
        if self.yt_downloader and self.is_yt_downloading:
            self.yt_downloader.cancel()
        self.is_yt_downloading = False
        self.btn_yt_download.configure(state="normal", text="⬇️ Download & Import")
        self.btn_yt_cancel.configure(state="disabled")
        self.yt_progress_bar.set(0.0)
        self.yt_status_label.configure(text="Download cancelled by user", text_color="#94a3b8")

    def _on_yt_auto_captions_toggled(self):
        on = self.yt_auto_captions_var.get()
        if on:
            self.yt_cap_box.pack(fill="x", padx=12, pady=(0, 8))
        else:
            self.yt_cap_box.pack_forget()
        self._update_scroll_region()
        self._save_persistent_config()

    def _start_download_subtitles_srt(self):
        url = self.yt_sub_url_entry.get().strip() or self.yt_url_entry.get().strip()
        if not url:
            messagebox.showwarning("Missing Link", "Please paste or enter a YouTube URL to download captions.")
            return

        lang_val = self.yt_sub_lang_menu.get()
        self.btn_download_srt.configure(state="disabled", text="⏳ Downloading...")
        self.yt_sub_status_label.configure(text="Fetching subtitles from YouTube...", text_color="#38bdf8")

        def _worker():
            try:
                dl = YouTubeDownloader(output_dir=DOWNLOADS_DIR)
                srt_path = dl.download_subtitles(url, lang=lang_val, as_txt=False)
                if srt_path and os.path.exists(srt_path):
                    def _apply():
                        self.btn_download_srt.configure(state="normal", text="📥 Download SRT")
                        self.srt_entry.delete(0, "end")
                        self.srt_entry.insert(0, srt_path)
                        self.sub_switch_var.set(True)
                        self.srt_path = srt_path
                        self._render_visual_crop()
                        self._update_cli_display()
                        self.yt_sub_status_label.configure(text=f"✅ Loaded: {Path(srt_path).name}", text_color="#4ade80")
                        self._save_persistent_config()
                    self.after(0, _apply)
                else:
                    # Speech-to-text fallback if video is available
                    if self.video_path and os.path.exists(self.video_path):
                        fallback_srt = str(Path(self.video_path).with_suffix(".srt"))
                        ok = generate_speech_timed_srt(self.video_path, fallback_srt)
                        if ok and os.path.exists(fallback_srt):
                            def _apply_fb():
                                self.btn_download_srt.configure(state="normal", text="📥 Download SRT")
                                self.srt_entry.delete(0, "end")
                                self.srt_entry.insert(0, fallback_srt)
                                self.sub_switch_var.set(True)
                                self.srt_path = fallback_srt
                                self._render_visual_crop()
                                self.yt_sub_status_label.configure(text="✅ Whisper AI Captions Generated!", text_color="#4ade80")
                            self.after(0, _apply_fb)
                            return

                    def _fail():
                        self.btn_download_srt.configure(state="normal", text="📥 Download SRT")
                        self.yt_sub_status_label.configure(text="⚠️ No subtitles found on YouTube for this video", text_color="#f87171")
                        messagebox.showinfo("Captions Unavailable", "No subtitles were found on YouTube for this video.\nIf the video is already downloaded, Speech-to-Text can generate timestamps.")
                    self.after(0, _fail)
            except Exception as e:
                err_str = str(e)
                self.after(0, lambda: self.btn_download_srt.configure(state="normal", text="📥 Download SRT"))
                self.after(0, lambda: self.yt_sub_status_label.configure(text=f"❌ Error: {err_str[:60]}", text_color="#f87171"))

        threading.Thread(target=_worker, daemon=True).start()

    def _start_download_subtitles_txt(self):
        url = self.yt_sub_url_entry.get().strip() or self.yt_url_entry.get().strip()
        if not url:
            messagebox.showwarning("Missing Link", "Please paste or enter a YouTube URL to download transcript.")
            return

        lang_val = self.yt_sub_lang_menu.get()
        self.btn_download_txt.configure(state="disabled", text="⏳...")

        def _worker():
            try:
                dl = YouTubeDownloader(output_dir=DOWNLOADS_DIR)
                txt_path = dl.download_subtitles(url, lang=lang_val, as_txt=True)
                if txt_path and os.path.exists(txt_path):
                    def _done():
                        self.btn_download_txt.configure(state="normal", text="📄 Download TXT")
                        self.yt_sub_status_label.configure(text=f"✅ Plain text transcript saved & opened!", text_color="#4ade80")
                        subprocess.run(["open", str(txt_path)])
                    self.after(0, _done)
                else:
                    def _fail():
                        self.btn_download_txt.configure(state="normal", text="📄 Download TXT")
                        self.yt_sub_status_label.configure(text="⚠️ No transcript found for this video", text_color="#f87171")
                    self.after(0, _fail)
            except Exception as e:
                err_str = str(e)
                self.after(0, lambda: self.btn_download_txt.configure(state="normal", text="📄 Download TXT"))
                self.after(0, lambda: self.yt_sub_status_label.configure(text=f"❌ Error: {err_str[:60]}", text_color="#f87171"))

        threading.Thread(target=_worker, daemon=True).start()

    def _start_yt_audio_download(self):
        url = self.yt_music_url_entry.get().strip()
        if not url:
            messagebox.showwarning("Missing Link", "Please paste or enter a YouTube music / audio URL.")
            return

        self.btn_yt_music_dl.configure(state="disabled", text="⏳ Downloading...")
        self.yt_music_status_label.configure(text="Downloading & extracting MP3...", text_color="#38bdf8")

        def _worker():
            try:
                dl = YouTubeDownloader(output_dir=AUDIO_DOWNLOADS_DIR)
                mp3_path = dl.download_audio(url)
                if mp3_path and os.path.exists(mp3_path):
                    self.after(0, lambda: self._on_yt_audio_complete(mp3_path))
                else:
                    self.after(0, lambda: self._on_yt_audio_error("Failed to extract MP3 audio."))
            except Exception as e:
                self.after(0, lambda err=str(e): self._on_yt_audio_error(err))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_yt_audio_complete(self, mp3_path: str):
        self.btn_yt_music_dl.configure(state="normal", text="🎵 Download Music")
        self.music_entry.delete(0, "end")
        self.music_entry.insert(0, mp3_path)
        self.music_switch_var.set(True)
        self.music_path = mp3_path
        self.yt_music_status_label.configure(
            text=f"✅ Loaded into Section 4: {Path(mp3_path).name}",
            text_color="#4ade80"
        )
        self._update_cli_display()
        self._save_persistent_config()

    def _on_yt_audio_error(self, err_msg: str):
        self.btn_yt_music_dl.configure(state="normal", text="🎵 Download Music")
        self.yt_music_status_label.configure(text=f"❌ Error: {err_msg[:60]}", text_color="#f87171")
        messagebox.showerror("Audio Download Error", err_msg)

    def _toggle_yt_audio_preview(self):
        if getattr(self, "yt_is_playing_audio", False):
            if hasattr(self, "yt_audio_player_proc") and self.yt_audio_player_proc:
                try:
                    self.yt_audio_player_proc.terminate()
                except Exception:
                    pass
                self.yt_audio_player_proc = None
            self.yt_is_playing_audio = False
            self.btn_yt_audio_preview.configure(text="▶ Play Preview", fg_color="#10b981")
        else:
            target_music = self.music_entry.get().strip() or getattr(self, "music_path", "")
            if not target_music or not os.path.exists(target_music):
                messagebox.showwarning("No Audio Track", "Please download or choose an audio track in Section 4 first.")
                return
            try:
                self.yt_audio_player_proc = subprocess.Popen(["/usr/bin/afplay", target_music])
                self.yt_is_playing_audio = True
                self.btn_yt_audio_preview.configure(text="⏹ Stop", fg_color="#ef4444")

                def _monitor():
                    if self.yt_audio_player_proc:
                        self.yt_audio_player_proc.wait()
                    self.after(0, lambda: self.btn_yt_audio_preview.configure(text="▶ Play Preview", fg_color="#10b981"))
                    self.yt_is_playing_audio = False

                threading.Thread(target=_monitor, daemon=True).start()
            except Exception as e:
                messagebox.showerror("Audio Playback Error", str(e))

    def _on_yt_noise_intensity_changed(self, value: str):
        self.yt_noise_intensity = value
        self._save_persistent_config()

    # ==============================================================
    # FILE BROWSING & DATA LOADING
    # ==============================================================
    def _on_video_entry_submitted(self):
        val = self.video_entry.get().strip().strip("'\"")
        if not val:
            return
        if os.path.exists(val) and os.path.isfile(val):
            if val != getattr(self, "video_path", None):
                self._load_video(val)
        elif val.startswith(("http://", "https://", "www.youtube.com", "youtu.be")):
            if hasattr(self, "yt_url_entry"):
                self.yt_url_entry.delete(0, "end")
                self.yt_url_entry.insert(0, val)
                if not self.yt_enabled:
                    self._toggle_youtube_mode()
                messagebox.showinfo("YouTube Link Detected", "Link pasted into YouTube Downloader above.\nClick 'Download & Import' to download and load this video into the editor!")

    def _paste_and_load_video(self):
        try:
            val = self.clipboard_get().strip().strip("'\"")
        except Exception:
            return
        if not val:
            return
        if os.path.exists(val) and os.path.isfile(val):
            self._load_video(val)
        elif val.startswith(("http://", "https://", "www.youtube.com", "youtu.be")):
            if hasattr(self, "yt_url_entry"):
                self.yt_url_entry.delete(0, "end")
                self.yt_url_entry.insert(0, val)
                if not self.yt_enabled:
                    self._toggle_youtube_mode()
                messagebox.showinfo("YouTube Link Detected", "Link pasted into YouTube Downloader above.\nClick 'Download & Import' to download and load this video into the editor!")
        else:
            self.video_entry.delete(0, "end")
            self.video_entry.insert(0, val)
            self._on_video_entry_submitted()

    def _browse_video(self):
        file_path = filedialog.askopenfilename(
            title="Choose Video File",
            filetypes=[
                ("Video Files", "*.mp4 *.mov *.mkv *.m4v *.webm *.avi"),
                ("All Files", "*.*")
            ]
        )
        if file_path:
            self._load_video(file_path)

    def _load_video(self, file_path: str):
        if not file_path or not os.path.exists(file_path):
            return
        self.video_path = file_path
        self.video_entry.delete(0, "end")
        self.video_entry.insert(0, file_path)

        if hasattr(self, "shorts_config"):
            self.shorts_config["video_path"] = file_path
        if hasattr(self, "videos_config"):
            self.videos_config["video_path"] = file_path
        self._save_persistent_config()

        try:
            self.video_info = probe_video(file_path)
            w = self.video_info["width"]
            h = self.video_info["height"]
            dur = self.video_info["duration"]
            has_hq_audio = (
                hasattr(self, "audio_switch_var") and
                self.audio_switch_var.get() and
                hasattr(self, "audio_entry") and
                self.audio_entry.get().strip() and
                os.path.exists(self.audio_entry.get().strip())
            )
            audio = "HQ External Audio" if has_hq_audio else ("Yes" if self.video_info["has_audio"] else "No")
            
            m, s = int(dur // 60), int(dur % 60)
            dur_fmt = f"{m}m {s}s" if m < 60 else f"{m // 60}h {m % 60}m"
            self.meta_label.configure(text=f"📐 {w}x{h}  |  ⏱️ {dur_fmt}  |  🎵 Audio: {audio}")

            # Grab keyframe for live preview
            self._extract_keyframe(file_path)
            self._render_visual_crop()
            self._update_cli_display()
        except Exception as e:
            messagebox.showerror("Error Reading Video", str(e))

    def _browse_audio(self):
        path = filedialog.askopenfilename(
            title="Choose High-Quality Audio File to Pair with Video",
            filetypes=[
                ("All Supported Audio Files", "*.mp3 *.m4a *.wav *.aac *.ogg *.flac *.opus *.wma *.alac"),
                ("M4A / AAC Audio", "*.m4a *.aac"),
                ("MP3 Audio", "*.mp3"),
                ("WAV / Lossless", "*.wav *.flac *.alac"),
                ("All Files", "*.*")
            ]
        )
        if path:
            self.audio_path = path
            self.audio_entry.delete(0, "end")
            self.audio_entry.insert(0, path)
            self.audio_switch_var.set(True)
            self._update_audio_info(path)
            self._update_cli_display()
            self._save_persistent_config()

    def _on_audio_switch_changed(self):
        self._update_audio_info(self.audio_entry.get().strip())
        self._update_cli_display()
        self._save_persistent_config()

    def _update_audio_info(self, path: str):
        if not path or not os.path.exists(path):
            self.audio_meta_label.configure(
                text="No audio file selected (video audio will be used)",
                text_color="#94a3b8"
            )
            if hasattr(self, "video_info") and self.video_info:
                w = self.video_info["width"]
                h = self.video_info["height"]
                dur = self.video_info["duration"]
                m, s = int(dur // 60), int(dur % 60)
                dur_fmt = f"{m}m {s}s" if m < 60 else f"{m // 60}h {m % 60}m"
                audio = "Yes" if self.video_info["has_audio"] else "No"
                self.meta_label.configure(text=f"📐 {w}x{h}  |  ⏱️ {dur_fmt}  |  🎵 Audio: {audio}")
            return

        is_enabled = self.audio_switch_var.get() if hasattr(self, "audio_switch_var") else True
        try:
            info = probe_audio(path)
            dur = info.get("duration", 0.0)
            m, s = int(dur // 60), int(dur % 60)
            dur_fmt = f"{m}m {s}s" if m < 60 else f"{m // 60}h {m % 60}m"
            codec = info.get("codec", "AUDIO").upper()
            fname = Path(path).name
            if len(fname) > 28:
                fname = fname[:25] + "..."
            status_prefix = "🎵 Active: " if is_enabled else "⏸️ Paused: "
            color = "#38bdf8" if is_enabled else "#64748b"
            self.audio_meta_label.configure(
                text=f"{status_prefix}{fname} ({codec}, ⏱️ {dur_fmt})",
                text_color=color
            )
        except Exception:
            fname = Path(path).name
            self.audio_meta_label.configure(
                text=f"🎵 {fname}",
                text_color="#38bdf8" if is_enabled else "#64748b"
            )

        if hasattr(self, "video_info") and self.video_info:
            w = self.video_info["width"]
            h = self.video_info["height"]
            dur = self.video_info["duration"]
            m, s = int(dur // 60), int(dur % 60)
            dur_fmt = f"{m}m {s}s" if m < 60 else f"{m // 60}h {m % 60}m"
            audio_tag = "HQ External Audio" if is_enabled else ("Yes" if self.video_info["has_audio"] else "No")
            self.meta_label.configure(text=f"📐 {w}x{h}  |  ⏱️ {dur_fmt}  |  🎵 Audio: {audio_tag}")

    def _quick_combine_audio_video(self):
        vid = self.video_entry.get().strip() if hasattr(self, "video_entry") else ""
        aud = self.audio_entry.get().strip() if hasattr(self, "audio_entry") else ""
        if not vid or not os.path.exists(vid):
            messagebox.showwarning("Missing Video", "Please select a valid source video file first.")
            return
        if not aud or not os.path.exists(aud):
            messagebox.showwarning("Missing Audio", "Please select a valid high-quality audio file first.")
            return

        vid_path = Path(vid)
        default_out = str(vid_path.parent / f"{vid_path.stem}_with_audio.mp4")
        save_path = filedialog.asksaveasfilename(
            title="Save Combined Video & Audio File",
            initialdir=str(vid_path.parent),
            initialfile=Path(default_out).name,
            defaultextension=".mp4",
            filetypes=[("MP4 Video (*.mp4)", "*.mp4"), ("All Files", "*.*")]
        )
        if not save_path:
            return

        self.status_label.configure(text="Status: ⚡ Combining Video & Audio lossless streams...")
        self.progress_bar.set(0.3)
        self.btn_quick_combine.configure(state="disabled", text="⏳ Combining...")

        def _worker():
            try:
                ok, err = combine_video_audio(vid, aud, save_path, video_volume=getattr(self, "current_video_vol", 1.0))
                if not ok:
                    raise RuntimeError(err)

                def _success():
                    self.status_label.configure(text="Status: Ready")
                    self.progress_bar.set(0.0)
                    self.btn_quick_combine.configure(state="normal", text="⚡ Combine Now")
                    ans = messagebox.askyesno(
                        "Video & Audio Combined!",
                        f"Video and high-quality audio combined successfully!\n\nSaved to:\n{save_path}\n\nWould you like to set this combined video as your current Source Video in Clipper?"
                    )
                    if ans:
                        self._load_video(save_path)
                        self.audio_switch_var.set(False)
                        self._update_audio_info(self.audio_entry.get().strip())
                        self._save_persistent_config()

                self.after(0, _success)
            except Exception as e:
                def _fail(msg=str(e)):
                    self.status_label.configure(text="Status: Combine error")
                    self.progress_bar.set(0.0)
                    self.btn_quick_combine.configure(state="normal", text="⚡ Combine Now")
                    messagebox.showerror("Combine Error", f"Failed to combine video and audio:\n{msg}")

                self.after(0, _fail)

        threading.Thread(target=_worker, daemon=True).start()

    def _extract_keyframe(self, video_path: str):
        """Extracts 1 frame snapshot for the visual crop framing box."""
        dur = float(self.video_info.get("duration", 0.0)) if self.video_info else 0.0
        seek_ts = "00:00:02" if dur > 5.0 else ("00:00:01" if dur > 1.0 else "00:00:00")

        # Try seeking first
        cmd = [
            "ffmpeg", "-y",
            "-err_detect", "ignore_err",
            "-max_error_rate", "1.0",
            "-ss", seek_ts,
            "-i", video_path,
            "-frames:v", "1",
            "-update", "1",
            "-q:v", "2",
            self.raw_frame_path
        ]
        p = subprocess.run(cmd, capture_output=True)
        if p.returncode != 0 or not os.path.exists(self.raw_frame_path) or os.path.getsize(self.raw_frame_path) == 0:
            # Fallback: extract first frame directly without seek
            cmd_fb = [
                "ffmpeg", "-y",
                "-err_detect", "ignore_err",
                "-i", video_path,
                "-frames:v", "1",
                "-update", "1",
                "-q:v", "2",
                self.raw_frame_path
            ]
            subprocess.run(cmd_fb, capture_output=True)

        try:
            if os.path.exists(self.raw_frame_path) and os.path.getsize(self.raw_frame_path) > 0:
                self.base_frame_image = Image.open(self.raw_frame_path).convert("RGB")
            else:
                self.base_frame_image = None
        except Exception as e:
            print("Failed to open snapshot frame:", e)
            self.base_frame_image = None

    def _browse_srt(self):
        path = filedialog.askopenfilename(
            title="Choose Subtitle or Lyrics File",
            filetypes=[
                ("All Supported Subtitles & Lyrics", "*.srt *.ass *.ssa *.lrc *.vtt"),
                ("SRT Subtitles (*.srt)", "*.srt"),
                ("ASS / SSA Subtitles (*.ass, *.ssa)", "*.ass *.ssa"),
                ("LRC Lyrics (*.lrc)", "*.lrc"),
                ("WebVTT (*.vtt)", "*.vtt"),
                ("All Files", "*.*")
            ]
        )
        if path:
            self.srt_path = path
            self.srt_entry.delete(0, "end")
            self.srt_entry.insert(0, path)
            self._render_visual_crop()
            self._update_cli_display()
            self._save_persistent_config()

    def _browse_music(self):
        path = filedialog.askopenfilename(
            title="Choose Background Music File",
            filetypes=[
                ("Audio Files", "*.mp3 *.wav *.m4a *.aac *.ogg *.flac *.opus *.wma"),
                ("All Files", "*.*")
            ]
        )
        if path:
            self.music_path = path
            self.music_entry.delete(0, "end")
            self.music_entry.insert(0, path)
            self.music_switch_var.set(True)
            if hasattr(self, "yt_music_status_label"):
                self.yt_music_status_label.configure(
                    text=f"✅ Loaded Music: {Path(path).name}",
                    text_color="#4ade80"
                )
            self._update_cli_display()
            self._save_persistent_config()

    def _on_music_entry_changed(self, event=None):
        path = self.music_entry.get().strip()
        if path and os.path.exists(path):
            self.music_path = path
            self.music_switch_var.set(True)
            if hasattr(self, "yt_music_status_label"):
                self.yt_music_status_label.configure(
                    text=f"✅ Loaded Music: {Path(path).name}",
                    text_color="#4ade80"
                )
        elif not path:
            self.music_path = None
        self._update_cli_display()
        self._save_persistent_config()

    # ==============================================================
    # SHORTS vs VIDEOS MODE SWITCHING & STATE SYNC
    # (Only Sections 1 & 2 change per mode. Sections 3 & 4 are Universal)
    # ==============================================================
    def _sync_to_active_config(self):
        """Persists mode-specific control values (1 & 2) into active profile and universal values (3 & 4) to disk."""
        cfg = self.shorts_config if self.active_mode == "shorts" else self.videos_config
        # Section 1: Source Video
        if hasattr(self, "video_path") and self.video_path:
            cfg["video_path"] = self.video_path
        # Section 2: Ratio & Crop Framing
        cfg["ratio_str"] = self.ratio_segmented.get() if hasattr(self, "ratio_segmented") else "9:16 (Shorts/Reels)"
        cfg["ratio_val"] = self.current_ratio
        cfg["scale"] = self.current_scale
        cfg["pos_x"] = self.current_pos_x
        cfg["pos_y"] = self.current_pos_y
        cfg["pad_top"] = self.current_pad_top
        cfg["pad_bottom"] = self.current_pad_bottom
        cfg["bottom_fade"] = self.bottom_fade_var.get()
        cfg["fade_height"] = self.fade_height
        cfg["fade_opacity"] = self.fade_opacity
        cfg["vol"] = self.current_vol
        self.shorts_config["vol"] = self.current_vol
        self.videos_config["vol"] = self.current_vol

        self._save_persistent_config()

    def _save_persistent_config(self):
        """Saves current state into clipper_config.json so it persists across restarts and folder runs."""
        try:
            cap_style = self.caption_style_menu.get() if hasattr(self, "caption_style_menu") else getattr(self, "saved_caption_style", "✨ Soothe White Light (Soft Clean Glow)")
            cap_font = self._get_selected_font_name()
            cap_size = self.size_menu.get() if hasattr(self, "size_menu") else getattr(self, "saved_font_size", "Medium (22pt)")
            cap_border = self._get_selected_caption_border()
            cap_pos = self.caption_pos_menu.get() if hasattr(self, "caption_pos_menu") else getattr(self, "saved_caption_pos", "Caption: On Video Fade (Above Padding)")
            sub_on = self.sub_switch_var.get() if hasattr(self, "sub_switch_var") else True
            audio_on = self.audio_switch_var.get() if hasattr(self, "audio_switch_var") else True
            audio_entry_val = self.audio_entry.get().strip() if hasattr(self, "audio_entry") else ""
            m_path_val = self.music_entry.get().strip() if hasattr(self, "music_entry") else getattr(self, "music_path", "") or ""
            music_on = self.music_switch_var.get() if hasattr(self, "music_switch_var") else True
            skip_on = self.skip_existing_var.get() if hasattr(self, "skip_existing_var") else True

            cfg = {
                "active_mode": self.active_mode,
                "shorts_config": copy.deepcopy(self.shorts_config),
                "videos_config": copy.deepcopy(self.videos_config),
                # Section 1: Paired High-Quality Audio & Main Video Volume
                "audio": {
                    "enabled": audio_on,
                    "path": audio_entry_val or getattr(self, "audio_path", "") or "",
                    "video_volume": getattr(self, "current_video_vol", 1.0),
                },
                # Section 3: Universal Subtitles Configuration
                "subtitles": {
                    "enabled": sub_on,
                    "path": getattr(self, "srt_path", "") or "",
                    "style": cap_style,
                    "font": cap_font,
                    "size": cap_size,
                    "border": cap_border,
                    "position": cap_pos,
                    "pos_y": getattr(self, "sub_pos_y", 0.15),
                    "overflow": self.caption_overflow_var.get() if hasattr(self, "caption_overflow_var") else getattr(self, "saved_caption_overflow", False)
                },
                # Section 4: Universal Music & Volume Configuration
                "music": {
                    "enabled": music_on,
                    "path": m_path_val,
                    "volume": self.current_vol
                },
                "skip_existing": skip_on,
                # YouTube Integration State (Saved across restarts)
                "youtube_config": {
                    "enabled": getattr(self, "yt_enabled", False),
                    "auto_captions": self.yt_auto_captions_var.get() if hasattr(self, "yt_auto_captions_var") else True,
                    "noise_removal": self.yt_noise_removal_var.get() if hasattr(self, "yt_noise_removal_var") else True,
                    "noise_intensity": getattr(self, "yt_noise_intensity", "Medium"),
                    "auto_delete_after_days": getattr(self, "yt_auto_delete_days", DEFAULT_EXPIRY_DAYS)
                }
            }
            save_saved_config(cfg)
        except Exception:
            pass

    def _switch_mode(self, new_mode: str):
        """Switches editing context between Shorts (<=3m) and Videos (>3m & full exports).
        Only Sections 1 & 2 change per mode. Sections 3 & 4 remain completely universal!
        """
        if self.active_mode == new_mode:
            return

        if hasattr(self, "yt_active_mode_lbl"):
            self.yt_active_mode_lbl.configure(text="Active Target: " + ("📱 Shorts" if new_mode == "shorts" else "🎬 Videos"))

        # 1. Save current controls into current profile
        self._sync_to_active_config()
        self.active_mode = new_mode

        # 2. Update pill buttons visual style & section labels
        if new_mode == "shorts":
            self.btn_mode_shorts.configure(fg_color="#cbd5e1", text_color="#0f172a", hover_color="#e2e8f0")
            self.btn_mode_videos.configure(fg_color="transparent", text_color="#94a3b8", hover_color="#242c3d")
            self.section_2_label.configure(text="2. Aspect Ratio & Crop Framing (📱 Shorts)")
            self.ratio_info_label.configure(
                text="💡 Selected ratio & framing apply to Shorts (≤3m). Clips >3m automatically use Videos settings."
            )
            self.btn_preview.configure(text="🎬 Generate 1-Min Preview (Shorts Format)")
            self.btn_preset_shifted.configure(text="🎬 Shift Up (8% / 14%)", command=self._preset_shifted_pad)
            self.btn_preset_centered.configure(text="↕️ Centered (10% / 10%)", command=self._preset_centered_pad)
            self.btn_preset_zero.configure(text="Full (0%)", command=self._reset_padding)
            cfg = self.shorts_config
        else:
            self.btn_mode_videos.configure(fg_color="#cbd5e1", text_color="#0f172a", hover_color="#e2e8f0")
            self.btn_mode_shorts.configure(fg_color="transparent", text_color="#94a3b8", hover_color="#242c3d")
            self.section_2_label.configure(text="2. Aspect Ratio & Crop Framing (🎬 Videos)")
            self.ratio_info_label.configure(
                text="💡 Selected ratio & framing apply to Videos (>3m & full exports). Clips ≤3m automatically use Shorts settings."
            )
            self.btn_preview.configure(text="🎬 Generate 1-Min Preview (Videos Format)")
            self.btn_preset_shifted.configure(text="📺 Full 16:9 (0%)", command=self._reset_padding)
            self.btn_preset_centered.configure(text="↕️ Letterbox (6% / 6%)", command=lambda: self._set_custom_padding(6.0, 6.0))
            self.btn_preset_zero.configure(text="🎬 Cinema (12% / 12%)", command=lambda: self._set_custom_padding(12.0, 12.0))
            cfg = self.videos_config

        # 3. Section 1 (Source Video): keep current video if valid; otherwise restore saved video
        if not (self.video_path and os.path.exists(self.video_path)):
            mode_video = cfg.get("video_path")
            if mode_video and os.path.exists(mode_video):
                self._load_video(mode_video)

        # 4. Section 2 (Aspect Ratio & Crop Framing): apply mode profile
        self.current_ratio = cfg["ratio_val"]
        self.ratio_segmented.set(cfg["ratio_str"])

        self.current_scale = cfg["scale"]
        self.scale_slider.set(self.current_scale)
        pct = int(round(self.current_scale * 100))
        if self.current_scale == 1.0:
            desc = "1.00x (Standard Fill 100%)"
        elif self.current_scale > 1.0:
            desc = f"{self.current_scale:.2f}x (Scale Up / Zoom {pct}%)"
        else:
            desc = f"{self.current_scale:.2f}x (Scale Down / Fit {pct}%)"
        self.scale_label_var.set(f"Scale / Zoom: {desc}")

        self.current_pos_x = cfg["pos_x"]
        self.pos_slider.set(self.current_pos_x)
        pct_x = int(round(self.current_pos_x * 100))
        lbl_x = "Center (50%)" if pct_x == 50 else (f"Left ({pct_x}%)" if pct_x < 50 else f"Right ({pct_x}%)")
        self.pos_label_var.set(f"Horizontal Framing (X): {lbl_x}")

        self.current_pos_y = cfg["pos_y"]
        self.pos_y_slider.set(self.current_pos_y)
        pct_y = int(round(self.current_pos_y * 100))
        lbl_y = "Center (50%)" if pct_y == 50 else (f"Top ({pct_y}%)" if pct_y < 50 else f"Bottom ({pct_y}%)")
        self.pos_y_label_var.set(f"Vertical Framing (Y): {lbl_y}")

        self.current_pad_top = cfg["pad_top"]
        self.pad_top_slider.set(self.current_pad_top)
        self.pad_top_label_var.set(f"Top Padding: {self.current_pad_top:.1f}%")

        self.current_pad_bottom = cfg["pad_bottom"]
        self.pad_bottom_slider.set(self.current_pad_bottom)
        self.pad_bottom_label_var.set(f"Bottom Padding: {self.current_pad_bottom:.1f}%")

        self.bottom_fade_var.set(cfg["bottom_fade"])
        self.fade_height = cfg.get("fade_height", 0.38)
        self.fade_opacity = cfg.get("fade_opacity", 0.92)
        if hasattr(self, "fade_height_slider"):
            self.fade_height_slider.set(self.fade_height)
            self.fade_height_label_var.set(f"Fade Spread: {int(round(self.fade_height * 100))}% (Coverage)")
        if hasattr(self, "fade_opacity_slider"):
            self.fade_opacity_slider.set(self.fade_opacity)
            self.fade_opacity_label_var.set(f"Fade Darkness: {int(round(self.fade_opacity * 100))}% (Contrast)")
        if hasattr(self, "fade_ctrl_frame"):
            if cfg["bottom_fade"]:
                self.fade_ctrl_frame.pack(fill="x", padx=10, pady=(0, 8))
            else:
                self.fade_ctrl_frame.pack_forget()

        # NOTE: Sections 3 (Subtitles) & 4 (Music & Volume) are UNIVERSAL and NOT touched here!
        # They stay completely intact and synced across Shorts, Videos, and 1-Click!

        # 5. Redraw preview & refresh CLI command
        self._render_visual_crop()
        self._update_cli_display()
        self._on_batch_text_changed()
        self._update_scroll_region()

    def _update_scroll_region(self):
        try:
            self.after(50, lambda: self.left_frame._parent_canvas.configure(
                scrollregion=self.left_frame._parent_canvas.bbox("all")
            ))
        except Exception:
            pass

    def _show_auto_info(self):
        messagebox.showinfo(
            "✨ Auto Mode Active",
            "Auto-Detection is always active during batch processing:\n\n"
            "• Clips ≤ 3 minutes automatically use your SHORTS settings (9:16 ratio, zoom, pan, padding & fade).\n\n"
            "• Clips > 3 minutes automatically use your VIDEOS settings (16:9 ratio, custom framing & padding).\n\n"
            "Switch between 'Shorts' and 'Videos' tabs above to customize both independently!"
        )

    # ==============================================================
    # CROP RATIO & POSITION UPDATES
    # ==============================================================
    def _on_ratio_changed(self, value: str):
        if "9:16" in value:
            self.current_ratio = "9:16"
        elif "1:1" in value:
            self.current_ratio = "1:1"
        elif "4:5" in value:
            self.current_ratio = "4:5"
        else:
            self.current_ratio = "16:9"

        self._sync_to_active_config()
        self._render_visual_crop()
        self._update_cli_display()
        self._update_scroll_region()
        self._on_batch_text_changed()

    def _scale_step_minus(self):
        new_val = max(0.5, min(3.0, round(self.scale_slider.get() - 0.05, 2)))
        self.scale_slider.set(new_val)
        self._on_scale_slider_changed(new_val)

    def _scale_step_plus(self):
        new_val = max(0.5, min(3.0, round(self.scale_slider.get() + 0.05, 2)))
        self.scale_slider.set(new_val)
        self._on_scale_slider_changed(new_val)

    def _pos_step_minus(self):
        new_val = max(0.0, min(1.0, round(self.pos_slider.get() - 0.01, 2)))
        self.pos_slider.set(new_val)
        self._on_pos_slider_changed(new_val)

    def _pos_step_plus(self):
        new_val = max(0.0, min(1.0, round(self.pos_slider.get() + 0.01, 2)))
        self.pos_slider.set(new_val)
        self._on_pos_slider_changed(new_val)

    def _pos_y_step_minus(self):
        new_val = max(0.0, min(1.0, round(self.pos_y_slider.get() - 0.01, 2)))
        self.pos_y_slider.set(new_val)
        self._on_pos_y_slider_changed(new_val)

    def _pos_y_step_plus(self):
        new_val = max(0.0, min(1.0, round(self.pos_y_slider.get() + 0.01, 2)))
        self.pos_y_slider.set(new_val)
        self._on_pos_y_slider_changed(new_val)

    def _reset_scale(self):
        self.scale_slider.set(1.0)
        self._on_scale_slider_changed(1.0)

    def _reset_pos_x(self):
        self.pos_slider.set(0.5)
        self._on_pos_slider_changed(0.5)

    def _reset_pos_y(self):
        self.pos_y_slider.set(0.5)
        self._on_pos_y_slider_changed(0.5)

    def _preset_shifted_pad(self):
        """Sets top padding to 8% and bottom padding to 14% to shift clip upward like reference Reel."""
        self._set_custom_padding(8.0, 14.0)

    def _preset_centered_pad(self):
        """Sets balanced 10% top and 10% bottom padding."""
        self._set_custom_padding(10.0, 10.0)

    def _reset_padding(self):
        """Resets padding to 0%."""
        self._set_custom_padding(0.0, 0.0)

    def _set_custom_padding(self, top: float, bottom: float):
        self.current_pad_top = float(top)
        self.current_pad_bottom = float(bottom)
        self.pad_top_slider.set(self.current_pad_top)
        self.pad_bottom_slider.set(self.current_pad_bottom)
        self.pad_top_label_var.set(f"Top Padding: {self.current_pad_top:.1f}%")
        self.pad_bottom_label_var.set(f"Bottom Padding: {self.current_pad_bottom:.1f}%")
        self._sync_to_active_config()
        self._render_visual_crop()
        self._update_cli_display()
        self._update_scroll_region()

    def _on_pad_top_slider_changed(self, value: float):
        self.current_pad_top = round(float(value), 1)
        self.pad_top_label_var.set(f"Top Padding: {self.current_pad_top:.1f}%")
        self._sync_to_active_config()
        self._render_visual_crop()
        self._update_cli_display()
        self._update_scroll_region()

    def _on_pad_bottom_slider_changed(self, value: float):
        self.current_pad_bottom = round(float(value), 1)
        self.pad_bottom_label_var.set(f"Bottom Padding: {self.current_pad_bottom:.1f}%")
        self._sync_to_active_config()
        self._render_visual_crop()
        self._update_cli_display()
        self._update_scroll_region()

    def _pad_top_step_minus(self):
        val = max(0.0, round(self.current_pad_top - 1.0, 1))
        self.pad_top_slider.set(val)
        self._on_pad_top_slider_changed(val)

    def _pad_top_step_plus(self):
        val = min(30.0, round(self.current_pad_top + 1.0, 1))
        self.pad_top_slider.set(val)
        self._on_pad_top_slider_changed(val)

    def _pad_bottom_step_minus(self):
        val = max(0.0, round(self.current_pad_bottom - 1.0, 1))
        self.pad_bottom_slider.set(val)
        self._on_pad_bottom_slider_changed(val)

    def _pad_bottom_step_plus(self):
        val = min(40.0, round(self.current_pad_bottom + 1.0, 1))
        self.pad_bottom_slider.set(val)
        self._on_pad_bottom_slider_changed(val)

    def _on_preview_mode_changed(self, mode: str):
        if "Cutout" in mode:
            self.preview_mode = "source"
        else:
            self.preview_mode = "reel"
        self._render_visual_crop()

    def _on_fade_switch_changed(self):
        is_on = self.bottom_fade_var.get()
        if hasattr(self, "fade_ctrl_frame"):
            if is_on:
                self.fade_ctrl_frame.pack(fill="x", padx=10, pady=(0, 8))
            else:
                self.fade_ctrl_frame.pack_forget()
        self._sync_to_active_config()
        self._render_visual_crop()
        self._update_cli_display()
        self._update_scroll_region()

    def _on_fade_height_slider_changed(self, val):
        self.fade_height = round(float(val), 2)
        pct = int(round(self.fade_height * 100))
        self.fade_height_label_var.set(f"Fade Spread: {pct}% (Coverage)")
        self._sync_to_active_config()
        self._render_visual_crop()
        self._update_cli_display()
        self._update_scroll_region()

    def _fade_height_step_minus(self):
        new_val = max(0.15, round(self.fade_height - 0.03, 2))
        self.fade_height_slider.set(new_val)
        self._on_fade_height_slider_changed(new_val)

    def _fade_height_step_plus(self):
        new_val = min(0.70, round(self.fade_height + 0.03, 2))
        self.fade_height_slider.set(new_val)
        self._on_fade_height_slider_changed(new_val)

    def _on_fade_opacity_slider_changed(self, val):
        self.fade_opacity = round(float(val), 2)
        pct = int(round(self.fade_opacity * 100))
        self.fade_opacity_label_var.set(f"Fade Darkness: {pct}% (Contrast)")
        self._sync_to_active_config()
        self._render_visual_crop()
        self._update_cli_display()
        self._update_scroll_region()

    def _fade_opacity_step_minus(self):
        new_val = max(0.30, round(self.fade_opacity - 0.05, 2))
        self.fade_opacity_slider.set(new_val)
        self._on_fade_opacity_slider_changed(new_val)

    def _fade_opacity_step_plus(self):
        new_val = min(1.0, round(self.fade_opacity + 0.05, 2))
        self.fade_opacity_slider.set(new_val)
        self._on_fade_opacity_slider_changed(new_val)

    def _set_fade_preset(self, height: float, opacity: float):
        self.fade_height = round(height, 2)
        self.fade_opacity = round(opacity, 2)
        if hasattr(self, "fade_height_slider"):
            self.fade_height_slider.set(self.fade_height)
            self.fade_height_label_var.set(f"Fade Spread: {int(round(self.fade_height * 100))}% (Coverage)")
        if hasattr(self, "fade_opacity_slider"):
            self.fade_opacity_slider.set(self.fade_opacity)
            self.fade_opacity_label_var.set(f"Fade Darkness: {int(round(self.fade_opacity * 100))}% (Contrast)")
        self._sync_to_active_config()
        self._render_visual_crop()
        self._update_cli_display()
        self._update_scroll_region()

    def _on_caption_style_changed(self, style_name: str):
        self._render_visual_crop()
        self._update_cli_display()
        self._save_persistent_config()

    def _get_selected_font_size(self) -> int:
        val = self.size_menu.get() if hasattr(self, "size_menu") else "22"
        digits = ''.join(c for c in val if c.isdigit())
        if digits:
            try:
                sz = int(digits)
                if 10 <= sz <= 72:
                    return sz
            except Exception:
                pass
        return 22

    def _get_selected_font_name(self) -> str:
        if hasattr(self, "font_menu"):
            return self.font_menu.get().strip()
        return getattr(self, "saved_font_name", "Kohinoor Devanagari")

    def _get_selected_caption_border(self) -> str:
        if hasattr(self, "border_menu"):
            return self.border_menu.get().strip()
        return getattr(self, "saved_caption_border", "Auto (Preset Default)")

    def _step_font_size(self, delta: int):
        cur_sz = self._get_selected_font_size()
        new_sz = max(12, min(50, cur_sz + delta))

        matching_label = None
        for opt in FONT_SIZE_OPTIONS:
            if f"{new_sz}pt" in opt:
                matching_label = opt
                break
        if not matching_label:
            matching_label = f"{new_sz}pt"
            vals = list(self.size_menu.cget("values"))
            if matching_label not in vals:
                vals.append(matching_label)
                vals.sort(key=lambda s: int(''.join(c for c in s if c.isdigit()) or 0))
                self.size_menu.configure(values=vals)

        self.size_menu.set(matching_label)
        self._render_visual_crop()
        self._update_cli_display()
        self._save_persistent_config()

    def _on_sub_pos_slider_changed(self, value, refresh=True):
        self.sub_pos_y = round(float(value), 3)
        pct = int(round(self.sub_pos_y * 100))
        self.sub_pos_label_var.set(f"Vertical Position (Y): {pct}%")
        self._sync_sub_pos_menu_preset(pct)
        if refresh:
            self._render_visual_crop()
            self._update_cli_display()
            self._save_persistent_config()

    def _step_sub_pos_minus(self):
        new_val = max(0.0, round(self.sub_pos_y - 0.01, 3))
        self.sub_pos_slider.set(new_val)
        self._on_sub_pos_slider_changed(new_val)

    def _step_sub_pos_plus(self):
        new_val = min(0.90, round(self.sub_pos_y + 0.01, 3))
        self.sub_pos_slider.set(new_val)
        self._on_sub_pos_slider_changed(new_val)

    def _on_sub_pos_preset_selected(self, choice: str):
        if "15%" in choice or "Above Padding" in choice:
            target = 0.15
        elif "6%" in choice or "Inside Bottom" in choice:
            target = 0.06
        elif "50%" in choice or "Center" in choice:
            target = 0.50
        elif "75%" in choice or "Upper" in choice:
            target = 0.75
        elif "85%" in choice or "Top" in choice:
            target = 0.85
        else:
            target = self.sub_pos_y
        self.sub_pos_slider.set(target)
        self._on_sub_pos_slider_changed(target)

    def _sync_sub_pos_menu_preset(self, pct: int):
        if not hasattr(self, "sub_pos_menu"):
            return
        if pct == 15:
            self.sub_pos_menu.set("✨ Above Padding (15%)")
        elif pct == 6:
            self.sub_pos_menu.set("🖤 Inside Bottom Bar (6%)")
        elif pct == 50:
            self.sub_pos_menu.set("🎯 Screen Center (50%)")
        elif pct == 75:
            self.sub_pos_menu.set("⬆️ Upper Third (75%)")
        elif pct == 85:
            self.sub_pos_menu.set("🔝 Top Edge (85%)")
        else:
            self.sub_pos_menu.set("⚙️ Custom Position")

    def _on_caption_overflow_toggle(self):
        is_overflow = bool(self.caption_overflow_var.get())
        self._update_caption_overflow_ui(is_overflow)

    def _set_caption_overflow(self, overflow: bool):
        self.caption_overflow_var.set(overflow)
        self._update_caption_overflow_ui(overflow)

    def _update_caption_overflow_ui(self, overflow: bool):
        if hasattr(self, "overflow_label_var"):
            self.overflow_label_var.set(
                "Caption Flow: ⋯ Overflow (Single Line)" if overflow else "Caption Flow: ⤹ Word Wrap (Multi-Line)"
            )
        if hasattr(self, "btn_flow_wrap") and hasattr(self, "btn_flow_overflow"):
            if overflow:
                self.btn_flow_overflow.configure(fg_color="#2563eb", hover_color="#1d4ed8")
                self.btn_flow_wrap.configure(fg_color="#2b3245", hover_color="#3b445c")
            else:
                self.btn_flow_wrap.configure(fg_color="#2563eb", hover_color="#1d4ed8")
                self.btn_flow_overflow.configure(fg_color="#2b3245", hover_color="#3b445c")
        self._render_visual_crop()
        self._update_cli_display()
        self._save_persistent_config()

    def _video_vol_step_minus(self):
        new_val = max(0.0, min(3.0, round(self.video_vol_slider.get() - 0.05, 2)))
        self.video_vol_slider.set(new_val)
        self._on_video_vol_slider_changed(new_val)

    def _video_vol_step_plus(self):
        new_val = max(0.0, min(3.0, round(self.video_vol_slider.get() + 0.05, 2)))
        self.video_vol_slider.set(new_val)
        self._on_video_vol_slider_changed(new_val)

    def _set_video_vol(self, val: float):
        val = max(0.0, min(3.0, round(val, 2)))
        self.video_vol_slider.set(val)
        self._on_video_vol_slider_changed(val)

    def _vol_step_minus(self):
        new_val = max(0.0, min(0.5, round(self.vol_slider.get() - 0.01, 2)))
        self.vol_slider.set(new_val)
        self._on_vol_slider_changed(new_val)

    def _vol_step_plus(self):
        new_val = max(0.0, min(0.5, round(self.vol_slider.get() + 0.01, 2)))
        self.vol_slider.set(new_val)
        self._on_vol_slider_changed(new_val)

    def _attach_button_repeat(self, btn, step_fn):
        """Attaches click and hold-to-repeat behavior to a button."""
        timer_ref = [None]
        held_flag = [False]

        def _repeat():
            held_flag[0] = True
            step_fn()
            timer_ref[0] = self.after(50, _repeat)

        def _on_press(event):
            held_flag[0] = False
            timer_ref[0] = self.after(350, _repeat)

        def _on_release(event):
            if timer_ref[0] is not None:
                self.after_cancel(timer_ref[0])
                timer_ref[0] = None

        def _on_click():
            if not held_flag[0]:
                step_fn()
            held_flag[0] = False

        btn.configure(command=_on_click)
        btn.bind("<Button-1>", _on_press, add="+")
        btn.bind("<ButtonRelease-1>", _on_release, add="+")
        btn.bind("<Leave>", _on_release, add="+")

    def _disable_slider_scroll(self, slider, scroll_target=None):
        """Disables mouse wheel/trackpad scrolling on slider to prevent accidental value jumps.
        Instead, forwards the scroll gesture to the scrollable container."""
        try:
            slider._canvas.unbind("<MouseWheel>")
        except Exception:
            pass

        if sys.platform.startswith("linux"):
            try:
                slider._canvas.unbind("<Button-4>")
                slider._canvas.unbind("<Button-5>")
            except Exception:
                pass

        slider._mouse_scroll_event = lambda event: None

        if scroll_target is not None:
            def _forward_scroll(event):
                try:
                    if sys.platform == "darwin":
                        scroll_target._parent_canvas.yview("scroll", -event.delta, "units")
                    elif sys.platform.startswith("win"):
                        scroll_target._parent_canvas.yview("scroll", -int(event.delta / 6), "units")
                    else:
                        scroll_target._parent_canvas.yview_scroll(-1 if event.num == 4 else 1, "units")
                except Exception:
                    pass
                return "break"

            slider._canvas.bind("<MouseWheel>", _forward_scroll)
            slider.bind("<MouseWheel>", _forward_scroll)
            if sys.platform.startswith("linux"):
                slider._canvas.bind("<Button-4>", _forward_scroll)
                slider._canvas.bind("<Button-5>", _forward_scroll)

    def _on_preview_drag_start(self, event):
        if not self.video_info or not hasattr(self, "_preview_geom"):
            return
        self._drag_start_x = event.x
        self._drag_start_y = event.y
        self._drag_start_pos_x = self.current_pos_x
        self._drag_start_pos_y = self.current_pos_y

    def _on_preview_drag_motion(self, event):

        if not self.video_info or not hasattr(self, "_preview_geom"):
            return
        geom = self._preview_geom
        mode = geom.get("mode", "reel")
        in_w = geom["in_w"]
        in_h = geom["in_h"]
        crop_w = geom["crop_w"]
        crop_h = geom["crop_h"]

        if mode == "reel":
            dx = event.x - self._drag_start_x
            dy = event.y - self._drag_start_y
            d_pos_x = -dx / 120.0
            d_pos_y = -dy / 120.0
            new_pos_x = max(0.0, min(1.0, round(self._drag_start_pos_x + d_pos_x, 3)))
            new_pos_y = max(0.0, min(1.0, round(self._drag_start_pos_y + d_pos_y, 3)))
            if new_pos_x != self.current_pos_x:
                self.pos_slider.set(new_pos_x)
                self._on_pos_slider_changed(new_pos_x, refresh=False)
            if new_pos_y != self.current_pos_y:
                self.pos_y_slider.set(new_pos_y)
                self._on_pos_y_slider_changed(new_pos_y, refresh=False)
        else:
            target_w = geom.get("target_w", 480)
            target_h = geom.get("target_h", 260)
            scale_x = target_w / in_w
            scale_y = target_h / in_h

            max_travel_px_x = (in_w - crop_w) * scale_x
            if max_travel_px_x > 0:
                dx = event.x - self._drag_start_x
                d_pos_x = dx / max_travel_px_x
                new_pos_x = max(0.0, min(1.0, round(self._drag_start_pos_x + d_pos_x, 3)))
                if new_pos_x != self.current_pos_x:
                    self.pos_slider.set(new_pos_x)
                    self._on_pos_slider_changed(new_pos_x, refresh=False)

            max_travel_px_y = (in_h - crop_h) * scale_y
            if max_travel_px_y > 0:
                dy = event.y - self._drag_start_y
                d_pos_y = dy / max_travel_px_y
                new_pos_y = max(0.0, min(1.0, round(self._drag_start_pos_y + d_pos_y, 3)))
                if new_pos_y != self.current_pos_y:
                    self.pos_y_slider.set(new_pos_y)
                    self._on_pos_y_slider_changed(new_pos_y, refresh=False)

        self._render_visual_crop()
        self._update_cli_display()

    def _on_canvas_mousewheel(self, event):
        delta = event.delta
        if delta == 0:
            return "break"
        step = 0.05 if delta > 0 else -0.05
        new_scale = max(0.5, min(3.0, round(self.current_scale + step, 2)))
        if new_scale != self.current_scale:
            self.scale_slider.set(new_scale)
            self._on_scale_slider_changed(new_scale)
        return "break"

    def _on_scale_slider_changed(self, value: float):
        self.current_scale = round(float(value), 2)
        pct = int(round(self.current_scale * 100))
        if self.current_scale == 1.0:
            desc = "1.00x (Standard Fill 100%)"
        elif self.current_scale > 1.0:
            desc = f"{self.current_scale:.2f}x (Scale Up / Zoom {pct}%)"
        else:
            desc = f"{self.current_scale:.2f}x (Scale Down / Fit {pct}%)"
        self.scale_label_var.set(f"Scale / Zoom: {desc}")
        self._sync_to_active_config()
        self._render_visual_crop()
        self._update_cli_display()
        self._update_scroll_region()

    def _on_pos_slider_changed(self, value: float, refresh=True):
        self.current_pos_x = round(float(value), 3)
        pct = int(round(self.current_pos_x * 100))
        label = "Center (50%)"
        if pct < 50:
            label = f"Left ({pct}%)"
        elif pct > 50:
            label = f"Right ({pct}%)"
        self.pos_label_var.set(f"Horizontal Framing (X): {label}")
        self._sync_to_active_config()

        if refresh:
            self._render_visual_crop()
            self._update_cli_display()

    def _on_pos_y_slider_changed(self, value: float, refresh=True):
        self.current_pos_y = round(float(value), 3)
        pct = int(round(self.current_pos_y * 100))
        label = "Center (50%)"
        if pct < 50:
            label = f"Top ({pct}%)"
        elif pct > 50:
            label = f"Bottom ({pct}%)"
        self.pos_y_label_var.set(f"Vertical Framing (Y): {label}")
        self._sync_to_active_config()

        if refresh:
            self._render_visual_crop()
            self._update_cli_display()

    def _on_video_vol_slider_changed(self, value: float):
        self.current_video_vol = round(float(value), 2)
        pct = int(round(self.current_video_vol * 100))
        if self.current_video_vol == 0:
            lbl = "🔇 Main Video Sound: Muted (0%)"
        elif abs(self.current_video_vol - 1.0) < 0.01:
            lbl = "🔊 Main Video Sound: 100% (Normal)"
        elif self.current_video_vol > 1.0:
            lbl = f"🔊 Main Video Sound: {pct}% ({self.current_video_vol:.2f}x Boost 🚀)"
        else:
            lbl = f"🔉 Main Video Sound: {pct}% ({self.current_video_vol:.2f}x)"
        self.video_vol_label_var.set(lbl)
        self._save_persistent_config()
        self._update_cli_display()

    def _on_vol_slider_changed(self, value: float):
        self.current_vol = float(value)
        pct = int(round(self.current_vol * 100))
        self.vol_label_var.set(f"Background Volume: {pct}% (Speech stays crisp)")
        self.shorts_config["vol"] = self.current_vol
        self.videos_config["vol"] = self.current_vol
        self._sync_to_active_config()
        self._update_cli_display()

    def _render_visual_crop(self):
        """Draws visual crop dynamically supporting Shorts (9:16, 1:1, 4:5) and Videos (16:9, etc.)."""
        target_display_w = 480
        target_display_h = 260

        if not self.video_info:
            return

        in_w = self.video_info.get("width", 1920)
        in_h = self.video_info.get("height", 1080)
        cur_ratio = self.current_ratio
        cur_pos_x = self.current_pos_x
        cur_pos_y = self.current_pos_y
        cur_scale = self.current_scale
        cur_pad_t = self.current_pad_top
        cur_pad_b = self.current_pad_bottom
        bottom_fade_on = self.bottom_fade_var.get()
        fade_h_val = self.fade_height
        fade_o_val = self.fade_opacity
        cap_style_val = self.caption_style_menu.get() if hasattr(self, "caption_style_menu") else "✨ Soothe White Light (Soft Clean Glow)"
        cap_font_val = self._get_selected_font_name()
        cap_border_val = self._get_selected_caption_border()
        sub_y_val = getattr(self, "sub_pos_y", 0.15)
        base_frame = self.base_frame_image
        mode_label = "📱 Shorts" if self.active_mode == "shorts" else "🎬 Videos"

        crop_filter, crop_w, crop_h, off_x, off_y = compute_crop_filter(
            in_w=in_w,
            in_h=in_h,
            ratio=cur_ratio,
            pos_x=cur_pos_x,
            pos_y=cur_pos_y,
            scale=cur_scale,
            pad_top=cur_pad_t,
            pad_bottom=cur_pad_b
        )

        fade_status = "ON (🖤)" if bottom_fade_on else "OFF"
        pad_status = f"Pad: T:{cur_pad_t:.1f}% B:{cur_pad_b:.1f}%"
        self.filter_info_label.configure(
            text=f"Mode: {mode_label} ({cur_ratio}) | FFmpeg: {crop_filter}\nFraming: Scale {cur_scale:.2f}x | X {int(cur_pos_x*100)}% | Y {int(cur_pos_y*100)}% | {pad_status} | Fade: {fade_status}"
        )

        target_display_w = 480
        target_display_h = 260

        if self.preview_mode == "reel":
            # --- FINAL FRAMED VIEW (Shorts or Videos) ---
            bg = Image.new("RGBA", (target_display_w, target_display_h), (18, 22, 32, 255))
            d_bg = ImageDraw.Draw(bg)

            # Determine target aspect ratio
            r_str = str(cur_ratio).strip().lower()
            if r_str in ["16:9", "original", "none", "full"]:
                target_aspect = 16.0 / 9.0
            elif r_str == "9:16":
                target_aspect = 9.0 / 16.0
            elif r_str == "1:1":
                target_aspect = 1.0
            elif r_str == "4:5":
                target_aspect = 4.0 / 5.0
            elif ":" in r_str:
                try:
                    rw, rh = r_str.split(":")
                    target_aspect = float(rw) / float(rh)
                except Exception:
                    target_aspect = 16.0 / 9.0 if self.active_mode == "videos" else 9.0 / 16.0
            else:
                target_aspect = 16.0 / 9.0 if self.active_mode == "videos" else 9.0 / 16.0

            canvas_aspect = target_display_w / target_display_h
            if target_aspect <= canvas_aspect:
                frame_h = 252
                frame_w = int(round(frame_h * target_aspect))
                frame_w -= (frame_w % 2)
            else:
                frame_w = target_display_w - 24
                frame_h = int(round(frame_w / target_aspect))
                frame_h -= (frame_h % 2)

            rx1 = (target_display_w - frame_w) // 2
            rx2 = rx1 + frame_w
            ry1 = (target_display_h - frame_h) // 2
            ry2 = ry1 + frame_h

            pad_t_px = int(round(frame_h * (cur_pad_t / 100.0)))
            pad_b_px = int(round(frame_h * (cur_pad_b / 100.0)))
            vid_disp_h = max(24, frame_h - pad_t_px - pad_b_px)

            self._preview_geom = {
                "mode": "reel",
                "in_w": in_w,
                "in_h": in_h,
                "crop_w": crop_w,
                "crop_h": crop_h,
                "rx1": rx1,
                "rx2": rx2,
                "ry1": ry1,
                "ry2": ry2,
                "target_w": frame_w,
                "target_h": frame_h
            }

            d_bg.rectangle([rx1, ry1, rx2, ry2], fill=(0, 0, 0, 255))

            in_aspect = in_w / in_h
            if in_aspect > target_aspect:
                base_h = in_h
                base_w = int(round(in_h * target_aspect))
            else:
                base_w = in_w
                base_h = int(round(in_w / target_aspect))
            base_w -= (base_w % 2)
            base_h -= (base_h % 2)

            pad_t_calc = int(round(base_h * (cur_pad_t / 100.0)))
            pad_b_calc = int(round(base_h * (cur_pad_b / 100.0)))
            avail_w = base_w
            avail_h = max(64, base_h - pad_t_calc - pad_b_calc)
            sub_aspect = avail_w / avail_h

            if in_aspect > sub_aspect:
                crop_base_h = in_h
                crop_base_w = int(round(in_h * sub_aspect))
            else:
                crop_base_w = in_w
                crop_base_h = int(round(in_w / sub_aspect))
            crop_base_w -= (crop_base_w % 2)
            crop_base_h -= (crop_base_h % 2)

            if base_frame:
                crop_box = (off_x, off_y, off_x + crop_w, off_y + crop_h)
                req_w = crop_base_w / cur_scale
                req_h = crop_base_h / cur_scale
                scale_fit = min(frame_w / req_w, vid_disp_h / req_h)
                ui_disp_w = max(16, min(frame_w, int(round(crop_w * scale_fit))))
                ui_disp_h = max(16, min(vid_disp_h, int(round(crop_h * scale_fit))))
                ui_disp_w -= (ui_disp_w % 2)
                ui_disp_h -= (ui_disp_h % 2)

                try:
                    cropped_frame = base_frame.crop(crop_box).resize((ui_disp_w, ui_disp_h), Image.Resampling.LANCZOS)
                except Exception:
                    cropped_frame = base_frame.resize((ui_disp_w, ui_disp_h), Image.Resampling.LANCZOS)

                ui_pad_x = max(0, int(round((frame_w - ui_disp_w) * cur_pos_x)))
                ui_pad_y = max(0, int(round((vid_disp_h - ui_disp_h) * cur_pos_y)))
                ui_pad_x -= (ui_pad_x % 2)
                ui_pad_y -= (ui_pad_y % 2)

                bg.paste(cropped_frame, (rx1 + ui_pad_x, ry1 + pad_t_px + ui_pad_y))
            else:
                cropped_frame = Image.new("RGBA", (frame_w, vid_disp_h), (40, 50, 70, 255))
                bg.paste(cropped_frame, (rx1, ry1 + pad_t_px))

            # Draw bottom black fade gradient if enabled
            if bottom_fade_on and vid_disp_h > 20:
                fade_h = max(6, int(vid_disp_h * fade_h_val))
                fade_top = vid_disp_h - fade_h
                col = Image.new("RGBA", (1, fade_h), (0, 0, 0, 0))
                for y_idx in range(fade_h):
                    norm = y_idx / max(1, fade_h - 1)
                    alpha = int(255 * fade_o_val * (norm ** 1.35))
                    col.putpixel((0, y_idx), (0, 0, 0, min(255, max(0, alpha))))
                fade_img = col.resize((frame_w, fade_h), Image.Resampling.NEAREST)
                bg.paste(fade_img, (rx1, ry1 + pad_t_px + fade_top), fade_img)

            # Draw sample caption text on preview
            draw_cap = ImageDraw.Draw(bg)
            sample_text = "सिद्धि तो है। हम बढ़ रहे हैं"
            is_overflow = bool(self.caption_overflow_var.get()) if hasattr(self, "caption_overflow_var") else False

            font_nm = self._get_selected_font_name()
            font_sz = self._get_selected_font_size()
            cap_border = self._get_selected_caption_border()

            # Proportionally scale font size to preview frame height (base 22pt -> ~13pt at frame_h=252)
            preview_font_sz = max(8, min(36, int(round(font_sz * (frame_h / 420.0)))))
            cap_font = get_pillow_font(font_nm, preview_font_sz)
            if not cap_font:
                try:
                    cap_font = ImageFont.load_default()
                except Exception:
                    cap_font = None

            # Split lines depending on Overflow mode vs Word Wrap mode
            if is_overflow:
                text_lines = [sample_text]
            else:
                words = sample_text.split()
                if len(words) >= 4:
                    mid = len(words) // 2
                    text_lines = [" ".join(words[:mid]), " ".join(words[mid:])]
                else:
                    text_lines = [sample_text]

            line_metrics = []
            max_line_w = 0
            for line_str in text_lines:
                try:
                    bbox = draw_cap.textbbox((0, 0), line_str, font=cap_font)
                    lw = bbox[2] - bbox[0]
                    lh = bbox[3] - bbox[1]
                except Exception:
                    lw = len(line_str) * int(preview_font_sz * 0.6)
                    lh = preview_font_sz + 4
                line_metrics.append((lw, lh))
                if lw > max_line_w:
                    max_line_w = lw

            line_step = preview_font_sz + 4
            total_th = len(text_lines) * line_step
            sub_pos_frac = max(0.0, min(0.92, sub_y_val))
            dist_from_bottom_px = int(round(frame_h * sub_pos_frac))
            base_ty = ry1 + frame_h - dist_from_bottom_px - total_th
            base_ty = max(ry1 + 4, min(ry1 + frame_h - total_th - 4, base_ty))

            style_sel = cap_style_val

            # Primary text color
            if "Yellow" in style_sel:
                text_col = (255, 240, 0, 255)
            elif "Cyan" in style_sel or "Aqua" in style_sel:
                text_col = (34, 211, 238, 255)
            elif "Golden" in style_sel:
                text_col = (255, 215, 58, 255)
            elif "Lime" in style_sel:
                text_col = (126, 255, 50, 255)
            else:
                text_col = (255, 255, 255, 255)

            # Determine border styling
            cb_lower = cap_border.lower()
            is_box = "box" in cb_lower or "pill" in cb_lower or ("auto" in cb_lower and "pill box" in style_sel.lower())
            is_no_border = "no border" in cb_lower or "none" in cb_lower
            is_small = "small" in cb_lower or "thin" in cb_lower or "1.0" in cb_lower
            is_bold = "bold" in cb_lower or "thick" in cb_lower or "3.2" in cb_lower
            is_heavy = "heavy" in cb_lower or "extra thick" in cb_lower or "4.5" in cb_lower

            for l_idx, line_str in enumerate(text_lines):
                lw, lh = line_metrics[l_idx]
                tx = rx1 + max(2, (frame_w - lw) // 2)
                ty = base_ty + l_idx * line_step

                if is_box:
                    pill_pad_x = max(6, int(preview_font_sz * 0.6))
                    pill_pad_y = max(3, int(preview_font_sz * 0.25))
                    draw_cap.rounded_rectangle(
                        [tx - pill_pad_x, ty - pill_pad_y, tx + lw + pill_pad_x, ty + lh + pill_pad_y + 2],
                        radius=max(3, int(preview_font_sz * 0.4)),
                        fill=(10, 15, 25, 215),
                        outline=(55, 65, 81, 255),
                        width=1
                    )
                    draw_cap.text((tx, ty), line_str, font=cap_font, fill=text_col)
                elif is_no_border:
                    # Clean text with soft 1px shadow
                    draw_cap.text((tx + 1, ty + 1), line_str, font=cap_font, fill=(0, 0, 0, 90))
                    draw_cap.text((tx, ty), line_str, font=cap_font, fill=text_col)
                elif is_small:
                    for ox, oy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                        draw_cap.text((tx + ox, ty + oy), line_str, font=cap_font, fill=(0, 0, 0, 220))
                    draw_cap.text((tx, ty), line_str, font=cap_font, fill=text_col)
                elif is_bold:
                    for ox in range(-2, 3):
                        for oy in range(-2, 3):
                            if ox != 0 or oy != 0:
                                draw_cap.text((tx + ox, ty + oy), line_str, font=cap_font, fill=(0, 0, 0, 255))
                    draw_cap.text((tx, ty), line_str, font=cap_font, fill=text_col)
                elif is_heavy:
                    for ox in range(-3, 4):
                        for oy in range(-3, 4):
                            if ox * ox + oy * oy <= 9 and (ox != 0 or oy != 0):
                                draw_cap.text((tx + ox, ty + oy), line_str, font=cap_font, fill=(0, 0, 0, 255))
                    draw_cap.text((tx, ty), line_str, font=cap_font, fill=text_col)
                else:  # Standard / Medium or Auto Preset
                    if "Soothe White" in style_sel and "auto" in cb_lower:
                        for ox, oy in [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, -1), (-1, 1), (1, 1), (0, 2), (0, 3)]:
                            draw_cap.text((tx + ox, ty + oy), line_str, font=cap_font, fill=(15, 23, 42, 170))
                    else:
                        for ox, oy in [(-1, -1), (1, -1), (-1, 1), (1, 1), (0, 1), (0, -1), (1, 0), (-1, 0)]:
                            draw_cap.text((tx + ox, ty + oy), line_str, font=cap_font, fill=(0, 0, 0, 255))
                    draw_cap.text((tx, ty), line_str, font=cap_font, fill=text_col)

            # Glowing frame outline
            outline_col = (99, 102, 241, 255) if self.active_mode == "shorts" else (56, 189, 248, 255)
            d_bg.rectangle([rx1, ry1, rx2, ry2], outline=outline_col, width=2)

            # Draw side or overlay annotations
            try:
                side_font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 11)
            except Exception:
                side_font = ImageFont.load_default()

            mode_title = f"📱 Shorts ({self.current_ratio})" if self.active_mode == "shorts" else f"🎬 Videos ({self.current_ratio})"

            if target_aspect <= 1.0:
                d_bg.text((12, 40), mode_title, font=side_font, fill=(165, 180, 252, 255))
                d_bg.text((12, 65), f"Top Pad: {self.current_pad_top:.1f}%", font=side_font, fill=(148, 163, 184, 255))
                d_bg.text((12, 85), f"Bottom Pad: {self.current_pad_bottom:.1f}%", font=side_font, fill=(148, 163, 184, 255))
                if self.current_pad_bottom > self.current_pad_top:
                    d_bg.text((12, 115), "⬆️ Shifted Upward", font=side_font, fill=(52, 211, 153, 255))
                elif self.current_pad_top == 0 and self.current_pad_bottom == 0:
                    d_bg.text((12, 115), "↔️ Full Screen", font=side_font, fill=(148, 163, 184, 255))

                rx_info = rx2 + 16
                d_bg.text((rx_info, 40), f"Zoom: {self.current_scale:.2f}x", font=side_font, fill=(165, 180, 252, 255))
                d_bg.text((rx_info, 65), f"Pan X: {int(self.current_pos_x*100)}%", font=side_font, fill=(148, 163, 184, 255))
                d_bg.text((rx_info, 85), f"Pan Y: {int(self.current_pos_y*100)}%", font=side_font, fill=(148, 163, 184, 255))
                d_bg.text((rx_info, 115), f"Fade: {fade_status}", font=side_font, fill=(96, 165, 250, 255) if self.bottom_fade_var.get() else (148, 163, 184, 255))
            else:
                info_overlay = f"{mode_title} • Zoom {self.current_scale:.2f}x • Pan ({int(self.current_pos_x*100)}%, {int(self.current_pos_y*100)}%) • Pad (T:{self.current_pad_top:.0f}%, B:{self.current_pad_bottom:.0f}%) • Fade {fade_status}"
                pill_w = min(target_display_w - 20, len(info_overlay) * 6 + 18)
                px1 = (target_display_w - pill_w) // 2
                px2 = px1 + pill_w
                py1 = max(4, ry1 + 6)
                py2 = py1 + 20
                d_bg.rectangle([px1, py1, px2, py2], fill=(15, 23, 42, 220), outline=outline_col, width=1)
                d_bg.text((px1 + 8, py1 + 3), info_overlay, font=side_font, fill=(224, 231, 255, 255))

            img_composite = bg.convert("RGB")
        else:
            # --- 📐 SOURCE CUTOUT VIEW ---
            disp_h = int(target_display_w * (in_h / in_w))
            if disp_h > 260:
                disp_h = 260
                disp_w = int(disp_h * (in_w / in_h))
            else:
                disp_w = target_display_w

            self._preview_geom = {
                "mode": "source",
                "in_w": in_w,
                "in_h": in_h,
                "crop_w": crop_w,
                "crop_h": crop_h,
                "target_w": disp_w,
                "target_h": disp_h
            }

            if base_frame:
                img = base_frame.copy().resize((disp_w, disp_h), Image.Resampling.LANCZOS)
            else:
                img = Image.new("RGB", (disp_w, disp_h), color=(20, 24, 34))

            scale_x = disp_w / in_w
            scale_y = disp_h / in_h

            box_x1 = int(off_x * scale_x)
            box_y1 = int(off_y * scale_y)
            box_x2 = int((off_x + crop_w) * scale_x)
            box_y2 = int((off_y + crop_h) * scale_y)

            mask = Image.new("RGBA", (disp_w, disp_h), (0, 0, 0, 160))
            draw_mask = ImageDraw.Draw(mask)
            draw_mask.rectangle([box_x1, box_y1, box_x2, box_y2], fill=(0, 0, 0, 0))

            img_rgba = img.convert("RGBA")
            img_composite = Image.alpha_composite(img_rgba, mask)

            if bottom_fade_on and (box_y2 > box_y1) and (box_x2 > box_x1):
                box_h = box_y2 - box_y1
                fade_h = max(4, int(box_h * fade_h_val))
                fade_top = max(box_y1, box_y2 - fade_h)
                actual_fade_h = box_y2 - fade_top
                fade_col = Image.new("RGBA", (1, actual_fade_h), (0, 0, 0, 0))
                for y_idx in range(actual_fade_h):
                    norm = y_idx / max(1, actual_fade_h - 1)
                    alpha = int(255 * fade_o_val * (norm ** 1.35))
                    fade_col.putpixel((0, y_idx), (0, 0, 0, min(255, max(0, alpha))))
                fade_box = fade_col.resize((box_x2 - box_x1, actual_fade_h), Image.Resampling.NEAREST)
                img_composite.paste(fade_box, (box_x1, fade_top), fade_box)

            draw_box = ImageDraw.Draw(img_composite)
            box_col = (99, 102, 241, 255) if self.active_mode == "shorts" else (56, 189, 248, 255)
            draw_box.rectangle([box_x1, box_y1, box_x2, box_y2], outline=box_col, width=3)

        ctk_img = ctk.CTkImage(light_image=img_composite, dark_image=img_composite, size=(target_display_w, target_display_h))
        self.canvas_label.configure(image=ctk_img, text="", cursor="hand2")

    # ==============================================================
    # COMMAND GENERATION & CLIPBOARD
    # ==============================================================
    def _generate_command_string(self) -> str:
        vid = self.video_entry.get().strip() or "video.mp4"
        base, ext = os.path.splitext(vid)
        self._sync_to_active_config()
        active_cfg = self.shorts_config if self.active_mode == "shorts" else self.videos_config
        tag = active_cfg["ratio_val"].replace(":", "")
        out_name = f"{base}_{tag}_{self.active_mode}{ext or '.mp4'}"

        parts = [
            sys.executable,
            f'"{str(BASE_DIR / "process_full.py")}"',
            f'-v "{vid}"',
            f'-o "{out_name}"',
            f'-r {active_cfg["ratio_val"]}',
            f'--scale {active_cfg["scale"]:.2f}',
            f'--pos-x {active_cfg["pos_x"]:.2f}',
            f'--pos-y {active_cfg["pos_y"]:.2f}'
        ]

        if active_cfg["pad_top"] > 0:
            parts.append(f'--pad-top {active_cfg["pad_top"]:.1f}')
        if active_cfg["pad_bottom"] > 0:
            parts.append(f'--pad-bottom {active_cfg["pad_bottom"]:.1f}')

        if active_cfg["bottom_fade"]:
            parts.append('--bottom-fade')
            fade_h = active_cfg.get("fade_height", self.fade_height)
            fade_o = active_cfg.get("fade_opacity", self.fade_opacity)
            parts.append(f'--fade-height {fade_h:.2f}')
            parts.append(f'--fade-opacity {fade_o:.2f}')

        srt = self.srt_entry.get().strip()
        if self.sub_switch_var.get() and srt and os.path.exists(srt):
            parts.append(f'-s "{srt}"')
            font_nm = self._get_selected_font_name()
            parts.append(f'--font "{font_nm}"')
            f_size = self._get_selected_font_size()
            parts.append(f'--font-size {f_size}')
            cap_style = self.caption_style_menu.get() if hasattr(self, "caption_style_menu") else "✨ Soothe White Light (Soft Clean Glow)"
            parts.append(f'--caption-style "{cap_style}"')
            cap_border = self._get_selected_caption_border()
            parts.append(f'--caption-border "{cap_border}"')
            sub_y = getattr(self, "sub_pos_y", 0.15)
            parts.append(f'--sub-pos-y {sub_y:.2f}')
            if hasattr(self, "caption_overflow_var") and self.caption_overflow_var.get():
                parts.append('--caption-overflow')

        music = self.music_entry.get().strip()
        if self.music_switch_var.get() and music and os.path.exists(music):
            parts.append(f'-m "{music}"')
            parts.append(f'--volume {self.current_vol:.2f}')

        audio = self.audio_entry.get().strip() if hasattr(self, "audio_entry") else ""
        if hasattr(self, "audio_switch_var") and self.audio_switch_var.get() and audio and os.path.exists(audio):
            parts.append(f'-a "{audio}"')

        if hasattr(self, "current_video_vol") and abs(self.current_video_vol - 1.0) > 0.01:
            parts.append(f'--video-volume {self.current_video_vol:.2f}')

        return " ".join(parts)

    def _update_cli_display(self):
        cmd = self._generate_command_string()
        self.cli_textbox.delete("1.0", "end")
        self.cli_textbox.insert("1.0", cmd)

    def _copy_cli_command(self):
        cmd = self._generate_command_string()
        self.clipboard_clear()
        self.clipboard_append(cmd)
        messagebox.showinfo("Copied", "Command copied to clipboard! You can paste and run it directly in your Terminal.")

    # ==============================================================
    # 1-MINUTE PREVIEW (OPENS IN MAC PLAYER)
    # ==============================================================
    def _start_generate_preview(self):
        if not self.video_path or not os.path.exists(self.video_path):
            messagebox.showwarning("Missing Video", "Please select a valid video file first.")
            return

        if self.is_rendering:
            return

        self.is_rendering = True
        self.btn_preview.configure(state="disabled")
        if hasattr(self, "btn_multi_preview"):
            self.btn_multi_preview.configure(state="disabled")
        self.status_label.configure(text=f"⚡ Generating 1-Min preview ({self.active_mode.capitalize()} mode)...")
        self.progress_bar.set(0.3)

        threading.Thread(target=self._run_preview_thread, daemon=True).start()

    def _run_preview_thread(self):
        try:
            start_off = float(self.offset_entry.get().strip() or "0")
            preview_file = str(PREVIEW_DIR / f"preview_{int(time.time())}.mp4")

            self._sync_to_active_config()
            active_cfg = self.shorts_config if self.active_mode == "shorts" else self.videos_config
            r_val = active_cfg["ratio_val"]

            in_w = self.video_info.get("width", 1920)
            in_h = self.video_info.get("height", 1080)
            in_aspect = (in_w / in_h) if in_h else (16.0 / 9.0)

            # Crop filter
            if (r_val in ["16:9", "original"]) and abs(in_aspect - (16.0 / 9.0)) < 0.02 and active_cfg["scale"] == 1.0 and active_cfg["pad_top"] == 0.0 and active_cfg["pad_bottom"] == 0.0 and active_cfg["pos_x"] == 0.5 and active_cfg["pos_y"] == 0.5:
                crop_filter = None
            else:
                crop_filter, _, _, _, _ = compute_crop_filter(
                    in_w=in_w,
                    in_h=in_h,
                    ratio=r_val,
                    pos_x=active_cfg["pos_x"],
                    pos_y=active_cfg["pos_y"],
                    scale=active_cfg["scale"],
                    pad_top=active_cfg["pad_top"],
                    pad_bottom=active_cfg["pad_bottom"]
                )

            srt = self.srt_entry.get().strip()
            use_srt = self.sub_switch_var.get() and srt and os.path.exists(srt)

            music = self.music_entry.get().strip()
            use_music = self.music_switch_var.get() and music and os.path.exists(music)

            audio = self.audio_entry.get().strip() if hasattr(self, "audio_entry") else ""
            use_audio = self.audio_switch_var.get() and audio and os.path.exists(audio) if hasattr(self, "audio_switch_var") else False

            cap_style = self.caption_style_menu.get() if hasattr(self, "caption_style_menu") else "✨ Soothe White Light (Soft Clean Glow)"
            font_nm = self._get_selected_font_name()
            font_sz = self._get_selected_font_size()
            cap_border = self._get_selected_caption_border()
            sub_y = getattr(self, "sub_pos_y", 0.15)

            use_noise = (self.yt_noise_removal_var.get() if hasattr(self, "yt_noise_removal_var") and getattr(self, "yt_enabled", False) else False)
            noise_int = getattr(self, "yt_noise_intensity", "Medium")

            run_pipeline(
                video_path=self.video_path,
                output_path=preview_file,
                crop_filter=crop_filter,
                srt_path=srt if use_srt else None,
                music_path=music if use_music else None,
                music_volume=self.current_vol,
                start_sec=start_off,
                duration_sec=60.0,
                use_hw_accel=True,
                font_name=font_nm,
                font_size=font_sz,
                caption_style=cap_style,
                caption_border=cap_border,
                bottom_fade=active_cfg["bottom_fade"],
                fade_height=active_cfg.get("fade_height", self.fade_height),
                fade_opacity=active_cfg.get("fade_opacity", self.fade_opacity),
                sub_margin_v=None,
                audio_path=audio if use_audio else None,
                sub_pos_y=sub_y,
                noise_removal=use_noise,
                noise_intensity=noise_int,
                video_volume=getattr(self, "current_video_vol", 1.0),
                caption_overflow=bool(self.caption_overflow_var.get()) if hasattr(self, "caption_overflow_var") else False
            )

            preview_path = preview_file
            self.after(0, lambda p=preview_path: self._on_preview_finished(p))

        except Exception as e:
            err_msg = str(e)
            self.after(0, lambda err=err_msg: self._on_preview_error(err))

    def _on_preview_finished(self, preview_file: str):
        self.is_rendering = False
        self.btn_preview.configure(state="normal")
        self.btn_multi_preview.configure(state="normal")
        self.progress_bar.set(1.0)
        self.status_label.configure(text=f"✅ Preview Ready! Opened in Mac player: {os.path.basename(preview_file)}")

        # Launch preview in native macOS player (QuickTime / default viewer)
        subprocess.run(["open", preview_file], check=False)

    def _on_preview_error(self, err_msg: str):
        self.is_rendering = False
        self.btn_preview.configure(state="normal")
        self.btn_multi_preview.configure(state="normal")
        self.progress_bar.set(0)
        self.status_label.configure(text=f"❌ Preview Error")
        messagebox.showerror("Preview Failed", err_msg)

    # ==============================================================
    # 3-SEGMENT TEST PREVIEW (0%, 45%, 75% - 3 MIN COMBINED)
    # ==============================================================
    def _start_generate_multi_preview(self):
        if not self.video_path or not os.path.exists(self.video_path):
            messagebox.showwarning("Missing Video", "Please select a valid video file first.")
            return

        if self.is_rendering:
            messagebox.showwarning("Busy", "A render job is already running.")
            return

        self.is_rendering = True
        self.is_cancelled = False
        self.btn_preview.configure(state="disabled")
        self.btn_multi_preview.configure(state="disabled")
        self.status_label.configure(text="⚡ Generating 3-Segment Preview (0%, 45%, 75% • 3 Min Combined)...")
        self.progress_bar.set(0.1)

        threading.Thread(target=self._run_multi_preview_thread, daemon=True).start()

    def _run_multi_preview_thread(self):
        try:
            preview_file = str(PREVIEW_DIR / f"preview_3seg_{int(time.time())}.mp4")

            self._sync_to_active_config()
            active_cfg = self.shorts_config if self.active_mode == "shorts" else self.videos_config
            r_val = active_cfg["ratio_val"]

            in_w = self.video_info.get("width", 1920)
            in_h = self.video_info.get("height", 1080)
            in_aspect = (in_w / in_h) if in_h else (16.0 / 9.0)

            if (r_val in ["16:9", "original"]) and abs(in_aspect - (16.0 / 9.0)) < 0.02 and active_cfg["scale"] == 1.0 and active_cfg["pad_top"] == 0.0 and active_cfg["pad_bottom"] == 0.0 and active_cfg["pos_x"] == 0.5 and active_cfg["pos_y"] == 0.5:
                crop_filter = None
            else:
                crop_filter, _, _, _, _ = compute_crop_filter(
                    in_w=in_w,
                    in_h=in_h,
                    ratio=r_val,
                    pos_x=active_cfg["pos_x"],
                    pos_y=active_cfg["pos_y"],
                    scale=active_cfg["scale"],
                    pad_top=active_cfg["pad_top"],
                    pad_bottom=active_cfg["pad_bottom"]
                )

            srt = self.srt_entry.get().strip()
            use_srt = self.sub_switch_var.get() and srt and os.path.exists(srt)

            music = self.music_entry.get().strip()
            use_music = self.music_switch_var.get() and music and os.path.exists(music)

            audio = self.audio_entry.get().strip() if hasattr(self, "audio_entry") else ""
            use_audio = self.audio_switch_var.get() and audio and os.path.exists(audio) if hasattr(self, "audio_switch_var") else False

            cap_style = self.caption_style_menu.get() if hasattr(self, "caption_style_menu") else "✨ Soothe White Light (Soft Clean Glow)"
            font_nm = self._get_selected_font_name()
            font_sz = self._get_selected_font_size()
            cap_border = self._get_selected_caption_border()
            sub_y = getattr(self, "sub_pos_y", 0.15)

            def cancel_checker():
                return self.is_cancelled

            def set_proc(p):
                self.current_proc = p

            def update_progress(seg_num, total_segs, seg_start, seg_dur, stage="rendering"):
                if stage == "combining":
                    pct = 0.92
                    msg = "⚡ Combining 3 segments into final preview video..."
                else:
                    pct = 0.15 + (seg_num - 1) * 0.25
                    pct_desc = "0% (Start)" if seg_num == 1 else ("45% (Middle)" if seg_num == 2 else "75% (Late)")
                    m, s = divmod(int(seg_start), 60)
                    msg = f"⚡ Rendering Segment {seg_num}/{total_segs} ({pct_desc} at {m:02d}:{s:02d}, {seg_dur:.0f}s)..."
                self.after(0, lambda: self.status_label.configure(text=msg))
                self.after(0, lambda: self.progress_bar.set(pct))

            use_noise = (self.yt_noise_removal_var.get() if hasattr(self, "yt_noise_removal_var") and getattr(self, "yt_enabled", False) else False)
            noise_int = getattr(self, "yt_noise_intensity", "Medium")

            generate_3segment_preview(
                video_path=self.video_path,
                output_path=preview_file,
                crop_filter=crop_filter,
                srt_path=srt if use_srt else None,
                music_path=music if use_music else None,
                music_volume=self.current_vol,
                segment_duration=60.0,
                percentages=(0.0, 0.45, 0.75),
                use_hw_accel=True,
                caption_style=cap_style,
                font_name=font_nm,
                font_size=font_sz,
                caption_border=cap_border,
                bottom_fade=active_cfg["bottom_fade"],
                fade_height=active_cfg.get("fade_height", self.fade_height),
                fade_opacity=active_cfg.get("fade_opacity", self.fade_opacity),
                sub_margin_v=None,
                cancel_check=cancel_checker,
                proc_callback=set_proc,
                status_callback=update_progress,
                audio_path=audio if use_audio else None,
                sub_pos_y=sub_y,
                noise_removal=use_noise,
                noise_intensity=noise_int,
                video_volume=getattr(self, "current_video_vol", 1.0),
                caption_overflow=bool(self.caption_overflow_var.get()) if hasattr(self, "caption_overflow_var") else False
            )

            preview_path = preview_file
            self.after(0, lambda p=preview_path: self._on_multi_preview_finished(p))

        except Exception as e:
            err_msg = str(e)
            self.after(0, lambda err=err_msg: self._on_preview_error(err))

    def _on_multi_preview_finished(self, preview_file: str):
        self.is_rendering = False
        self.btn_preview.configure(state="normal")
        self.btn_multi_preview.configure(state="normal")
        self.progress_bar.set(1.0)
        self.status_label.configure(text=f"✅ 3-Segment Preview Ready (3 Min Combined)! Opened: {os.path.basename(preview_file)}")
        subprocess.run(["open", preview_file], check=False)

    # ==============================================================
    # WHOLE VIDEO BACKGROUND RUNNER
    # ==============================================================
    def _start_run_full_video(self):
        if not self.video_path or not os.path.exists(self.video_path):
            messagebox.showwarning("Missing Video", "Please select a valid video file first.")
            return

        if self.is_rendering:
            messagebox.showwarning("Busy", "A render job is already running.")
            return

        self.is_rendering = True
        self.btn_run_full.configure(state="disabled")
        self.btn_preview.configure(state="disabled")
        self.btn_multi_preview.configure(state="disabled")
        self.status_label.configure(text="🚀 Launching full-length video hardware encoding...")
        self.progress_bar.set(0.1)

        threading.Thread(target=self._run_full_thread, daemon=True).start()

    def _run_full_thread(self):
        try:
            cmd_str = self._generate_command_string()
            # Parse command parts
            import shlex
            cmd_args = shlex.split(cmd_str)

            proc = subprocess.Popen(
                cmd_args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True
            )
            self.current_proc = proc

            for line in iter(proc.stdout.readline, ""):
                if self.is_cancelled:
                    proc.kill()
                    break
                line_s = line.strip()
                if "Progress:" in line_s or "frame=" in line_s or "time=" in line_s:
                    self.after(0, lambda l=line_s: self.status_label.configure(text=f"⚡ {l[:70]}"))

            proc.stdout.close()
            ret = proc.wait()
            self.current_proc = None

            if self.is_cancelled:
                return

            if ret == 0:
                self.after(0, self._on_full_finished)
            else:
                err_msg = f"FFmpeg process returned code {ret}"
                self.after(0, lambda err=err_msg: self._on_full_error(err))

        except Exception as e:
            if not self.is_cancelled:
                err_msg = str(e)
                self.after(0, lambda err=err_msg: self._on_full_error(err))

    def _on_full_finished(self):
        self.is_rendering = False
        self.btn_run_full.configure(state="normal")
        self.btn_preview.configure(state="normal")
        self.btn_multi_preview.configure(state="normal")
        self.progress_bar.set(1.0)
        self.status_label.configure(text="🎉 Full video completed successfully!")
        messagebox.showinfo("Render Complete", "Your full video has been processed at maximum hardware speed!")

    def _on_full_error(self, err_msg: str):
        self.is_rendering = False
        self.btn_run_full.configure(state="normal")
        self.btn_preview.configure(state="normal")
        self.btn_multi_preview.configure(state="normal")
        self.progress_bar.set(0)
        self.status_label.configure(text="❌ Full Video Error")
        messagebox.showerror("Full Video Failed", err_msg)

    # ==============================================================
    # 1-CLICK FOLDER AUTOMATION METHOD
    # ==============================================================
    def _choose_folder_and_run(self):
        folder_path = filedialog.askdirectory(
            title="Select Folder With Video, Subtitles & Timestamps",
            initialdir=str(Path.home() / "Desktop")
        )
        if not folder_path:
            return

        from run_folder import detect_folder_assets
        assets = detect_folder_assets(Path(folder_path))

        if assets["video"]:
            self._load_video(str(assets["video"]))
        if assets["subtitles"]:
            self.srt_path = str(assets["subtitles"])
            self.srt_entry.delete(0, "end")
            self.srt_entry.insert(0, str(assets["subtitles"]))
        if assets["audio"]:
            self.music_path = str(assets["audio"])
            self.music_entry.delete(0, "end")
            self.music_entry.insert(0, str(assets["audio"]))
        if assets["ts_content"]:
            self.batch_textbox.delete("1.0", "end")
            self.batch_textbox.insert("1.0", assets["ts_content"])
            self._on_batch_text_changed()

        self._update_cli_display()
        # Switch to Batch tab
        self.right_tabs.set("✂️ Batch Auto-Clipper")

        if assets["ts_count"] > 0:
            self._start_run_batch_clips()
        else:
            messagebox.showinfo("Folder Loaded", f"Loaded folder '{Path(folder_path).name}'.\nPlease paste timestamps into the box and click 'Clip All Segments'.")

    # ==============================================================
    # BATCH AUTO-CLIPPER METHODS
    # ==============================================================
    def _on_batch_text_changed(self, event=None):
        try:
            text = self.batch_textbox.get("1.0", "end").strip()
            entries = parse_timestamp_entries(text)
            shorts = [e for e in entries if e["duration"] <= 180]
            longs = [e for e in entries if e["duration"] > 180]
            s_tag = self.shorts_config.get("ratio_val", "9:16")
            v_tag = self.videos_config.get("ratio_val", "16:9")
            if longs:
                self.batch_count_label.configure(
                    text=f"Detected: {len(entries)} clips ({len(shorts)} Shorts [{s_tag}], {len(longs)} Videos [{v_tag}])",
                    text_color="#38bdf8"
                )
            else:
                self.batch_count_label.configure(
                    text=f"Detected: {len(entries)} clips (All Shorts [{s_tag}])",
                    text_color="#818cf8"
                )
        except Exception:
            pass

    def _fix_batch_hour_typos(self):
        text = self.batch_textbox.get("1.0", "end")
        fixed_text, count = auto_fix_timestamp_text(text)
        if count > 0:
            self.batch_textbox.delete("1.0", "end")
            self.batch_textbox.insert("1.0", fixed_text)
            self._on_batch_text_changed()
            messagebox.showinfo(
                "Timestamps Auto-Fixed",
                f"Successfully detected and fixed {count} timestamp(s) with hour typos!\n\nAll clips now have proper ~60s Reel durations."
            )
        else:
            messagebox.showinfo(
                "All Clean",
                "No hour-jump typos detected in your timestamps list.\nEverything looks good to clip!"
            )

    def _browse_batch_timestamps(self):
        file_path = filedialog.askopenfilename(
            title="Choose Timestamps File",
            filetypes=[
                ("Text & Markdown Files", "*.txt *.md"),
                ("All Files", "*.*")
            ]
        )
        if file_path:
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                self.batch_textbox.delete("1.0", "end")
                self.batch_textbox.insert("1.0", content)
                self._on_batch_text_changed()
            except Exception as e:
                messagebox.showerror("Error", f"Failed reading file: {e}")

    def _clear_batch_timestamps(self):
        self.batch_textbox.delete("1.0", "end")
        self._on_batch_text_changed()

    def _open_approved_folder(self):
        out_dir = BASE_DIR / "approved_clips"
        out_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["open", str(out_dir)], check=False)

    def _master_kill_process(self):
        """Immediately stops the running batch or video render, kills FFmpeg, and resets UI."""
        self.is_cancelled = True
        self.is_rendering = False

        if self.current_proc:
            try:
                self.current_proc.kill()
            except Exception:
                pass
            self.current_proc = None

        # System-level kill for active ffmpeg encoders
        try:
            subprocess.run(["pkill", "-9", "-f", "ffmpeg.*approved_clips"], check=False)
            subprocess.run(["pkill", "-9", "-f", "ffmpeg.*CONCENTRATION"], check=False)
            subprocess.run(["pkill", "-9", "-f", "ffmpeg.*h264_videotoolbox"], check=False)
        except Exception:
            pass

        self.btn_run_batch.configure(state="normal", text="🚀 Clip All Segments (Hardware Accelerated)")
        self.btn_preview.configure(state="normal")
        self.btn_multi_preview.configure(state="normal")
        if hasattr(self, "btn_run_full"):
            self.btn_run_full.configure(state="normal")

        self.batch_progress_bar.set(0)
        self.batch_status_label.configure(
            text="🛑 Master Kill executed! All rendering tasks killed. Ready to resume.",
            text_color="#ef4444"
        )
        if hasattr(self, "status_label"):
            self.status_label.configure(text="Status: Stopped by Master Kill")
            self.progress_bar.set(0)

        messagebox.showinfo("Master Kill Executed", "Processing stopped!\nAll running FFmpeg tasks have been terminated.\nYou can now resume or re-clip.")

    def _start_run_batch_clips(self):
        if not self.video_path or not os.path.exists(self.video_path):
            messagebox.showwarning("Missing Video", "Please select a valid video file first in Section 1.")
            return

        text = self.batch_textbox.get("1.0", "end").strip()
        entries = parse_timestamp_entries(text)
        if not entries:
            messagebox.showwarning("No Clips", "No valid timestamp entries found. Please paste timestamps in the box.")
            return

        if self.is_rendering:
            messagebox.showwarning("Busy", "A render job is already running.")
            return

        self.is_rendering = True
        self.is_cancelled = False
        self.current_proc = None
        self.btn_run_batch.configure(state="disabled")
        self.btn_preview.configure(state="disabled")
        self.btn_multi_preview.configure(state="disabled")
        if hasattr(self, "btn_run_full"):
            self.btn_run_full.configure(state="disabled")
        self.batch_status_label.configure(text=f"🚀 Starting batch cut of {len(entries)} clips...", text_color="#818cf8")
        self.batch_progress_bar.set(0.01)

        threading.Thread(target=self._run_batch_clips_thread, args=(entries,), daemon=True).start()

    def _run_batch_clips_thread(self, entries):
        try:
            out_dir = BASE_DIR / "approved_clips"
            out_dir.mkdir(parents=True, exist_ok=True)

            in_w = self.video_info.get("width", 1920)
            in_h = self.video_info.get("height", 1080)
            in_aspect = (in_w / in_h) if in_h else (16.0 / 9.0)

            # Ensure current active settings are synced
            self._sync_to_active_config()

            # Pre-compute Shorts crop filter from Shorts profile
            s_cfg = self.shorts_config
            shorts_crop_filter, _, _, _, _ = compute_crop_filter(
                in_w=in_w,
                in_h=in_h,
                ratio=s_cfg["ratio_val"],
                pos_x=s_cfg["pos_x"],
                pos_y=s_cfg["pos_y"],
                scale=s_cfg["scale"],
                pad_top=s_cfg["pad_top"],
                pad_bottom=s_cfg["pad_bottom"]
            )

            # Pre-compute Videos crop filter from Videos profile
            v_cfg = self.videos_config
            v_ratio = v_cfg["ratio_val"]
            if (v_ratio in ["16:9", "original"]) and abs(in_aspect - (16.0 / 9.0)) < 0.02 and v_cfg["scale"] == 1.0 and v_cfg["pad_top"] == 0.0 and v_cfg["pad_bottom"] == 0.0 and v_cfg["pos_x"] == 0.5 and v_cfg["pos_y"] == 0.5:
                videos_crop_filter = None
            else:
                videos_crop_filter, _, _, _, _ = compute_crop_filter(
                    in_w=in_w,
                    in_h=in_h,
                    ratio=v_ratio,
                    pos_x=v_cfg["pos_x"],
                    pos_y=v_cfg["pos_y"],
                    scale=v_cfg["scale"],
                    pad_top=v_cfg["pad_top"],
                    pad_bottom=v_cfg["pad_bottom"]
                )

            srt = self.srt_entry.get().strip()
            use_srt = self.sub_switch_var.get() and srt and os.path.exists(srt)
            music = self.music_entry.get().strip()
            use_music = self.music_switch_var.get() and music and os.path.exists(music)
            audio = self.audio_entry.get().strip() if hasattr(self, "audio_entry") else ""
            use_audio = self.audio_switch_var.get() and audio and os.path.exists(audio) if hasattr(self, "audio_switch_var") else False
            cap_style = self.caption_style_menu.get() if hasattr(self, "caption_style_menu") else "✨ Soothe White Light (Soft Clean Glow)"
            font_nm = self._get_selected_font_name()
            font_sz = self._get_selected_font_size()
            cap_border = self._get_selected_caption_border()
            sub_y = getattr(self, "sub_pos_y", 0.15)

            total = len(entries)
            thumb_dir = out_dir / "thumbnails"
            thumb_dir.mkdir(parents=True, exist_ok=True)
            skip_done = self.skip_existing_var.get()

            for idx, entry in enumerate(entries, 1):
                if self.is_cancelled:
                    break

                clean_t = entry["clean_title"]
                out_vid = out_dir / f"{clean_t}.mp4"
                out_thumb = thumb_dir / f"{clean_t}.png"

                progress_val = idx / total

                # Instant resume: skip already finished clips if checkbox is enabled
                if skip_done and out_vid.exists() and out_vid.stat().st_size > 100000:
                    status_txt = f"[{idx}/{total}] ⏩ Already done: {clean_t} (Skipped)"
                    self.after(0, lambda p=progress_val, s=status_txt: self._update_batch_progress(p, s))
                    if not out_thumb.exists():
                        try:
                            extract_thumbnail(str(out_vid), str(out_thumb))
                        except Exception:
                            pass
                    time.sleep(0.05)
                    continue

                dur = entry["duration"]
                is_long = dur > 180.0

                if is_long:
                    chosen_crop = videos_crop_filter
                    ratio_tag = f"{v_cfg['ratio_val']} Video"
                    dur_display = f"{dur/60:.1f}m"
                    clip_fade = v_cfg["bottom_fade"]
                    clip_fade_h = v_cfg.get("fade_height", 0.38)
                    clip_fade_o = v_cfg.get("fade_opacity", 0.92)
                else:
                    chosen_crop = shorts_crop_filter
                    ratio_tag = f"{s_cfg['ratio_val']} Shorts"
                    dur_display = f"{dur:.1f}s"
                    clip_fade = s_cfg["bottom_fade"]
                    clip_fade_h = s_cfg.get("fade_height", 0.38)
                    clip_fade_o = s_cfg.get("fade_opacity", 0.92)

                status_txt = f"[{idx}/{total}] 🎬 Cutting [{ratio_tag}]: {entry['title']} ({dur_display})..."
                self.after(0, lambda p=progress_val, s=status_txt: self._update_batch_progress(p, s))

                def check_cancel():
                    return self.is_cancelled

                def set_proc(p):
                    self.current_proc = p

                # Render video clip with chosen ratio framing (1 & 2), and universal music & subtitles (3 & 4)
                use_noise = (self.yt_noise_removal_var.get() if hasattr(self, "yt_noise_removal_var") and getattr(self, "yt_enabled", False) else False)
                noise_int = getattr(self, "yt_noise_intensity", "Medium")

                run_pipeline(
                    video_path=self.video_path,
                    output_path=str(out_vid),
                    crop_filter=chosen_crop,
                    srt_path=srt if use_srt else None,
                    music_path=music if use_music else None,
                    music_volume=self.current_vol,
                    start_sec=entry["start_sec"],
                    duration_sec=dur,
                    use_hw_accel=True,
                    caption_style=cap_style,
                    font_name=font_nm,
                    font_size=font_sz,
                    caption_border=cap_border,
                    bottom_fade=clip_fade,
                    fade_height=clip_fade_h,
                    fade_opacity=clip_fade_o,
                    sub_margin_v=None,
                    cancel_check=check_cancel,
                    proc_callback=set_proc,
                    audio_path=audio if use_audio else None,
                    sub_pos_y=sub_y,
                    noise_removal=use_noise,
                    noise_intensity=noise_int,
                    video_volume=getattr(self, "current_video_vol", 1.0),
                    caption_overflow=bool(self.caption_overflow_var.get()) if hasattr(self, "caption_overflow_var") else False
                )

                if self.is_cancelled:
                    break

                # Extract thumbnail matching title into thumbnails subfolder
                try:
                    extract_thumbnail(str(out_vid), str(out_thumb), offset_sec=min(5.0, max(0.5, dur / 2.0)))
                except Exception as te:
                    print(f"Warning: could not extract thumbnail: {te}")

            self.current_proc = None
            if self.is_cancelled:
                self.after(0, self._on_batch_cancelled)
            else:
                done_txt = f"🎉 Done! All {total} clips saved into approved_clips."
                out_dir_path = str(out_dir)
                self.after(0, lambda t=done_txt, d=out_dir_path: self._on_batch_finished(t, d))
        except (Exception, BaseException) as e:
            if not self.is_cancelled:
                err_msg = str(e)
                self.after(0, lambda err=err_msg: self._on_batch_error(err))

    def _on_batch_cancelled(self):
        self.is_rendering = False
        self.btn_run_batch.configure(state="normal")
        self.btn_preview.configure(state="normal")
        self.btn_multi_preview.configure(state="normal")
        if hasattr(self, "btn_run_full"):
            self.btn_run_full.configure(state="normal")
        self.batch_progress_bar.set(0)
        self.batch_status_label.configure(text="⏹️ Stopped by user (Master Kill). Ready to restart.", text_color="#f87171")

    def _update_batch_progress(self, progress: float, status: str):
        self.batch_progress_bar.set(progress)
        self.batch_status_label.configure(text=status)

    def _on_batch_finished(self, msg: str, folder_path: str):
        self.is_rendering = False
        self.btn_run_batch.configure(state="normal")
        self.btn_preview.configure(state="normal")
        self.btn_multi_preview.configure(state="normal")
        if hasattr(self, "btn_run_full"):
            self.btn_run_full.configure(state="normal")
        self.batch_progress_bar.set(1.0)
        self.batch_status_label.configure(text=msg)
        subprocess.run(["open", folder_path], check=False)
        messagebox.showinfo("Batch Complete", msg + "\nOpened output folder in Finder.")

    def _on_batch_error(self, err: str):
        self.is_rendering = False
        self.btn_run_batch.configure(state="normal")
        self.btn_preview.configure(state="normal")
        self.btn_multi_preview.configure(state="normal")
        if hasattr(self, "btn_run_full"):
            self.btn_run_full.configure(state="normal")
        self.batch_progress_bar.set(0)
        self.batch_status_label.configure(text=f"❌ Error: {err[:60]}", text_color="#ef4444")
        messagebox.showerror("Batch Cut Failed", err)


if __name__ == "__main__":
    app = StudioClipperApp()
    app.mainloop()
