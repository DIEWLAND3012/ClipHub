# Changelog

All notable changes to ClipHUB. Format follows Keep a Changelog.

## [Unreleased]

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
