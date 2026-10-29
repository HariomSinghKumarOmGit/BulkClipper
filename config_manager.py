"""
config_manager.py - Persistent Configuration Manager for Studio Video Clipper

Automatically saves and restores user preferences across app sessions:
- Shorts & Videos aspect ratios, zoom/scale, X/Y pan, frame padding
- Bottom black fade settings (enabled, height spread %, darkness opacity %)
- Subtitle styling presets (Soothe White Light, Viral Yellow, Frosted Pill Box, etc.)
- Audio ducking volume levels
- Batch clipping preferences
"""

import os
import json
import copy
from pathlib import Path
from typing import Dict, Any

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "clipper_config.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    "active_mode": "shorts",
    "shorts_config": {
        "video_path": "",
        "ratio_str": "9:16 (Shorts/Reels)",
        "ratio_val": "9:16",
        "scale": 1.0,
        "pos_x": 0.5,
        "pos_y": 0.5,
        "pad_top": 8.0,
        "pad_bottom": 14.0,
        "bottom_fade": True,
        "fade_height": 0.38,
        "fade_opacity": 0.92,
    },
    "videos_config": {
        "video_path": "",
        "ratio_str": "16:9 (Original)",
        "ratio_val": "16:9",
        "scale": 1.0,
        "pos_x": 0.5,
        "pos_y": 0.5,
        "pad_top": 0.0,
        "pad_bottom": 0.0,
        "bottom_fade": False,
        "fade_height": 0.38,
        "fade_opacity": 0.92,
    },
    "subtitles": {
        "enabled": True,
        "path": "",
        "style": "✨ Soothe White Light (Soft Clean Glow)",
        "size": "Medium (22pt)",
        "position": "Caption: On Video Fade (Above Padding)"
    },
    "music": {
        "enabled": True,
        "path": "",
        "volume": 0.12
    },
    "skip_existing": True
}


def load_saved_config() -> Dict[str, Any]:
    """Loads configuration from clipper_config.json, deep-merging with defaults."""
    if not CONFIG_PATH.exists():
        return copy.deepcopy(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        merged = copy.deepcopy(DEFAULT_CONFIG)
        for k, v in data.items():
            if isinstance(v, dict) and k in merged and isinstance(merged[k], dict):
                merged[k].update(v)
            else:
                merged[k] = v
        # Backwards compatibility: migrate legacy vol in shorts_config/videos_config to universal music.volume
        if "volume" not in merged["music"]:
            legacy_vol = data.get("shorts_config", {}).get("vol", 0.12)
            merged["music"]["volume"] = legacy_vol

        return merged
    except Exception as e:
        print(f"Notice: Using default settings ({e})")
        return copy.deepcopy(DEFAULT_CONFIG)


def save_saved_config(config: Dict[str, Any]) -> bool:
    """Atomically saves configuration to clipper_config.json."""
    try:
        tmp_path = CONFIG_PATH.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, CONFIG_PATH)
        return True
    except Exception as e:
        print(f"Warning: Could not save config to {CONFIG_PATH}: {e}")
        return False
