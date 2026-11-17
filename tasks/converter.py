"""Feature 2: format converter and audio separator (owner: member 2).

A target is chosen in steps: container (MP4, MOV, ... or MP3, M4A, ...), then codecs and quality
in ConvertSettings. Without settings the old defaults apply (H.264 CRF 20 + AAC 192k).
"""
from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, ClassVar

from core.base_task import BaseMediaTask
from core.errors import InputError, ToolFailedError
from core.ffmpeg import probe_duration, probe_media, run_ffmpeg
from core.hwaccel import Engine, encoder_args

VIDEO_TARGETS = ("mp4", "mov", "mkv", "webm", "avi")
AUDIO_TARGETS = ("mp3", "m4a", "wav", "flac", "opus", "aac")
SEPARATOR_FORMATS = ("mp3", "wav")  # SRS 3.2.2: separated audio is saved as MP3 or WAV

_VIDEO_MUXER = {"mp4": "mp4", "mkv": "matroska", "avi": "avi", "mov": "mov", "webm": "webm"}
# ext -> (default codec options, muxer). The muxer is explicit because temp names end in .part.
_AUDIO_ENCODE = {
    "mp3": (["-c:a", "libmp3lame", "-q:a", "2"], "mp3"),
    "wav": (["-c:a", "pcm_s16le"], "wav"),
    "aac": (["-c:a", "aac", "-b:a", "192k"], "adts"),
    "flac": (["-c:a", "flac"], "flac"),
    "m4a": (["-c:a", "aac", "-b:a", "192k"], "ipod"),
    "opus": (["-c:a", "libopus", "-b:a", "160k"], "ogg"),
}
LOSSLESS_AUDIO = ("wav", "flac")
# libx264 + yuv420p refuse odd frame sizes; this rounds them down to even numbers
_EVEN_SIZE = "scale=trunc(iw/2)*2:trunc(ih/2)*2"

# --- codecs ------------------------------------------------------------------------

COPY = "copy"
VIDEO_CODECS = {  # key -> (label, encoder)
    "h264": ("H.264", "libx264"),
    "h265": ("H.265 (HEVC)", "libx265"),
    "av1": ("AV1", "libsvtav1"),
    "vp9": ("VP9", "libvpx-vp9"),
    "prores": ("ProRes", "prores_ks"),
    COPY: ("คงเดิม", ""),
}
AUDIO_CODECS = {
    "aac": ("AAC", "aac"),
    "mp3": ("MP3", "libmp3lame"),
    "opus": ("Opus", "libopus"),
    "flac": ("FLAC", "flac"),
    "pcm": ("PCM", "pcm_s16le"),
    COPY: ("คงเดิม", ""),
}
# What each container can hold, best default first.
VIDEO_CODECS_FOR = {
    "mp4": ("h264", "h265", "av1", COPY),
    "mov": ("h264", "h265", "prores", COPY),
    "mkv": ("h264", "h265", "av1", "vp9", COPY),
    "webm": ("vp9", "av1", COPY),
    "avi": ("h264", COPY),
}
AUDIO_CODECS_FOR = {
    "mp4": ("aac", "mp3", COPY),
    "mov": ("aac", "pcm", COPY),
    "mkv": ("aac", "opus", "flac", "mp3", COPY),
    "webm": ("opus", COPY),
    "avi": ("aac", "mp3", COPY),
}
# Source codecs (ffprobe names) a container accepts without re-encoding.
COPYABLE_VIDEO = {
    "mp4": {"h264", "hevc", "av1", "mpeg4"},
    "mov": {"h264", "hevc", "prores", "mpeg4", "mjpeg"},
    "mkv": None,  # Matroska holds anything
    "webm": {"vp8", "vp9", "av1"},
    "avi": {"h264", "mpeg4", "mjpeg", "msmpeg4v3"},
}
COPYABLE_AUDIO = {
    "mp4": {"aac", "mp3", "alac", "ac3", "eac3"},
    "mov": {"aac", "mp3", "alac", "pcm_s16le", "pcm_s24le"},
    "mkv": None,
    "webm": {"opus", "vorbis"},
    "avi": {"mp3", "aac", "ac3", "pcm_s16le"},
    "mp3": {"mp3"},
    "m4a": {"aac", "alac"},
    "aac": {"aac"},
    "opus": {"opus"},
    "flac": {"flac"},
    "wav": {"pcm_s16le", "pcm_s24le", "pcm_f32le"},
}

QUALITIES = ("high", "balanced", "small")
QUALITY_LABELS = {"high": "คุณภาพสูง", "balanced": "สมดุล", "small": "ไฟล์เล็ก"}
# Constant-quality value per encoder (lower = better). "balanced" for H.264 is the historical CRF 20.
_CRF = {
    "h264": {"high": 17, "balanced": 20, "small": 25},
    "h265": {"high": 19, "balanced": 23, "small": 28},
    "av1": {"high": 26, "balanced": 32, "small": 40},
    "vp9": {"high": 24, "balanced": 31, "small": 38},
}
_PRORES_PROFILE = {"high": "3", "balanced": "2", "small": "0"}  # HQ, 422, Proxy
RESOLUTIONS = (2160, 1440, 1080, 720, 480, 360)  # downscale only; None keeps the source size
FRAME_RATES = (60, 30, 25, 24)
AUDIO_BITRATES = (320, 256, 192, 160, 128, 96, 64)


@dataclass(frozen=True)
class ConvertSettings:
    """How to encode. Empty codec strings mean "the container's default"."""

    video_codec: str = ""
    audio_codec: str = ""
    quality: str = "balanced"
    height: int | None = None       # max output height; never upscales
    fps: float | None = None        # None keeps the source frame rate
    audio_bitrate: int | None = None  # kbps; None uses the format default

    def resolved(self, ext: str) -> ConvertSettings:
        """Fill in the container defaults for an output extension."""
        video = self.video_codec or (VIDEO_CODECS_FOR[ext][0] if ext in VIDEO_CODECS_FOR else "")
        audio = self.audio_codec or (AUDIO_CODECS_FOR[ext][0] if ext in AUDIO_CODECS_FOR else "")
        return replace(self, video_codec=video, audio_codec=audio)


def normalize_ext(ext: str) -> str:
    return ext.strip().lower().lstrip(".")


def _unsupported(ext: str) -> InputError:
    return InputError(f"รูปแบบ .{ext} ไม่รองรับ" if ext else "รูปแบบ (ไม่มีนามสกุล) ไม่รองรับ")


def check_settings(ext: str, settings: ConvertSettings) -> None:
    """Reject codec / quality choices the target cannot use."""
    ext = normalize_ext(ext)
    s = settings.resolved(ext)
    if s.quality not in QUALITIES:
        raise InputError(f"ไม่รู้จักระดับคุณภาพ: {s.quality}")
    if ext in VIDEO_CODECS_FOR:
        if s.video_codec not in VIDEO_CODECS_FOR[ext]:
            raise InputError(f"ไฟล์ .{ext} ใช้โค้ดเดกวิดีโอ {VIDEO_CODECS.get(s.video_codec, (s.video_codec,))[0]} ไม่ได้")
        if s.audio_codec not in AUDIO_CODECS_FOR[ext]:
            raise InputError(f"ไฟล์ .{ext} ใช้โค้ดเดกเสียง {AUDIO_CODECS.get(s.audio_codec, (s.audio_codec,))[0]} ไม่ได้")
    if s.audio_bitrate is not None and s.audio_bitrate not in AUDIO_BITRATES:
        raise InputError(f"ไม่รองรับบิตเรตเสียง {s.audio_bitrate} kbps")


def copy_problem(ext: str, settings: ConvertSettings, info: dict[str, Any]) -> str:
    """Thai reason why "keep original" cannot be used with this source, or "" when it can."""
    ext = normalize_ext(ext)
    s = settings.resolved(ext)
    video, audio = info.get("video"), info.get("audio")
    if ext in VIDEO_CODECS_FOR and s.video_codec == COPY and video is not None:
        allowed = COPYABLE_VIDEO.get(ext)
        if allowed is not None and video["codec"] not in allowed:
            return f"วิดีโอ {video['codec'].upper()} ใส่ในไฟล์ .{ext} แบบคงเดิมไม่ได้ ให้เลือกโค้ดเดกอื่น"
    wants_audio_copy = s.audio_codec == COPY if ext in VIDEO_CODECS_FOR else settings.audio_codec == COPY
    if wants_audio_copy and audio is not None:
        allowed = COPYABLE_AUDIO.get(ext)
        if allowed is not None and audio["codec"] not in allowed:
            return f"เสียง {audio['codec'].upper()} ใส่ในไฟล์ .{ext} แบบคงเดิมไม่ได้ ให้เลือกแบบอื่น"
    return ""


def _video_options(ext: str, s: ConvertSettings, source_video_codec: str = "", engine: Engine | None = None) -> list[str]:
    codec = s.video_codec
    if codec == COPY:
        opts = ["-c:v", "copy"]
        if ext in ("mp4", "mov") and source_video_codec == "hevc":
            opts += ["-tag:v", "hvc1"]  # lets QuickTime and iOS play H.265
        return opts
    if s.height:
        scale = f"scale=-2:'trunc(min({s.height},ih)/2)*2'"  # downscale only, keep aspect, even size
    else:
        scale = _EVEN_SIZE
    opts = ["-vf", scale]
    if engine is not None and engine.is_gpu and codec in engine.codecs:
        opts += encoder_args(engine, codec)  # GPU encoder at matching quality
        if codec == "h265" and ext in ("mp4", "mov"):
            opts += ["-tag:v", "hvc1"]
    elif codec == "h264":
        opts += ["-c:v", "libx264", "-preset", "medium", "-crf", str(_CRF["h264"][s.quality]), "-pix_fmt", "yuv420p"]
    elif codec == "h265":
        opts += ["-c:v", "libx265", "-preset", "medium", "-crf", str(_CRF["h265"][s.quality]), "-pix_fmt", "yuv420p"]
        if ext in ("mp4", "mov"):
            opts += ["-tag:v", "hvc1"]
    elif codec == "av1":
        opts += ["-c:v", "libsvtav1", "-preset", "6", "-crf", str(_CRF["av1"][s.quality]), "-pix_fmt", "yuv420p"]
    elif codec == "vp9":
        opts += ["-c:v", "libvpx-vp9", "-b:v", "0", "-crf", str(_CRF["vp9"][s.quality]),
                 "-deadline", "good", "-cpu-used", "4", "-row-mt", "1", "-pix_fmt", "yuv420p"]
    elif codec == "prores":
        opts += ["-c:v", "prores_ks", "-profile:v", _PRORES_PROFILE[s.quality], "-pix_fmt", "yuv422p10le"]
    else:
        raise InputError(f"ไม่รู้จักโค้ดเดกวิดีโอ: {codec}")
    if s.fps:
        opts += ["-r", f"{s.fps:g}"]
    return opts


def _audio_options(codec: str, bitrate: int | None) -> list[str]:
    if codec == COPY:
        return ["-c:a", "copy"]
    if codec == "aac":
        return ["-c:a", "aac", "-b:a", f"{bitrate or 192}k"]
    if codec == "mp3":
        return ["-c:a", "libmp3lame", "-b:a", f"{bitrate}k"] if bitrate else ["-c:a", "libmp3lame", "-q:a", "2"]
    if codec == "opus":
        return ["-c:a", "libopus", "-b:a", f"{bitrate or 160}k"]
    if codec == "flac":
        return ["-c:a", "flac"]
    if codec == "pcm":
        return ["-c:a", "pcm_s16le"]
    raise InputError(f"ไม่รู้จักโค้ดเดกเสียง: {codec}")


def build_convert_args(
    input_path: str, partial_path: str, target_ext: str,
    settings: ConvertSettings | None = None, source_video_codec: str = "", engine: Engine | None = None,
) -> list[str]:
    """ffmpeg input/output options for converting into one supported target format."""
    ext = normalize_ext(target_ext)
    if ext in _VIDEO_MUXER:
        s = (settings or ConvertSettings()).resolved(ext)
        args = [
            "-i", input_path,
            "-map", "0:v:0?", "-map", "0:a?",  # skip subtitle/data streams MP4 and AVI cannot hold
            *_video_options(ext, s, source_video_codec, engine),
            *_audio_options(s.audio_codec, s.audio_bitrate),
        ]
        if ext == "mp4":
            args += ["-movflags", "+faststart"]
        return [*args, "-f", _VIDEO_MUXER[ext], partial_path]
    if ext in _AUDIO_ENCODE:
        default, muxer = _AUDIO_ENCODE[ext]
        if settings is None:
            codec_opts = default
        elif settings.audio_codec == COPY:
            codec_opts = ["-c:a", "copy"]
        elif ext in LOSSLESS_AUDIO or settings.audio_bitrate is None:
            codec_opts = default
        else:
            codec_opts = [*default[:2], "-b:a", f"{settings.audio_bitrate}k"]
        return ["-i", input_path, "-map", "0:a:0", "-vn", *codec_opts, "-f", muxer, partial_path]
    raise _unsupported(ext)


def build_extract_args(input_path: str, partial_path: str, audio_ext: str) -> list[str]:
    """ffmpeg options that copy the first audio stream out of a video as MP3 or WAV."""
    ext = normalize_ext(audio_ext)
    if ext not in SEPARATOR_FORMATS:
        raise InputError(f"รูปแบบเสียง .{ext} ไม่รองรับในโหมดแยกเสียง (รองรับเฉพาะ MP3 / WAV)")
    codec, muxer = _AUDIO_ENCODE[ext]
    return ["-i", input_path, "-map", "0:a:0", "-vn", *codec, "-f", muxer, partial_path]


def audio_sidecar_path(output_path: str | Path, audio_ext: str = "mp3") -> Path:
    """<output stem>_audio.<mp3|wav> in the same folder as the main output."""
    out = Path(output_path)
    return out.with_name(f"{out.stem}_audio.{normalize_ext(audio_ext)}")


def describe_conversion(source_ext: str, target_ext: str, sidecar_ext: str | None = None) -> str:
    """Text such as 'MKV → MP4 · + เสียง MP3' for the queue row and the page hint."""
    text = f"{normalize_ext(source_ext).upper() or '?'} → {normalize_ext(target_ext).upper()}"
    if sidecar_ext:
        text += f" · + เสียง {normalize_ext(sidecar_ext).upper()}"
    return text


def describe_settings(target_ext: str, settings: ConvertSettings) -> str:
    """'H.265 · 1080p' / 'คงเดิม' / '192 kbps' for the summary line."""
    ext = normalize_ext(target_ext)
    s = settings.resolved(ext)
    if ext in VIDEO_CODECS_FOR:
        if s.video_codec == COPY:
            return "คงเดิม (ไม่เข้ารหัสใหม่)"
        parts = [VIDEO_CODECS[s.video_codec][0].split(" ")[0]]
        if s.height:
            parts.append(f"{s.height}p")
        if s.fps:
            parts.append(f"{s.fps:g} fps")
        return " · ".join(parts)
    if settings.audio_codec == COPY:
        return "คงเดิม"
    if ext in LOSSLESS_AUDIO:
        return "Lossless"
    return f"{settings.audio_bitrate} kbps" if settings.audio_bitrate else "คุณภาพมาตรฐาน"


def suggest_output_path(
    input_path: str,
    target_ext: str,
    *,
    sidecar_ext: str | None = None,
    taken: Iterable[str | Path] = (),
) -> Path:
    """<stem>_converted.<ext> beside the input, then '(2)', '(3)'... until nothing collides.

    With sidecar_ext the audio sidecar name must be free too. `taken` lists paths that are
    queued but not written yet.
    """
    src = Path(input_path)
    ext = normalize_ext(target_ext)
    reserved = {os.path.normcase(str(p)) for p in taken}
    count = 1
    while True:
        tag = "_converted" if count == 1 else f"_converted ({count})"
        candidate = src.with_name(f"{src.stem}{tag}.{ext}")
        paths = [candidate]
        if sidecar_ext:
            paths.append(audio_sidecar_path(candidate, sidecar_ext))
        if not any(p.exists() or os.path.normcase(str(p)) in reserved for p in paths):
            return candidate
        count += 1


def _same_file(a: str | Path, b: str | Path) -> bool:
    try:
        if Path(a).exists() and Path(b).exists():
            return os.path.samefile(a, b)
    except OSError:
        pass
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


class ConverterTask(BaseMediaTask):
    kind: ClassVar[str] = "convert"
    title: ClassVar[str] = "แปลงไฟล์"

    def __init__(
        self,
        input_path: str,
        output_path: str,
        extract_audio: bool = False,
        audio_format: str = "mp3",
        settings: ConvertSettings | None = None,
    ) -> None:
        super().__init__(input_path, output_path)
        self.extract_audio = extract_audio
        self.audio_format = normalize_ext(audio_format)
        self.settings = settings
        self.display_name = Path(input_path).name  # shown on the queue card

    @property
    def target_ext(self) -> str:
        return normalize_ext(Path(self.output_path).suffix)

    @property
    def audio_output_path(self) -> Path | None:
        """Where the separated audio goes (None when extract_audio is off)."""
        if not self.extract_audio:
            return None
        return audio_sidecar_path(self.output_path, self.audio_format)

    def summary(self) -> str:
        sidecar = self.audio_format if self.extract_audio else None
        text = describe_conversion(Path(self.input_path).suffix, self.target_ext, sidecar)
        if self.settings is not None and self.target_ext in VIDEO_TARGETS + AUDIO_TARGETS:
            text += f" · {describe_settings(self.target_ext, self.settings)}"
        return text

    def validate(self) -> None:
        super().validate()
        if self.target_ext not in VIDEO_TARGETS + AUDIO_TARGETS:
            raise _unsupported(self.target_ext)
        if self.settings is not None:
            check_settings(self.target_ext, self.settings)
        outputs = [Path(self.output_path)]
        if self.extract_audio:
            if self.audio_format not in SEPARATOR_FORMATS:
                raise InputError(
                    f"รูปแบบเสียง .{self.audio_format} ไม่รองรับในโหมดแยกเสียง (รองรับเฉพาะ MP3 / WAV)"
                )
            outputs.append(self.audio_output_path)
        if any(_same_file(self.input_path, out) for out in outputs):
            raise InputError("ไฟล์ปลายทางซ้ำกับไฟล์ต้นทาง ระบบไม่เขียนทับต้นฉบับ")

    def resource(self) -> str | None:
        """The video codec this job encodes, so the queue can give it a GPU or the CPU."""
        if self.target_ext not in VIDEO_TARGETS:
            return None
        codec = (self.settings or ConvertSettings()).resolved(self.target_ext).video_codec
        return None if codec == COPY else codec

    def _uses_copy(self) -> bool:
        if self.settings is None:
            return False
        s = self.settings.resolved(self.target_ext)
        return COPY in (s.video_codec, s.audio_codec, self.settings.audio_codec)

    def process(self) -> Path:
        duration = probe_duration(self.input_path)
        source_video = ""
        if self._uses_copy():  # "keep original" only works when the container accepts the source codec
            info = probe_media(self.input_path)
            problem = copy_problem(self.target_ext, self.settings, info)
            if problem:
                raise InputError(problem)
            source_video = (info.get("video") or {}).get("codec", "")
        final = Path(self.output_path)

        part = self.partial_output()
        self._main_args = lambda engine: build_convert_args(
            self.input_path, str(part), self.target_ext, self.settings, source_video, engine)
        stages = [(
            final, part, self._main_args(self.engine),
            "กำลังแปลงไฟล์" + self.engine_note(), self.target_ext in AUDIO_TARGETS,
        )]
        sidecar = self.audio_output_path
        if sidecar is not None:
            side_part = self.register_temp(sidecar.with_name(sidecar.name + ".part"))
            stages.append((
                sidecar, side_part,
                build_extract_args(self.input_path, str(side_part), self.audio_format),
                "กำลังแยกเสียง", True,
            ))

        try:
            final.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise InputError(f"สร้างโฟลเดอร์ปลายทางไม่ได้: {exc}") from exc

        total = len(stages)
        for index, (_, _, args, message, audio_only) in enumerate(stages):
            self.check_cancelled()
            low, high = 100 * index / total, 100 * (index + 1) / total
            self.report_progress(low, message)
            try:
                self._run_stage(args, duration, low, high, message, audio_only)
            except ToolFailedError:
                if index != 0 or self.engine is None or not self.engine.is_gpu or self.is_cancel_requested():
                    raise
                # The GPU refused this file (driver, size, format): do it on the CPU instead.
                self.engine = None
                message = "กำลังแปลงไฟล์ · GPU ใช้ไม่ได้ ใช้ CPU แทน"
                self.report_progress(low, message)
                self._run_stage(self._main_args(None), duration, low, high, message, audio_only)

        # Publish only after every stage succeeded, so a failure never leaves half a result.
        for destination, partial, *_ in stages:
            try:
                partial.replace(destination)
            except OSError as exc:
                raise ToolFailedError(f"บันทึกไฟล์ปลายทางไม่สำเร็จ: {exc}") from exc
        return final

    def _run_stage(
        self, args: list[str], duration: float, low: float, high: float,
        message: str, audio_only: bool,
    ) -> None:
        try:
            run_ffmpeg(
                args,
                duration=duration,
                on_progress=lambda fraction: self.report_progress(
                    low + fraction * (high - low), message
                ),
                is_cancelled=self.is_cancel_requested,
            )
        except ToolFailedError as exc:
            text = f"{exc}\n{exc.stderr}"
            if audio_only and ("matches no streams" in text or "does not contain any stream" in text):
                raise ToolFailedError("ไฟล์นี้ไม่มีสตรีมเสียงให้แยก") from exc
            if "Unknown encoder" in text or "Encoder not found" in text:
                raise ToolFailedError("FFmpeg ในเครื่องนี้ไม่มีตัวเข้ารหัสที่เลือก ลองเลือกโค้ดเดกอื่น") from exc
            raise
