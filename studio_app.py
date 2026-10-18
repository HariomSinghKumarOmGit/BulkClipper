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
import threading
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFont

# Import core multimedia processor & utilities
from process_full import probe_video, compute_crop_filter, run_pipeline, get_available_encoders
from sub_utils import parse_timestamp_entries, sanitize_filename, auto_fix_timestamp_text
from clip_tester import extract_thumbnail

# Set appearance and theme
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

BASE_DIR = Path(__file__).resolve().parent
PREVIEW_DIR = BASE_DIR / "offline_previews"
PREVIEW_DIR.mkdir(parents=True, exist_ok=True)


class StudioClipperApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Studio Video Clipper & Hardware Cropper (Offline Mac App)")
        self.geometry("1160x780")
        self.minsize(1050, 700)

        # State variables
        self.video_path: Optional[str] = None
        self.srt_path: Optional[str] = None
        self.music_path: Optional[str] = None
        self.video_info: Dict[str, Any] = {}
        self.base_frame_image: Optional[Image.Image] = None
        self.raw_frame_path = str(BASE_DIR / ".temp_raw_frame.jpg")

        self.current_ratio = "9:16"
        self.current_scale = 1.0
        self.current_pos_x = 0.5
        self.current_pos_y = 0.5
        self.current_pad_top = 0.0
        self.current_pad_bottom = 0.0
        self.current_vol = 0.12
        self.bottom_fade_var = ctk.BooleanVar(value=True)
        self.fade_height = 0.38
        self.fade_opacity = 0.92
        self.preview_mode = "reel"
        self.is_rendering = False
        self.is_cancelled = False
        self.current_proc = None
        self.skip_existing_var = ctk.BooleanVar(value=True)

        self.encoders = get_available_encoders()
        self.hw_label = "Apple Silicon VideoToolbox (Hardware Acceleration)" if self.encoders.get("videotoolbox") else "CPU (libx264)"

        self._build_ui()

        # Auto-check if TRATAK video exists on Desktop as a convenience
        default_video = Path.home() / "Desktop" / "CONCENTRATION AND TRATAK_480p.mp4"
        if default_video.exists():
            self._load_video(str(default_video))

    def _build_ui(self):
        # Configure layout (2 columns)
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # ==============================================================
        # LEFT COLUMN: CONTROLS & FILE PICKERS (Scrollable)
        # ==============================================================
        self.left_frame = ctk.CTkScrollableFrame(self, label_text="Project Configuration", corner_radius=10)
        self.left_frame.grid(row=0, column=0, padx=(16, 8), pady=16, sticky="nsew")

        # Header Badge
        hw_color = "#10b981" if self.encoders.get("videotoolbox") else "#6366f1"
        self.badge = ctk.CTkLabel(
            self.left_frame,
            text=f"⚡ {self.hw_label}",
            text_color=hw_color,
            font=ctk.CTkFont(size=12, weight="bold")
        )
        self.badge.pack(anchor="w", pady=(0, 10))

        # 1-Click Folder Automation Card
        folder_box = ctk.CTkFrame(self.left_frame, fg_color="#182030", border_width=1, border_color="#3b82f6", corner_radius=8)
        folder_box.pack(fill="x", pady=(0, 14))

        ctk.CTkLabel(folder_box, text="⚡ 1-Click Folder Auto-Run", font=ctk.CTkFont(size=13, weight="bold"), text_color="#60a5fa").pack(anchor="w", padx=10, pady=(8, 2))
        ctk.CTkLabel(folder_box, text="Pick 1 folder with your video, subtitles, music & timestamps.\nEverything is detected and clipped automatically!", font=ctk.CTkFont(size=11), text_color="gray", justify="left").pack(anchor="w", padx=10, pady=(0, 6))

        self.btn_1click_folder = ctk.CTkButton(
            folder_box,
            text="📂 Choose Folder & Auto-Run Everything",
            fg_color="#2563eb",
            hover_color="#1d4ed8",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=36,
            command=self._choose_folder_and_run
        )
        self.btn_1click_folder.pack(fill="x", padx=10, pady=(0, 8))

        # 1. Source Video Selection
        self._create_section_label(self.left_frame, "1. Source Video (.mp4, .mov, .mkv)")
        
        vid_box = ctk.CTkFrame(self.left_frame, fg_color="transparent")
        vid_box.pack(fill="x", pady=(0, 6))

        self.video_entry = ctk.CTkEntry(vid_box, placeholder_text="Select a video from your Mac...")
        self.video_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self.btn_browse_vid = ctk.CTkButton(vid_box, text="📁 Choose File", width=110, command=self._browse_video)
        self.btn_browse_vid.pack(side="right")

        self.meta_label = ctk.CTkLabel(
            self.left_frame,
            text="No video loaded yet",
            text_color="gray",
            font=ctk.CTkFont(size=12)
        )
        self.meta_label.pack(anchor="w", pady=(0, 14))

        # 2. Aspect Ratio & Crop Positioning
        self._create_section_label(self.left_frame, "2. Aspect Ratio & Crop Framing")

        self.ratio_segmented = ctk.CTkSegmentedButton(
            self.left_frame,
            values=["9:16 (Shorts/Reels)", "1:1 (Square)", "4:5 (Portrait)", "16:9 (Original)"],
            command=self._on_ratio_changed
        )
        self.ratio_segmented.set("9:16 (Shorts/Reels)")
        self.ratio_segmented.pack(fill="x", pady=(0, 6))

        self.ratio_info_label = ctk.CTkLabel(
            self.left_frame,
            text="💡 Selected ratio applies to Shorts (≤3m). Clips >3m are automatically kept in 16:9 (unscaled original video with bg music & subs).",
            font=ctk.CTkFont(size=11),
            text_color="#9ca3af",
            wraplength=340,
            justify="left"
        )
        self.ratio_info_label.pack(anchor="w", pady=(0, 10))

        # --- A. Scale / Zoom Framing ---
        scale_header_frame = ctk.CTkFrame(self.left_frame, fg_color="transparent")
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

        scale_ctrl_frame = ctk.CTkFrame(self.left_frame, fg_color="transparent")
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
        pos_header_frame = ctk.CTkFrame(self.left_frame, fg_color="transparent")
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

        pos_ctrl_frame = ctk.CTkFrame(self.left_frame, fg_color="transparent")
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
        pos_y_header_frame = ctk.CTkFrame(self.left_frame, fg_color="transparent")
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

        pos_y_ctrl_frame = ctk.CTkFrame(self.left_frame, fg_color="transparent")
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
        pad_header_frame = ctk.CTkFrame(self.left_frame, fg_color="transparent")
        pad_header_frame.pack(fill="x", pady=(0, 2))

        ctk.CTkLabel(
            pad_header_frame,
            text="Frame Padding (Shift Clip Up / Letterbox):",
            font=ctk.CTkFont(size=12, weight="bold")
        ).pack(side="left")

        # Quick Preset Buttons:
        pad_preset_frame = ctk.CTkFrame(self.left_frame, fg_color="transparent")
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
        pad_top_subhead = ctk.CTkFrame(self.left_frame, fg_color="transparent")
        pad_top_subhead.pack(fill="x", pady=(0, 2))
        self.pad_top_label_var = ctk.StringVar(value="Top Padding: 0%")
        ctk.CTkLabel(pad_top_subhead, textvariable=self.pad_top_label_var, font=ctk.CTkFont(size=11)).pack(side="left")

        pad_top_ctrl = ctk.CTkFrame(self.left_frame, fg_color="transparent")
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
        pad_bottom_subhead = ctk.CTkFrame(self.left_frame, fg_color="transparent")
        pad_bottom_subhead.pack(fill="x", pady=(0, 2))
        self.pad_bottom_label_var = ctk.StringVar(value="Bottom Padding: 0%")
        ctk.CTkLabel(pad_bottom_subhead, textvariable=self.pad_bottom_label_var, font=ctk.CTkFont(size=11)).pack(side="left")

        pad_bottom_ctrl = ctk.CTkFrame(self.left_frame, fg_color="transparent")
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

        # --- E. Bottom Black Fade (Caption Contrast) Switch ---
        fade_box = ctk.CTkFrame(self.left_frame, fg_color="#182030", border_width=1, border_color="#374151", corner_radius=8)
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
        ).pack(anchor="w", padx=10, pady=(0, 8))

        # 3. Subtitles & Captions / Lyrics
        self._create_section_label(self.left_frame, "3. Subtitles & Lyrics (.srt, .ass, .lrc)")
        
        self.sub_switch_var = ctk.BooleanVar(value=True)
        self.sub_switch = ctk.CTkSwitch(
            self.left_frame,
            text="Burn Subtitles / Lyrics into Video",
            variable=self.sub_switch_var,
            command=self._update_cli_display
        )
        self.sub_switch.pack(anchor="w", pady=(0, 8))

        sub_box = ctk.CTkFrame(self.left_frame, fg_color="transparent")
        sub_box.pack(fill="x", pady=(0, 6))

        self.srt_entry = ctk.CTkEntry(sub_box, placeholder_text="Select subtitle or lyrics (.srt, .ass, .lrc)...")
        self.srt_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self.btn_browse_srt = ctk.CTkButton(sub_box, text="📁 Choose File", width=110, command=self._browse_srt)
        self.btn_browse_srt.pack(side="right")

        # Subtitle Color & Size
        sub_style_box = ctk.CTkFrame(self.left_frame, fg_color="transparent")
        sub_style_box.pack(fill="x", pady=(0, 14))
        
        self.color_menu = ctk.CTkOptionMenu(
            sub_style_box,
            values=["White Text (Black Border)", "Yellow Text (Black Border)"],
            width=180,
            command=lambda _: self._update_cli_display()
        )
        self.color_menu.set("White Text (Black Border)")
        self.color_menu.pack(side="left", padx=(0, 8))

        self.size_menu = ctk.CTkOptionMenu(
            sub_style_box,
            values=["Compact (18pt)", "Medium (22pt)", "Large (26pt)", "Extra Large (30pt)"],
            width=140,
            command=lambda _: self._update_cli_display()
        )
        self.size_menu.set("Medium (22pt)")
        self.size_menu.pack(side="left")

        # Caption Vertical Placement (Above Padding on Video vs Bottom Black Bar)
        self.caption_pos_menu = ctk.CTkOptionMenu(
            self.left_frame,
            values=["Caption: On Video Fade (Above Padding)", "Caption: Inside Black Bottom Bar"],
            command=lambda _: (self._render_visual_crop(), self._update_cli_display())
        )
        self.caption_pos_menu.set("Caption: On Video Fade (Above Padding)")
        self.caption_pos_menu.pack(fill="x", pady=(0, 14))

        # 4. Background Music & Ducking
        self._create_section_label(self.left_frame, "4. Background Music & Ducking (.mp3, .wav)")

        self.music_switch_var = ctk.BooleanVar(value=True)
        self.music_switch = ctk.CTkSwitch(
            self.left_frame,
            text="Mix Background Music Track",
            variable=self.music_switch_var,
            command=self._update_cli_display
        )
        self.music_switch.pack(anchor="w", pady=(0, 8))

        music_box = ctk.CTkFrame(self.left_frame, fg_color="transparent")
        music_box.pack(fill="x", pady=(0, 6))

        self.music_entry = ctk.CTkEntry(music_box, placeholder_text="Select background music file...")
        self.music_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self.btn_browse_music = ctk.CTkButton(music_box, text="📁 Choose Music", width=110, command=self._browse_music)
        self.btn_browse_music.pack(side="right")

        # Ducking volume slider with - / + nudge buttons
        self.vol_label_var = ctk.StringVar(value="Background Volume: 12% (Speech stays crisp)")
        self.vol_label = ctk.CTkLabel(self.left_frame, textvariable=self.vol_label_var, font=ctk.CTkFont(size=12))
        self.vol_label.pack(anchor="w", pady=(0, 2))

        vol_ctrl_frame = ctk.CTkFrame(self.left_frame, fg_color="transparent")
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
        self.vol_slider.set(0.12)
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
        self._create_section_label(self.left_frame, "5. Render Controls")

        offset_box = ctk.CTkFrame(self.left_frame, fg_color="transparent")
        offset_box.pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(offset_box, text="Start Preview Offset (sec):", font=ctk.CTkFont(size=12)).pack(side="left", padx=(0, 10))
        self.offset_entry = ctk.CTkEntry(offset_box, width=80)
        self.offset_entry.insert(0, "0")
        self.offset_entry.pack(side="left")

        self.btn_preview = ctk.CTkButton(
            self.left_frame,
            text="🎬 Generate 1-Min Preview (Opens in Mac Player)",
            height=42,
            fg_color="#6366f1",
            hover_color="#4f46e5",
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._start_generate_preview
        )
        self.btn_preview.pack(fill="x", pady=(0, 10))

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
            values=["📱 Final Reel View (9:16)", "📐 16:9 Source Cutout"],
            command=self._on_preview_mode_changed
        )
        self.preview_mode_segmented.set("📱 Final Reel View (9:16)")
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
    # FILE BROWSING & DATA LOADING
    # ==============================================================
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
        self.video_path = file_path
        self.video_entry.delete(0, "end")
        self.video_entry.insert(0, file_path)

        try:
            self.video_info = probe_video(file_path)
            w = self.video_info["width"]
            h = self.video_info["height"]
            dur = self.video_info["duration"]
            audio = "Yes" if self.video_info["has_audio"] else "No"
            
            m, s = int(dur // 60), int(dur % 60)
            dur_fmt = f"{m}m {s}s" if m < 60 else f"{m // 60}h {m % 60}m"
            self.meta_label.configure(text=f"📐 {w}x{h}  |  ⏱️ {dur_fmt}  |  🎵 Audio: {audio}")

            # Grab keyframe for live preview
            self._extract_keyframe(file_path)
            self._render_visual_crop()
            self._update_cli_display()
        except Exception as e:
            messagebox.showerror("Error Reading Video", str(e))

    def _extract_keyframe(self, video_path: str):
        """Extracts 1 frame snapshot for the visual crop framing box."""
        try:
            cmd = [
                "ffmpeg", "-y",
                "-ss", "00:00:03",
                "-i", video_path,
                "-frames:v", "1",
                "-update", "1",
                "-q:v", "2",
                self.raw_frame_path
            ]
            subprocess.run(cmd, capture_output=True, check=True)
            if os.path.exists(self.raw_frame_path):
                self.base_frame_image = Image.open(self.raw_frame_path).convert("RGB")
        except Exception as e:
            print("Failed to capture snapshot frame:", e)
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
            self._update_cli_display()

    def _browse_music(self):
        path = filedialog.askopenfilename(
            title="Choose Background Music File",
            filetypes=[("Audio Files", "*.mp3 *.wav *.m4a *.aac *.ogg"), ("All Files", "*.*")]
        )
        if path:
            self.music_path = path
            self.music_entry.delete(0, "end")
            self.music_entry.insert(0, path)
            self._update_cli_display()

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
            self.current_ratio = "original"

        self._render_visual_crop()
        self._update_cli_display()

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
        self.current_pad_top = 8.0
        self.current_pad_bottom = 14.0
        self.pad_top_slider.set(8.0)
        self.pad_bottom_slider.set(14.0)
        self.pad_top_label_var.set("Top Padding: 8.0% (Shifted Up)")
        self.pad_bottom_label_var.set("Bottom Padding: 14.0% (Letterbox)")
        self._render_visual_crop()
        self._update_cli_display()

    def _preset_centered_pad(self):
        """Sets balanced 10% top and 10% bottom padding."""
        self.current_pad_top = 10.0
        self.current_pad_bottom = 10.0
        self.pad_top_slider.set(10.0)
        self.pad_bottom_slider.set(10.0)
        self.pad_top_label_var.set("Top Padding: 10.0%")
        self.pad_bottom_label_var.set("Bottom Padding: 10.0%")
        self._render_visual_crop()
        self._update_cli_display()

    def _reset_padding(self):
        """Resets padding to 0%."""
        self.current_pad_top = 0.0
        self.current_pad_bottom = 0.0
        self.pad_top_slider.set(0.0)
        self.pad_bottom_slider.set(0.0)
        self.pad_top_label_var.set("Top Padding: 0%")
        self.pad_bottom_label_var.set("Bottom Padding: 0%")
        self._render_visual_crop()
        self._update_cli_display()

    def _on_pad_top_slider_changed(self, value: float):
        self.current_pad_top = round(float(value), 1)
        self.pad_top_label_var.set(f"Top Padding: {self.current_pad_top:.1f}%")
        self._render_visual_crop()
        self._update_cli_display()

    def _on_pad_bottom_slider_changed(self, value: float):
        self.current_pad_bottom = round(float(value), 1)
        self.pad_bottom_label_var.set(f"Bottom Padding: {self.current_pad_bottom:.1f}%")
        self._render_visual_crop()
        self._update_cli_display()

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
        if "Reel" in mode:
            self.preview_mode = "reel"
        else:
            self.preview_mode = "source"
        self._render_visual_crop()

    def _on_fade_switch_changed(self):
        self._render_visual_crop()
        self._update_cli_display()

    def _get_selected_font_size(self) -> int:
        val = self.size_menu.get() if hasattr(self, "size_menu") else "22"
        if "18" in val:
            return 18
        elif "26" in val:
            return 26
        elif "30" in val:
            return 30
        return 22

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
        self._render_visual_crop()
        self._update_cli_display()

    def _on_pos_slider_changed(self, value: float, refresh=True):
        self.current_pos_x = round(float(value), 3)
        pct = int(round(self.current_pos_x * 100))
        label = "Center (50%)"
        if pct < 50:
            label = f"Left ({pct}%)"
        elif pct > 50:
            label = f"Right ({pct}%)"
        self.pos_label_var.set(f"Horizontal Framing (X): {label}")

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

        if refresh:
            self._render_visual_crop()
            self._update_cli_display()

    def _on_vol_slider_changed(self, value: float):
        self.current_vol = float(value)
        pct = int(round(self.current_vol * 100))
        self.vol_label_var.set(f"Background Volume: {pct}% (Speech stays crisp)")
        self._update_cli_display()

    def _render_visual_crop(self):
        """Draws visual crop dynamically either as 9:16 Final Reel View or 16:9 Source Cutout View."""
        if not self.video_info:
            return

        in_w = self.video_info.get("width", 1920)
        in_h = self.video_info.get("height", 1080)

        crop_filter, crop_w, crop_h, off_x, off_y = compute_crop_filter(
            in_w=in_w,
            in_h=in_h,
            ratio=self.current_ratio,
            pos_x=self.current_pos_x,
            pos_y=self.current_pos_y,
            scale=self.current_scale,
            pad_top=self.current_pad_top,
            pad_bottom=self.current_pad_bottom
        )

        fade_status = "ON (🖤)" if self.bottom_fade_var.get() else "OFF"
        pad_status = f"Pad: T:{self.current_pad_top:.1f}% B:{self.current_pad_bottom:.1f}%"
        self.filter_info_label.configure(
            text=f"FFmpeg: {crop_filter}\nFraming: Scale {self.current_scale:.2f}x | X {int(self.current_pos_x*100)}% | Y {int(self.current_pos_y*100)}% | {pad_status} | Fade: {fade_status}"
        )

        target_display_w = 480
        target_display_h = 260

        if self.preview_mode == "reel":
            # --- 📱 9:16 FINAL REEL PREVIEW ---
            bg = Image.new("RGBA", (target_display_w, target_display_h), (18, 22, 32, 255))
            d_bg = ImageDraw.Draw(bg)

            reel_h = 256
            reel_w = int(reel_h * (9 / 16))
            rx1 = (target_display_w - reel_w) // 2
            rx2 = rx1 + reel_w
            ry1 = 2
            ry2 = ry1 + reel_h

            pad_t_px = int(round(reel_h * (self.current_pad_top / 100.0)))
            pad_b_px = int(round(reel_h * (self.current_pad_bottom / 100.0)))
            vid_disp_h = max(32, reel_h - pad_t_px - pad_b_px)

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
                "target_w": reel_w,
                "target_h": reel_h
            }

            d_bg.rectangle([rx1, ry1, rx2, ry2], fill=(0, 0, 0, 255))

            if self.base_frame_image:
                crop_box = (off_x, off_y, off_x + crop_w, off_y + crop_h)
                try:
                    cropped_frame = self.base_frame_image.crop(crop_box).resize((reel_w, vid_disp_h), Image.Resampling.LANCZOS)
                except Exception:
                    cropped_frame = self.base_frame_image.resize((reel_w, vid_disp_h), Image.Resampling.LANCZOS)
            else:
                cropped_frame = Image.new("RGBA", (reel_w, vid_disp_h), (40, 50, 70, 255))

            bg.paste(cropped_frame, (rx1, ry1 + pad_t_px))

            # Draw bottom black fade gradient if enabled
            if self.bottom_fade_var.get() and vid_disp_h > 20:
                fade_h = max(6, int(vid_disp_h * self.fade_height))
                fade_top = vid_disp_h - fade_h
                col = Image.new("RGBA", (1, fade_h), (0, 0, 0, 0))
                for y_idx in range(fade_h):
                    norm = y_idx / max(1, fade_h - 1)
                    alpha = int(255 * self.fade_opacity * (norm ** 1.35))
                    col.putpixel((0, y_idx), (0, 0, 0, min(255, max(0, alpha))))
                fade_img = col.resize((reel_w, fade_h), Image.Resampling.NEAREST)
                bg.paste(fade_img, (rx1, ry1 + pad_t_px + fade_top), fade_img)

            # Draw sample caption text on preview
            draw_cap = ImageDraw.Draw(bg)
            sample_text = "सिद्धि तो है। हम बढ़ रहे हैं"
            try:
                cap_font = ImageFont.truetype("/System/Library/Fonts/Kohinoor.ttc", 13)
            except Exception:
                try:
                    cap_font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 12)
                except Exception:
                    cap_font = ImageFont.load_default()

            try:
                bbox = draw_cap.textbbox((0, 0), sample_text, font=cap_font)
                tw = bbox[2] - bbox[0]
            except Exception:
                tw = len(sample_text) * 7

            tx = rx1 + max(2, (reel_w - tw) // 2)
            in_black_bar = hasattr(self, "caption_pos_menu") and "Inside Black" in self.caption_pos_menu.get()
            if in_black_bar and pad_b_px > 16:
                ty = ry1 + reel_h - max(12, int(pad_b_px / 2) + 6)
            else:
                ty = ry1 + pad_t_px + vid_disp_h - max(12, int(vid_disp_h * 0.12))

            for ox, oy in [(-1, -1), (1, -1), (-1, 1), (1, 1), (0, 1), (0, -1), (1, 0), (-1, 0)]:
                draw_cap.text((tx + ox, ty + oy), sample_text, font=cap_font, fill=(0, 0, 0, 255))
            txt_color = (255, 255, 0, 255) if "Yellow" in self.color_menu.get() else (255, 255, 255, 255)
            draw_cap.text((tx, ty), sample_text, font=cap_font, fill=txt_color)

            # Glowing Reel border
            d_bg.rectangle([rx1, ry1, rx2, ry2], outline=(99, 102, 241, 255), width=2)

            # Draw side annotations
            try:
                side_font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 11)
            except Exception:
                side_font = ImageFont.load_default()

            d_bg.text((12, 40), "📱 9:16 Final Reel", font=side_font, fill=(165, 180, 252, 255))
            d_bg.text((12, 65), f"Top Pad: {self.current_pad_top:.1f}%", font=side_font, fill=(148, 163, 184, 255))
            d_bg.text((12, 85), f"Bottom Pad: {self.current_pad_bottom:.1f}%", font=side_font, fill=(148, 163, 184, 255))
            if self.current_pad_bottom > self.current_pad_top:
                d_bg.text((12, 115), "⬆️ Shifted Upward", font=side_font, fill=(52, 211, 153, 255))
            elif self.current_pad_top == 0 and self.current_pad_bottom == 0:
                d_bg.text((12, 115), "↔️ Full Screen 9:16", font=side_font, fill=(148, 163, 184, 255))

            rx_info = rx2 + 16
            d_bg.text((rx_info, 40), f"Zoom: {self.current_scale:.2f}x", font=side_font, fill=(165, 180, 252, 255))
            d_bg.text((rx_info, 65), f"Pan X: {int(self.current_pos_x*100)}%", font=side_font, fill=(148, 163, 184, 255))
            d_bg.text((rx_info, 85), f"Pan Y: {int(self.current_pos_y*100)}%", font=side_font, fill=(148, 163, 184, 255))
            d_bg.text((rx_info, 115), f"Fade: {fade_status}", font=side_font, fill=(96, 165, 250, 255) if self.bottom_fade_var.get() else (148, 163, 184, 255))

            img_composite = bg.convert("RGB")
        else:
            # --- 📐 16:9 SOURCE CUTOUT VIEW ---
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

            if self.base_frame_image:
                img = self.base_frame_image.copy().resize((disp_w, disp_h), Image.Resampling.LANCZOS)
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

            if self.bottom_fade_var.get() and (box_y2 > box_y1) and (box_x2 > box_x1):
                box_h = box_y2 - box_y1
                fade_h = max(4, int(box_h * self.fade_height))
                fade_top = max(box_y1, box_y2 - fade_h)
                actual_fade_h = box_y2 - fade_top
                fade_col = Image.new("RGBA", (1, actual_fade_h), (0, 0, 0, 0))
                for y_idx in range(actual_fade_h):
                    norm = y_idx / max(1, actual_fade_h - 1)
                    alpha = int(255 * self.fade_opacity * (norm ** 1.35))
                    fade_col.putpixel((0, y_idx), (0, 0, 0, min(255, max(0, alpha))))
                fade_box = fade_col.resize((box_x2 - box_x1, actual_fade_h), Image.Resampling.NEAREST)
                img_composite.paste(fade_box, (box_x1, fade_top), fade_box)

            draw_box = ImageDraw.Draw(img_composite)
            draw_box.rectangle([box_x1, box_y1, box_x2, box_y2], outline=(99, 102, 241, 255), width=3)

        ctk_img = ctk.CTkImage(light_image=img_composite, dark_image=img_composite, size=(target_display_w, target_display_h))
        self.canvas_label.configure(image=ctk_img, text="", cursor="hand2")

    # ==============================================================
    # COMMAND GENERATION & CLIPBOARD
    # ==============================================================
    def _generate_command_string(self) -> str:
        vid = self.video_entry.get().strip() or "video.mp4"
        base, ext = os.path.splitext(vid)
        out_name = f"{base}_{self.current_ratio.replace(':', '')}{ext or '.mp4'}"

        parts = [
            sys.executable,
            f'"{str(BASE_DIR / "process_full.py")}"',
            f'-v "{vid}"',
            f'-o "{out_name}"',
            f'-r {self.current_ratio}',
            f'--scale {self.current_scale:.2f}',
            f'--pos-x {self.current_pos_x:.2f}',
            f'--pos-y {self.current_pos_y:.2f}'
        ]

        if self.current_pad_top > 0:
            parts.append(f'--pad-top {self.current_pad_top:.1f}')
        if self.current_pad_bottom > 0:
            parts.append(f'--pad-bottom {self.current_pad_bottom:.1f}')

        if self.bottom_fade_var.get():
            parts.append('--bottom-fade')

        srt = self.srt_entry.get().strip()
        if self.sub_switch_var.get() and srt and os.path.exists(srt):
            parts.append(f'-s "{srt}"')
            f_size = self._get_selected_font_size()
            parts.append(f'--font-size {f_size}')
            if "Yellow" in self.color_menu.get():
                parts.append('--font-color "&H0000FFFF"')
            if hasattr(self, "caption_pos_menu") and "Inside Black" in self.caption_pos_menu.get():
                parts.append('--sub-margin 35')

        music = self.music_entry.get().strip()
        if self.music_switch_var.get() and music and os.path.exists(music):
            parts.append(f'-m "{music}"')
            parts.append(f'--volume {self.current_vol:.2f}')

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
        self.status_label.configure(text="⚡ Generating 1-Min preview with Apple Silicon hardware acceleration...")
        self.progress_bar.set(0.3)

        threading.Thread(target=self._run_preview_thread, daemon=True).start()

    def _run_preview_thread(self):
        try:
            start_off = float(self.offset_entry.get().strip() or "0")
            preview_file = str(PREVIEW_DIR / f"preview_{int(time.time())}.mp4")

            # Crop filter
            crop_filter, _, _, _, _ = compute_crop_filter(
                in_w=self.video_info["width"],
                in_h=self.video_info["height"],
                ratio=self.current_ratio,
                pos_x=self.current_pos_x,
                pos_y=self.current_pos_y,
                scale=self.current_scale,
                pad_top=self.current_pad_top,
                pad_bottom=self.current_pad_bottom
            )

            srt = self.srt_entry.get().strip()
            use_srt = self.sub_switch_var.get() and srt and os.path.exists(srt)

            music = self.music_entry.get().strip()
            use_music = self.music_switch_var.get() and music and os.path.exists(music)

            font_col = "&H0000FFFF" if "Yellow" in self.color_menu.get() else "&H00FFFFFF"
            font_sz = self._get_selected_font_size()
            sub_marg = 35 if (hasattr(self, "caption_pos_menu") and "Inside Black" in self.caption_pos_menu.get()) else None

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
                font_color=font_col,
                font_size=font_sz,
                bottom_fade=self.bottom_fade_var.get(),
                fade_height=self.fade_height,
                fade_opacity=self.fade_opacity,
                sub_margin_v=sub_marg
            )

            preview_path = preview_file
            self.after(0, lambda p=preview_path: self._on_preview_finished(p))

        except Exception as e:
            err_msg = str(e)
            self.after(0, lambda err=err_msg: self._on_preview_error(err))

    def _on_preview_finished(self, preview_file: str):
        self.is_rendering = False
        self.btn_preview.configure(state="normal")
        self.progress_bar.set(1.0)
        self.status_label.configure(text=f"✅ Preview Ready! Opened in Mac player: {os.path.basename(preview_file)}")

        # Launch preview in native macOS player (QuickTime / default viewer)
        subprocess.run(["open", preview_file], check=False)

    def _on_preview_error(self, err_msg: str):
        self.is_rendering = False
        self.btn_preview.configure(state="normal")
        self.progress_bar.set(0)
        self.status_label.configure(text=f"❌ Preview Error")
        messagebox.showerror("Preview Failed", err_msg)

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
        self.progress_bar.set(1.0)
        self.status_label.configure(text="🎉 Full video completed successfully!")
        messagebox.showinfo("Render Complete", "Your full video has been processed at maximum hardware speed!")

    def _on_full_error(self, err_msg: str):
        self.is_rendering = False
        self.btn_run_full.configure(state="normal")
        self.btn_preview.configure(state="normal")
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
            if longs:
                self.batch_count_label.configure(
                    text=f"Detected: {len(entries)} clips ({len(shorts)} Shorts, {len(longs)} Long 16:9)",
                    text_color="#38bdf8"
                )
            else:
                self.batch_count_label.configure(
                    text=f"Detected: {len(entries)} clips (All Shorts)",
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

            # Pre-compute Shorts crop filter (user chosen ratio: 9:16 Shorts/Reels, 1:1, etc.)
            shorts_crop_filter, _, _, _, _ = compute_crop_filter(
                in_w=in_w,
                in_h=in_h,
                ratio=self.current_ratio,
                pos_x=self.current_pos_x,
                pos_y=self.current_pos_y,
                scale=self.current_scale,
                pad_top=self.current_pad_top,
                pad_bottom=self.current_pad_bottom
            )

            # For long clips > 3 min: keep 16:9 unscaled original video (YouTube friendly)
            # If video is already 16:9 (like 720p / 1080p), no crop is needed at all (crop_filter=None)
            if abs(in_aspect - (16.0 / 9.0)) < 0.02:
                yt_crop_filter = None
            else:
                yt_crop_filter, _, _, _, _ = compute_crop_filter(
                    in_w=in_w,
                    in_h=in_h,
                    ratio="16:9",
                    pos_x=0.5,
                    pos_y=0.5,
                    scale=1.0
                )

            srt = self.srt_entry.get().strip()
            use_srt = self.sub_switch_var.get() and srt and os.path.exists(srt)
            music = self.music_entry.get().strip()
            use_music = self.music_switch_var.get() and music and os.path.exists(music)
            font_col = "&H0000FFFF" if "Yellow" in self.color_menu.get() else "&H00FFFFFF"
            font_sz = self._get_selected_font_size()
            sub_marg = 35 if (hasattr(self, "caption_pos_menu") and "Inside Black" in self.caption_pos_menu.get()) else None

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
                    chosen_crop = yt_crop_filter
                    ratio_tag = "16:9 Original (Unscaled)"
                    dur_display = f"{dur/60:.1f}m"
                else:
                    chosen_crop = shorts_crop_filter
                    ratio_tag = f"{self.current_ratio} Shorts"
                    dur_display = f"{dur:.1f}s"

                status_txt = f"[{idx}/{total}] 🎬 Cutting [{ratio_tag}]: {entry['title']} ({dur_display})..."
                self.after(0, lambda p=progress_val, s=status_txt: self._update_batch_progress(p, s))

                def check_cancel():
                    return self.is_cancelled

                def set_proc(p):
                    self.current_proc = p

                # Render video clip with chosen ratio, music ducking & subtitles
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
                    font_color=font_col,
                    font_size=font_sz,
                    bottom_fade=self.bottom_fade_var.get() and not is_long,
                    fade_height=self.fade_height,
                    fade_opacity=self.fade_opacity,
                    sub_margin_v=sub_marg,
                    cancel_check=check_cancel,
                    proc_callback=set_proc
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
        if hasattr(self, "btn_run_full"):
            self.btn_run_full.configure(state="normal")
        self.batch_progress_bar.set(0)
        self.batch_status_label.configure(text=f"❌ Error: {err[:60]}", text_color="#ef4444")
        messagebox.showerror("Batch Cut Failed", err)


if __name__ == "__main__":
    app = StudioClipperApp()
    app.mainloop()
