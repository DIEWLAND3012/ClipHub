# Changelog

All notable changes to ClipHUB. Format follows Keep a Changelog.

## [Unreleased]

## [2.2.0] - 2026-11-18

In-app updates, the GPU pack as an optional download, and uninstall from the settings.

### Added
- App updates: ClipHUB checks GitHub quietly at start. A red dot on Settings (and next to the version inside) shows a newer version; "Update to vX" downloads it on a boot-style "Update" screen, then the app closes and reopens as the new version. Never forced. Any other published version can be installed from the list (go back).
- GPU pack (optional, Windows + NVIDIA): NVIDIA's cuBLAS and cuDNN for AI subtitles on CUDA, downloaded from the settings (about 1.3 GB, checked against PyPI's SHA-256) and removable again. Replaces `pip install -r requirements-gpu.txt` for the installed app.
- Uninstall ClipHUB from the settings: removes the app, FFmpeg, the GPU pack, the AI subtitle models and the settings.
- The settings are ordered: version, GPU pack, use the graphics card, which card, language, uninstall.
- The version number at the bottom-right of the boot screen.

### Changed
- One version source, `core/version.py`; CI names the installers from it.
- Downloads (FFmpeg, updates, GPU pack) resume on a new connection when a CDN connection stalls or crawls.

## [2.1.0] - 2026-11-18

FastCut, compress any file, FFmpeg that installs itself, and a better queue.

### Fixed
- The theme switch in the sidebar grew very tall in full screen and its thumb slipped out after a theme change; segmented controls now keep a fixed height.

### FastCut (new tool)
- Bigger preview: on wide windows the picture fills its column and the settings sit beside it (no black bars); on narrow ones the settings move below the timeline. The play button sits beside the timeline and the time is shown on the picture.
- A fifth tool, between Compress and Subtitles: cut part of a video or a song. The preview plays with a playhead; the timeline shows the sound as bars under a gradient box, picked the way Instagram picks a music clip: drag the box, stretch either edge, or type the length (default 20 s). The chosen part plays on a loop.
- Thai name ตัดคลิป (FastCut in English). The editor (preview, play button, timeline) spans the full width at every window size; the preview height follows the window so the timeline always fits. Settings sit below it.
- Cuts are always frame-accurate (video re-encoded on the GPU when there is one; audio stream-copied).
- Preview quality Full, 1/2, 1/4, 1/8 like Premiere Pro: a smaller proxy copy is made in the background for smooth playback on slower computers (remembered; proxies deleted on exit).
- Keys: Space plays / pauses; Left / Right or A / D move the trim box 1 s (Shift: 5 s).
- Trim box, handles and play button use the app accent colour; explanatory notes removed.
- Choose the folder and the file name, then Export.

### Boot screen
- "© 2026 DIEWLAND STUDIO" at the bottom.

### Compress any file
- The Compress page now takes any file. Videos work as before; images (JPG/PNG/WebP/BMP/TIFF/GIF) are re-encoded and large ones scaled down, PDFs and Office documents (DOCX/XLSX/PPTX/ODT/EPUB) get their pictures shrunk and are repacked, audio gets a lower bitrate (WAV/FLAC become FLAC or MP3), and anything else is packed into a lossless .zip.
- High / Balanced / Small apply to every kind; the file card shows details for each kind (dimensions, pages, pictures inside, duration).
- A result is never bigger than the original. New dependencies: Pillow and pikepdf.

### Queue
- Finished jobs get a "Show file" button that opens the folder with the file selected.
- Each finished job has a trash button that removes just that job from the queue (the file stays).
- "Clear" now removes every job; waiting and running jobs are cancelled first.

### FFmpeg installs itself
- On start, ClipHUB checks for FFmpeg; if the computer has none, it downloads it during the boot screen ("Loading required resources… 42%") into its own data folder (no admin rights). Windows: gyan.dev essentials build; macOS: martin-riedl.de (arm64/Intel). Downloads are checked against their SHA-256.
- The result is remembered in ffmpeg.json, so later starts skip the check (under 1 ms).
- The copy ClipHUB installed is updated in the background (checked every 3 days) and swapped in on the next start; an FFmpeg the user installed is never touched.
- Offline on first start: the boot screen says so, the app still opens, and the next start tries again.

## [2.0.1] - 2026-11-17

### Fixed
- The startup chime (assets/sounds/startup.wav) is now in the repo, so the released .exe and .dmg play it on the boot screen.

## [2.0.0] - 2026-11-17

A new ClipHUB: liquid-glass redesign, step-by-step tools, GPU acceleration, Thai/English, accurate song subtitles.

### App icon
- The new ClipHUB icon (assets/icons/cliphub.svg) is used on the title bar, the taskbar (own Windows app ID, so not Python's icon), the sidebar, the boot screen, the README, and as the .exe / .app icon (Explorer, desktop, Start menu, Dock).
- scripts/build_icons.py renders the SVG into cliphub.ico, cliphub.icns and cliphub.png; scripts/make_desktop_shortcut.py adds a desktop shortcut for running from source.

### Subtitles: much better results on songs
- Language can be detected automatically (now the default) and Japanese, Korean and Chinese were added; forcing Thai/English on a song in another language produced nonsense.
- New "Speech | Music" choice: music turns off the speech filter (it cut out singing) and stops Whisper repeating lyrics.
- Models medium, large-v3-turbo (new default) and large-v3 added.
- Lines are timed by word timestamps, long lines are split into readable subtitles, and invented lines ("Thanks for watching", "ขอบคุณที่รับชม" over silence) are dropped.
- Measured on ZUTOMAYO "Fig Smoke" (4 min, Japanese): before, 1 garbled Thai line; after, 49 lines of Japanese lyrics in 113 s on a GTX 1660 Ti.

### Settings and language
- "ประสิทธิภาพ" is now "การตั้งค่า" (Settings) with a gear icon; same GPU options plus a TH | EN sliding switch.
- The whole app switches between Thai and English live (pages, queue, progress, notifications, tooltips); the choice is remembered. Translation lives in ui/i18n.py.
- Action-bar summaries no longer include file names, which could be very long.

### Start-up and sizing
- Boot screen like a Mac starting up (black, the ClipHUB mark, a thin progress bar) that does real work while it shows: loads yt-dlp and Whisper and tests the GPUs, then cross-fades into the window. Plays assets/sounds/startup.wav when present (kept out of git).
- The window opens at about two thirds of the screen, centred, instead of a fixed large size.

### Fixed
- Narrow windows clipped the right column and the drop zone; columns now stack below and every box shrinks with the window.
- Long file names (e.g. Japanese titles) widened the subtitle page; they now shorten with an ellipsis.
- Turning the GPU switch off and on squashed the Performance popup; it now resizes smoothly.

### Layout
- The queue is no longer a column: a "คิวงาน" button with a job count (above "ประสิทธิภาพ" in the sidebar, Ctrl+J) opens it as a popup; the progress pill opens it too.
- Pages use the freed width in two columns and fit without scrolling at the default window size; columns stack on narrow windows.
- The action button sits in one fixed bar at the bottom right of every page, with the summary beside it.
- Converter, compressor and subtitles start with the same drop zone; after a file is chosen the cards rise in one by one. The compressor shows the file's details and gets an editable file name.
- Performance has a single setting: use the graphics card, plus which card when several work (locked when only one does). Jobs always run in parallel as the hardware allows.

### Downloader
- Page follows the decision order in one card: 1 folder, 2 link, 3 video or audio, 4 file format, 5 resolution / bitrate.
- Video formats MP4, MOV, MKV, WebM; resolutions 144p to 2160p (4K) plus Source. Resolutions a clip does not have are greyed out after fetching its details.
- Audio formats MP3, M4A, WAV, FLAC, Opus; bitrates 64 to 320 kbps plus Source (WAV/FLAC are lossless, no bitrate).
- MP4 prefers H.264 + AAC so it plays everywhere; MOV copies H.264 up to 1080p and re-encodes VP9/AV1 above that.
- Folder and last choices are remembered. No fetch button: the clip appears by itself once a valid link is typed or pasted.

### Converter
- Converts both video and audio files. Dropping or picking a file shows all its details: the container as a large badge, duration, size, bitrate, video codec, resolution, frame rate, audio codec, sample rate and channels.
- Steps: 1 video or audio only, 2 format (MP4, MOV, MKV, WebM, AVI / MP3, M4A, WAV, FLAC, Opus, AAC), 3 codec and quality (H.264, H.265, AV1, VP9, ProRes or Keep original; quality, resolution, frame rate, audio codec and bitrate), 4 output name.
- Keep original copies streams without re-encoding and is only offered when the source codec fits the target; codecs the local FFmpeg lacks are disabled. H.265 in MP4/MOV is tagged hvc1 so Apple devices play it.
- Files can be dropped anywhere on the page, also to replace the current one. Queue cards show the file name.

### File names
- Downloader step 6 and converter step 4 are an editable file name, prefilled with the name the app would use ('<title> 1080p', '<file>_converted'); "ใช้ชื่อเดิม" restores it. Names are made safe for Windows/macOS and get ' (2)' instead of overwriting an existing file.
- Converter no longer offers quality levels: video is always encoded at high quality (lower presets broke up the picture).

### Performance: CPU + GPU
- Hardware encoders are found at startup with a short test encode per GPU (NVIDIA NVENC, Intel Quick Sync, AMD AMF, Apple VideoToolbox; several NVIDIA cards each count separately). Problems are explained, e.g. an NVIDIA driver that is too old.
- Video jobs (converter, compressor, MOV re-encode in the downloader) run on the GPU at the same quality as the CPU (SSIM within 0.001), up to 3 at once per GPU and spread over GPUs; the CPU takes codecs no GPU supports. If a GPU refuses a file the job finishes on the CPU instead.
- The queue runs several jobs at once (automatic, or 1 to 3 in the new Performance panel), downloads alongside conversions. Measured: three 1080p conversions 15.2 s on the CPU one by one, 5.8 s on the Intel GPU in parallel.
- Subtitles use CUDA when the NVIDIA libraries from requirements-gpu.txt are installed (measured with the small model on a GTX 1660 Ti: 23.0 s on 12 CPU threads, 9.3 s on the GPU), otherwise all CPU threads.
- Primary action buttons sit on the right of each page.

### Responsive layout and motion
- Below 1180 px the queue becomes a drawer that springs in from a "คิวงาน" button (with job count) at the top right; a dimmed backdrop, Esc or the close button dismisses it. Below 940 px the sidebar collapses to icons.
- Window minimum size lowered to 760 x 600; long chip rows clip instead of pushing content under the panels.
- Sidebar selection glides between items with a slight spring; glass cards brighten on hover; progress bars glide; clip details slide open; new queue cards fade in; primary buttons glow when pressed; the queue button pops when a job is added.

### Fixed
- Dropdown items turned white-on-white when hovered.
- Downloader test failed on Windows because the FFmpeg folder was built with backslashes.

### Changed
- New "liquid glass" look inspired by Apple apps: floating frosted sidebar and queue rail, rim-lit glass cards over a soft colour backdrop, capsule buttons, Apple-style segmented controls and toggles, system blue accent.
- Minimal pages: title-only headers, explanatory sub-text and hints removed (the model download note is now a tooltip), content in one centred column.

- Fonts: SF Pro + Sukhumvit Set on macOS; on other platforms the bundled open look-alikes Inter + Noto Sans Thai (OFL, `assets/fonts/`).

- All page styles now come from one theme in `ui/theme.py`; per-page stylesheets were removed and shared widgets live in `ui/widgets.py`.

### Added
- Light / dark / follow-system appearance switch at the bottom of the sidebar. The choice is saved and the native title bar follows it.
- Floating progress pill at the bottom of the window for the running job: name, status, progress bar, percent, how many jobs are waiting, and cancel.
- Themed toast notifications replace the old message boxes for finished jobs, errors and subtitle export, with "Show in folder" / "View transcript" actions. Errors stay until closed.
- The queue lists the running and newest jobs at the top.
- Animations: sliding segmented-control thumb, page fade, light/dark cross-fade.
- Liquid-glass buttons (gradient with a lit top edge) and rounded dropdown popups.
- "Clear finished" button in the task queue.
- Ctrl+1 to Ctrl+4 shortcuts to switch between tools.
