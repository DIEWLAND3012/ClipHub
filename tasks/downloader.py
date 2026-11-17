"""Feature 1: video downloader (owner: member 1)."""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import parse_qs, urlparse

from core.base_task import BaseMediaTask
from core.errors import InputError, TaskCancelledError, ToolFailedError
from core.ffmpeg import find_tool, probe_codecs, probe_duration, run_ffmpeg
from core.hwaccel import encoder_args
from core.naming import safe_filename, strip_extension, unique_path

# Video resolutions from smallest up, then "source" = the best stream the video has.
QUALITIES = ("144p", "240p", "360p", "480p", "720p", "1080p", "1440p", "2160p", "source")
DEFAULT_QUALITY = "1080p"
SOURCE = "source"
QUALITY_LABELS = {"2160p": "2160p · 4K", "1440p": "1440p · 2K", "1080p": "1080p · Full HD", "720p": "720p · HD",
                  "source": "ต้นฉบับ"}

VIDEO_FORMATS = ("mp4", "mov", "mkv", "webm")
AUDIO_FORMATS = ("mp3", "m4a", "wav", "flac", "opus")
LOSSLESS_AUDIO = ("wav", "flac")
AUDIO_BITRATES = ("320", "256", "192", "128", "96", "64")  # kbps, then "source"
DEFAULT_AUDIO_BITRATE = "320"
H264_MAX_HEIGHT = 1080  # YouTube serves H.264 up to 1080p; above that only VP9 / AV1
INVALID_URL_MESSAGE = "ลิงก์ YouTube ไม่ถูกต้อง"

_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_MB = 1024 * 1024


def normalize_youtube_url(url: str) -> str:
    url = url.strip()
    return url if _SCHEME.match(url) else f"https://{url}"


def is_valid_youtube_url(url: str) -> bool:
    """Accepts watch?v=, youtu.be/ and shorts/ links with a 11-character id, with or without scheme."""
    if not isinstance(url, str):
        return False
    text = url.strip()
    if not text or any(ch.isspace() for ch in text):
        return False
    try:
        parsed = urlparse(normalize_youtube_url(text))
        host = (parsed.hostname or "").lower()
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    if host == "youtu.be":
        return bool(_VIDEO_ID.fullmatch(parsed.path.removeprefix("/").removesuffix("/")))
    if host not in _YOUTUBE_HOSTS:
        return False
    if parsed.path.rstrip("/") == "/watch":
        values = parse_qs(parsed.query).get("v", [""])
        return bool(_VIDEO_ID.fullmatch(values[0]))
    if parsed.path.startswith("/shorts/"):
        return bool(_VIDEO_ID.fullmatch(parsed.path.removeprefix("/shorts/").removesuffix("/")))
    return False


def _video_height(quality: str) -> int | None:
    """Height limit for a quality key, None for "source". Raises InputError for unknown keys."""
    key = (quality or "").strip().lower()
    if key in (SOURCE, "best"):  # "best" is the old name of "source"
        return None
    if key in QUALITIES:
        return int(key.removesuffix("p"))
    raise InputError(f"ไม่รองรับความละเอียด: {quality}")


def _audio_kbps(bitrate: str) -> int | None:
    """kbps for an audio quality key, None for "source"."""
    key = str(bitrate or "").strip().lower().removesuffix("k")
    if key == SOURCE:
        return None
    if key in AUDIO_BITRATES:
        return int(key)
    raise InputError(f"ไม่รองรับบิตเรตเสียง: {bitrate}")


def build_format_selector(quality: str, audio_only: bool, file_format: str = "", audio_bitrate: str = SOURCE) -> str:
    """yt-dlp format string for the chosen type, container and quality."""
    fmt = (file_format or ("mp3" if audio_only else "mp4")).lower()
    if audio_only:
        if fmt not in AUDIO_FORMATS:
            raise InputError(f"ไม่รองรับรูปแบบเสียง: {fmt}")
        keep = _audio_kbps(audio_bitrate) is None
        # yt-dlp copies audio whose codec already matches the target and then ignores the bitrate,
        # so a chosen bitrate starts from the other codec to make sure it is really encoded.
        if fmt == "m4a":
            return "bestaudio[ext=m4a]/bestaudio/best" if keep else "bestaudio[acodec^=opus]/bestaudio/best"
        if fmt == "opus":
            return "bestaudio[acodec^=opus]/bestaudio/best" if keep else "bestaudio[ext=m4a]/bestaudio/best"
        return "bestaudio/best"
    if fmt not in VIDEO_FORMATS:
        raise InputError(f"ไม่รองรับรูปแบบวิดีโอ: {fmt}")
    height = _video_height(quality)
    cap = "" if height is None else f"[height<={height}]"
    if fmt == "webm":  # WebM holds VP9 / AV1 + Opus, never H.264 / AAC
        return f"bestvideo{cap}[vcodec!^=avc1]+bestaudio[acodec^=opus]/bestvideo{cap}+bestaudio/best{cap}"
    if height is None:
        return "bestvideo+bestaudio/best"
    return f"bestvideo{cap}+bestaudio/best{cap}"


def audio_postprocessor(file_format: str, audio_bitrate: str) -> dict[str, Any]:
    """FFmpegExtractAudio settings: lossless formats have no bitrate, "source" copies when it can."""
    fmt = file_format.lower()
    kbps = _audio_kbps(audio_bitrate)
    processor: dict[str, Any] = {"key": "FFmpegExtractAudio", "preferredcodec": fmt}
    if fmt in LOSSLESS_AUDIO:
        return processor
    if kbps is not None:
        processor["preferredquality"] = str(kbps)
    elif fmt == "mp3":
        processor["preferredquality"] = "0"  # YouTube has no MP3, so "source" means the best VBR encode
    return processor


def suggest_file_name(title: str, audio_only: bool, quality: str, heights: list[int] | None = None) -> str:
    """The name the app proposes before the user edits it: '<title> 1080p' for video, '<title>' for audio."""
    base = safe_filename(title) or "ClipHUB"
    if audio_only:
        return base
    try:
        height = _video_height(quality)
    except InputError:
        height = None
    if height is None and heights:
        height = max(heights)
    return f"{base} {height}p" if height else base


def needs_mov_transcode(video_codec: str, audio_codec: str) -> bool:
    """MOV only takes H.264 + AAC from what YouTube serves; anything else must be re-encoded."""
    return video_codec != "h264" or audio_codec not in ("aac", "")


def describe_choice(audio_only: bool, file_format: str, quality: str, audio_bitrate: str) -> str:
    """Short text such as '1080p · MP4', 'ต้นฉบับ · MKV' or '320 kbps · MP3'."""
    fmt = file_format.upper()
    if audio_only:
        if file_format.lower() in LOSSLESS_AUDIO:
            return f"Lossless · {fmt}"
        kbps = _audio_kbps(audio_bitrate)
        return f"{'ต้นฉบับ' if kbps is None else f'{kbps} kbps'} · {fmt}"
    height = _video_height(quality)
    return f"{'ต้นฉบับ' if height is None else f'{height}p'} · {fmt}"


def parse_metadata(info: dict[str, Any]) -> dict[str, Any]:
    """Pick the fields the UI shows out of yt-dlp's info dict."""
    heights = {
        int(fmt["height"])
        for fmt in info.get("formats") or []
        if fmt.get("height") and fmt.get("vcodec") != "none"
    }
    if not heights and info.get("height"):
        heights.add(int(info["height"]))
    thumbnail = info.get("thumbnail") or ""
    if not thumbnail:
        thumbs = [t.get("url") for t in info.get("thumbnails") or [] if t.get("url")]
        thumbnail = thumbs[-1] if thumbs else ""
    return {
        "title": info.get("title") or "",
        "duration": int(info.get("duration") or 0),
        "thumbnail": thumbnail,
        "uploader": info.get("uploader") or info.get("channel") or "",
        "heights": sorted(heights),
    }


def format_duration(seconds: int) -> str:
    """12:34 or 1:02:03."""
    seconds = max(int(seconds), 0)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def fetch_metadata(url: str) -> dict[str, Any]:
    """Blocking network call: run it off the GUI thread."""
    if not is_valid_youtube_url(url):
        raise InputError(INVALID_URL_MESSAGE)
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError

    options = {"quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True, "socket_timeout": 20}
    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(normalize_youtube_url(url), download=False)
    except DownloadError as exc:
        raise ToolFailedError(f"ดึงข้อมูลวิดีโอไม่สำเร็จ: {_clean_error(exc)}") from exc
    if not info:
        raise ToolFailedError("ดึงข้อมูลวิดีโอไม่สำเร็จ: ไม่พบข้อมูลวิดีโอ")
    return parse_metadata(info)


def _clean_error(exc: BaseException) -> str:
    text = _ANSI.sub("", str(exc)).strip().removeprefix("ERROR:").strip()
    return text[:300] or "ไม่ทราบสาเหตุ"


def _final_path(info: Any) -> Path | None:
    """Where yt-dlp left the finished file (after merge / audio extraction)."""
    if not isinstance(info, dict):
        return None
    for entry in info.get("requested_downloads") or []:
        found = entry.get("filepath") or entry.get("_filename")
        if found:
            return Path(found)
    found = info.get("filepath") or info.get("_filename")
    return Path(found) if found else None


class DownloaderTask(BaseMediaTask):
    kind: ClassVar[str] = "download"
    title: ClassVar[str] = "ดาวน์โหลดวิดีโอ"
    fetch_metadata = staticmethod(fetch_metadata)

    def __init__(
        self,
        url: str,
        output_dir: str,
        quality: str = DEFAULT_QUALITY,
        audio_only: bool = False,
        video_title: str = "",
        file_format: str = "",
        audio_bitrate: str = DEFAULT_AUDIO_BITRATE,
        file_name: str = "",
    ) -> None:
        super().__init__(url, output_dir)
        self.file_name = file_name  # chosen by the user, without extension; "" = name from the video title
        self.quality = quality
        self.audio_only = audio_only
        self.file_format = (file_format or ("mp3" if audio_only else "mp4")).lower()
        self.audio_bitrate = audio_bitrate
        # shown on the queue card: the user's file name, else the video title, else the link
        self.display_name = file_name.strip() or video_title.strip() or url.strip()
        self.downloaded_file: Path | None = None
        self._temp_dir: Path | None = None
        self._streams: dict[str, tuple[float, float]] = {}
        self._last_percent = 0
        self._download_share = 99  # % of the bar for the download itself; MOV re-encoding takes the rest

    def summary(self) -> str:
        try:
            return describe_choice(self.audio_only, self.file_format, self.quality, self.audio_bitrate)
        except InputError:
            return self.file_format.upper()

    def validate(self) -> None:
        if not is_valid_youtube_url(self.input_path):
            raise InputError(INVALID_URL_MESSAGE)
        if not str(self.output_path).strip():
            raise InputError("ยังไม่ได้เลือกโฟลเดอร์ปลายทาง")
        # rejects an unknown format, quality or bitrate before any network work
        build_format_selector(self.quality, self.audio_only, self.file_format, self.audio_bitrate)
        if self.audio_only:
            audio_postprocessor(self.file_format, self.audio_bitrate)
        if self.file_name.strip() and not safe_filename(self.file_name):
            raise InputError("ชื่อไฟล์ไม่ถูกต้อง")

    def _output_template(self, out_dir: Path) -> str:
        """The user's name (made safe and unique, % escaped for yt-dlp) or the title-based default."""
        if self.file_name.strip():
            stem = safe_filename(strip_extension(self.file_name.strip(), self.file_format))
            stem = unique_path(out_dir, stem, self.file_format).stem
            return stem.replace("%", "%%") + ".%(ext)s"
        if self.audio_only:
            return "%(title).180B [%(id)s].%(ext)s"
        return "%(title).180B [%(id)s] %(height)sp.%(ext)s"

    def resource(self) -> str | None:
        return "h264" if self._expects_transcode() else None  # only MOV above 1080p is re-encoded

    def _expects_transcode(self) -> bool:
        if self.audio_only or self.file_format != "mov":
            return False
        height = _video_height(self.quality)
        return height is None or height > H264_MAX_HEIGHT

    def process(self) -> Path:
        from yt_dlp import YoutubeDL
        from yt_dlp.utils import DownloadError

        ffmpeg_dir = os.path.dirname(find_tool("ffmpeg"))
        out_dir = Path(self.output_path).expanduser()
        self._streams = {}
        self._last_percent = 0
        self._download_share = 60 if self._expects_transcode() else 99
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            self._temp_dir = Path(tempfile.mkdtemp(prefix=".cliphub-", dir=out_dir))
        except OSError as exc:
            raise ToolFailedError(f"สร้างโฟลเดอร์ปลายทางไม่ได้: {exc}") from exc

        mov = not self.audio_only and self.file_format == "mov"
        options: dict[str, Any] = {
            "format": build_format_selector(self.quality, self.audio_only, self.file_format, self.audio_bitrate),
            # Relative template: "paths" puts the result in out_dir and keeps partial files in the temp dir.
            # Video names carry the height, so a second download at another quality is not skipped as "already downloaded".
            "outtmpl": self._output_template(out_dir),
            # MOV is muxed by ClipHUB afterwards, so the intermediate file stays in the temp dir.
            "paths": {"home": str(self._temp_dir if mov else out_dir), "temp": str(self._temp_dir)},
            "ffmpeg_location": ffmpeg_dir,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "windowsfilenames": True,
            "retries": 5,
            "fragment_retries": 5,
            "socket_timeout": 30,
            "progress_hooks": [self._on_download_progress],
            "postprocessor_hooks": [self._on_postprocess],
        }
        if self.audio_only:
            options["postprocessors"] = [audio_postprocessor(self.file_format, self.audio_bitrate)]
        elif mov:
            options["format_sort"] = ["res", "vcodec:h264", "acodec:aac"]  # H.264 + AAC copy straight into MOV
            options["merge_output_format"] = "mkv"  # holds any codec; converted to MOV below
        elif self.file_format == "webm":
            options["format_sort"] = ["res", "vcodec:vp9", "acodec:opus"]
            options["merge_output_format"] = "webm"
        else:
            # MP4 should play everywhere, so at equal resolution prefer H.264 + AAC over AV1 / VP9.
            sort = ["res", "vcodec:h264", "acodec:aac"] if self.file_format == "mp4" else ["res"]
            options["format_sort"] = sort
            options["merge_output_format"] = self.file_format

        self.report_progress(0, "กำลังเตรียมดาวน์โหลด…")
        try:
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(normalize_youtube_url(self.input_path), download=True)
        except TaskCancelledError:
            raise
        except DownloadError as exc:
            if self._cancel_event.is_set():
                raise TaskCancelledError("ยกเลิกงานแล้ว") from exc
            raise ToolFailedError(f"ดาวน์โหลดไม่สำเร็จ: {_clean_error(exc)}") from exc
        except OSError as exc:
            raise ToolFailedError(f"ดาวน์โหลดไม่สำเร็จ: {exc}") from exc

        self.check_cancelled()
        final = _final_path(info)
        if final is None or not final.is_file():
            raise ToolFailedError("ดาวน์โหลดไม่สำเร็จ: ไม่พบไฟล์ผลลัพธ์")
        if mov:
            final = self._to_mov(final, out_dir)
        self.downloaded_file = final
        return final

    def _to_mov(self, source: Path, out_dir: Path) -> Path:
        """Copy H.264 + AAC into MOV, or re-encode VP9 / AV1 / Opus to H.264 + AAC."""
        target = out_dir / f"{source.stem}.mov"
        partial = self.register_temp(target.with_name(target.name + ".part"))
        video, audio = probe_codecs(str(source))
        duration = probe_duration(str(source))
        start = self._last_percent
        if needs_mov_transcode(video, audio):
            message = "กำลังแปลงเป็น MOV (H.264)" + self.engine_note()
            if self.engine is not None and self.engine.is_gpu and "h264" in self.engine.codecs:
                video_opts = encoder_args(self.engine, "h264")
            else:
                video_opts = ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p"]
            codecs = [*video_opts, "-c:a", "aac", "-b:a", "256k"]
        else:
            message = "กำลังบันทึกเป็น MOV"
            codecs = ["-c", "copy"]
        self.report_progress(start, message)
        run_ffmpeg(
            ["-i", str(source), "-map", "0:v:0", "-map", "0:a:0?", *codecs, "-movflags", "+faststart",
             "-f", "mov", str(partial)],
            duration=duration,
            on_progress=lambda fraction: self.report_progress(start + (99 - start) * fraction, message),
            is_cancelled=self.is_cancel_requested,
        )
        self.check_cancelled()
        try:
            partial.replace(target)
        except OSError as exc:
            raise ToolFailedError(f"บันทึกไฟล์ MOV ไม่สำเร็จ: {exc}") from exc
        return target

    def cleanup(self) -> None:
        super().cleanup()
        if self._temp_dir is not None:
            shutil.rmtree(self._temp_dir, ignore_errors=True)
            self._temp_dir = None

    def _on_download_progress(self, data: dict[str, Any]) -> None:
        self.check_cancelled()
        status = data.get("status")
        if status not in ("downloading", "finished"):
            return
        key = str(data.get("filename") or data.get("tmpfilename") or "")
        done = float(data.get("downloaded_bytes") or 0)
        total = float(data.get("total_bytes") or data.get("total_bytes_estimate") or 0)
        if status == "finished":
            total = done = max(done, total)
        self._streams[key] = (done, total)
        # Video and audio arrive as separate streams, so sum them to keep the bar moving forward.
        done_sum = sum(d for d, _ in self._streams.values())
        total_sum = sum(t for _, t in self._streams.values())
        if total_sum > 0:
            share = self._download_share
            self._last_percent = max(self._last_percent, min(int(done_sum / total_sum * share), share))
            text = f"กำลังดาวน์โหลด {done_sum / _MB:.1f} / {total_sum / _MB:.1f} MB"
        else:
            text = f"กำลังดาวน์โหลด {done_sum / _MB:.1f} MB"
        speed = data.get("speed")
        if speed:
            text += f" · {speed / _MB:.1f} MB/s"
        self.report_progress(self._last_percent, text)

    def _on_postprocess(self, data: dict[str, Any]) -> None:
        self.check_cancelled()
        name = str(data.get("postprocessor") or "")
        if data.get("status") != "started":
            return
        if "ExtractAudio" in name:
            self.report_progress(max(self._last_percent, 99), f"กำลังบันทึกเป็น {self.file_format.upper()}…")
        elif "Merger" in name:
            self._last_percent = max(self._last_percent, self._download_share)
            self.report_progress(self._last_percent, "กำลังรวมวิดีโอและเสียง…")
