# Studio Video Clipper & Hardware Cropper (Offline Mac Desktop App)

A 100% offline, native macOS desktop application for selecting local videos, visual aspect-ratio cropping (9:16 Shorts/Reels, 1:1, 4:5), subtitle burning, background music ducking, and 1-minute previews—powered by Apple Silicon Media Engine (`h264_videotoolbox`) hardware acceleration.

---

## 🖥️ How to Launch the Offline Desktop App

### Option A: Double-Click from Desktop (Easiest)
- **Launch App Window:** Double-click **`Launch_Clipper.command`** on your Desktop.
- **1-Click Folder Auto-Run:** Double-click **`1_CLICK_RUN_FOLDER.command`** on your Desktop, select your project folder, and it automatically clips all segments!

### Option B: From Terminal
```bash
cd /Users/hariomsingh/Desktop/mycliper
source .venv/bin/activate
python3 studio_app.py
```

---

## 🎨 What You Can Do in the App

1. **⚡ 1-Click Folder Auto-Run:**
   - Click **`📂 Choose Folder & Auto-Run Everything`** at the top of the app.
   - Select any folder that contains your video, subtitles/lyrics, and timestamps.
   - Everything is auto-detected and clipped in one single action!

2. **📁 Select Local Files Directly with Native Finder Dialogs:**
   - **Video:** Click `📁 Choose File` to browse any `.mp4`, `.mov`, `.mkv` on your Mac.
   - **Subtitles & Lyrics:** Click `📁 Choose File` to browse any `.srt`, `.ass`, `.lrc`, or `.vtt` file on your Mac.
   - **Background Music:** Click `📁 Choose Music` to browse your `.mp3` or `.wav`.

2. **📐 Interactive Aspect Ratio, Scale (Zoom In/Out), & Live 2D Visual Framing:**
   - Choose **9:16 (Shorts/Reels)**, **1:1 (Square)**, **4:5 (Portrait)**, or **16:9 (Original)**.
   - **Scale / Zoom Slider:** Scale up (>1.0x) to zoom in on speaker faces, or scale down (<1.0x) to fit full widescreen video with padding. Includes quick `Reset 1.0x` button.
   - **Horizontal (X) & Vertical (Y) Framing Sliders:** Position the crop box freely in both axes with nudge `-` / `+` buttons and quick `Center` buttons.
   - **Interactive Live Preview Canvas:** Click & drag directly on the video snapshot in any direction (X & Y) to frame, and scroll mouse wheel to zoom in and out.
   - **🖤 Bottom Black Fade (Caption Contrast):** Toggleable cinematic bottom shadow/gradient (38% height, smooth ease curve) positioned below subtitles for 100% text readability and high contrast, exactly as seen in modern Shorts/Reels.

3. **💬 Subtitles & Captions / Lyrics Styling:**
   - Choose subtitle text color: White with Black Border or Yellow with Black Border.
   - Choose font size: Compact (18pt), Medium (22pt), Large (26pt), Extra Large (30pt).

4. **🎵 Background Music Ducking Slider:**
   - Adjust volume between 0% and 50% (default 12%).
   - Speech dialogue remains 100% crisp and clear using `amix` with `normalize=0`.

5. **🎬 1-Minute Preview:**
   - Click **"🎬 Generate 1-Min Preview"**.
   - Generates in ~2-3 seconds using Apple Silicon VideoToolbox and automatically opens in your Mac's default video player (QuickTime Player) to review crop, sound balance, fade contrast, and subtitles.

6. **🚀 Run on Whole Video (Even 10+ Hours Long) Without UI:**
   - The app automatically generates the exact terminal command.
   - Click **"📋 Copy Command"** to paste into your terminal, or click **"⚡ Run Whole Video in Background"** to encode directly with live speed reporting.

---

## 💻 Standalone Headless Command Reference

```bash
python3 process_full.py \
  -v "/path/to/video.mp4" \
  -o "output_916.mp4" \
  -r 9:16 \
  --scale 1.30 \
  --pos-x 0.5 \
  --pos-y 0.35 \
  --bottom-fade \
  -s "/path/to/master.srt" \
  --font-size 22 \
  -m "/path/to/music.mp3" \
  --volume 0.12
```
