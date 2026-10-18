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

2. **📐 Interactive Aspect Ratio, Scale (Zoom In/Out), & Frame Padding (Shift Clip Up):**
   - Choose **9:16 (Shorts/Reels)**, **1:1 (Square)**, **4:5 (Portrait)**, or **16:9 (Original)**.
   - **Scale / Zoom Slider:** Scale up (>1.0x) to zoom in on speaker faces, or scale down (<1.0x) to fit widescreen video. Includes quick `Reset 1.0x` button.
   - **Horizontal (X) & Vertical (Y) Framing Sliders:** Position the crop box freely in both axes with nudge `-` / `+` buttons and quick `Center` buttons.
   - **🎬 Frame Padding (Top & Bottom Letterbox / Shift Upward):**
     - **Quick Presets:** `🎬 Shift Up (8% / 14%)` (instantly matches modern viral Reels with top 8% and bottom 14% letterbox, shifting the clip upward), `↕️ Centered (10% / 10%)`, and `Full (0%)`.
     - **Top & Bottom Padding Sliders:** Fine-tune top padding (0% - 30%) and bottom padding (0% - 40%) with `-` / `+` nudge buttons.
   - **📱 Dual-Mode Live Preview:**
     - Toggle between **`📱 Final Reel View (9:16)`** (shows the exact phone-framed Reel with padding, video, bottom fade, and caption preview) and **`📐 16:9 Source Cutout`** (shows raw footage with crop cutout box).
     - **Interactive 2D Drag & Zoom:** Drag directly on the preview to pan the video (X & Y), and scroll mouse wheel to Zoom in/out.
   - **🖤 Bottom Black Fade (Caption Contrast):** Toggleable cinematic bottom shadow/gradient (38% height, smooth ease curve) positioned below subtitles that seamlessly dissolves into the bottom black letterbox with zero seam!

3. **💬 Subtitles & Captions / Lyrics Styling:**
   - Choose subtitle text color: White with Black Border or Yellow with Black Border.
   - Choose font size: Compact (18pt), Medium (22pt), Large (26pt), Extra Large (30pt).
   - **Caption Placement:** Choose `Caption: On Video Fade (Above Padding)` (recommended modern Reel look) or `Caption: Inside Black Bottom Bar`.

4. **🎵 Background Music Ducking Slider:**
   - Adjust volume between 0% and 50% (default 12%).
   - Speech dialogue remains 100% crisp and clear using `amix` with `normalize=0`.

5. **🎬 1-Minute Preview:**
   - Click **"🎬 Generate 1-Min Preview"**.
   - Generates in ~2-3 seconds using Apple Silicon VideoToolbox and automatically opens in your Mac's default video player (QuickTime Player) to review crop, sound balance, fade contrast, padding, and subtitles.

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
  --scale 1.00 \
  --pos-x 0.5 \
  --pos-y 0.5 \
  --pad-top 8.0 \
  --pad-bottom 14.0 \
  --bottom-fade \
  -s "/path/to/master.srt" \
  --font-size 22 \
  -m "/path/to/music.mp3" \
  --volume 0.12
```
