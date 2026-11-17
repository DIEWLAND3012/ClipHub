"""Feature 5: FastCut, cut a part out of a video or audio file with FFmpeg.

Two ways to cut:
  fast     stream copy: no re-encoding, done in a second or two whatever the length. A video then
           starts on the nearest keyframe at or before the chosen start (can be a little early).
  precise  re-encodes the video (H.264, GPU when the queue gives one) so it starts on the exact frame.
Audio files are always cut by stream copy; audio frames are tiny, so the cut is exact enough.
"""
from __future__ import annotations

import array
import os
import subprocess
import sys
from collections.abc import Collection
from pathlib import Path
from typing import ClassVar

from core.base_task import BaseMediaTask
from core.errors import InputError, ToolFailedError
from core.ffmpeg import _spawn_kwargs, find_tool, run_ffmpeg
from core.hwaccel import Engine, encoder_args

AUDIO_EXTS = {"mp3", "m4a", "aac", "wav", "flac", "ogg", "opus", "wma", "aif", "aiff"}
VIDEO_EXTS = {"mp4", "mov", "mkv", "avi", "webm", "m4v", "flv", "wmv", "ts", "mts", "m2ts", "3gp", "mpg", "mpeg"}
PRECISE_CONTAINERS = {"mp4", "mov", "mkv", "m4v"}  # H.264 + AAC fit these; others become .mp4
MIN_LENGTH = 0.5  # seconds
PRECISE_KBPS = 8000


def is_audio(path: str | Path) -> bool:
    return Path(path).suffix.lower().lstrip(".") in AUDIO_EXTS


def output_ext(path: str | Path, precise: bool) -> str:
    ext = Path(path).suffix.lower().lstrip(".") or "mp4"
    if precise and not is_audio(path) and ext not in PRECISE_CONTAINERS:
        return "mp4"
    return ext


def format_time(seconds: float, tenths: bool = True) -> str:
    """75.4 -> '1:15.4'; 3725 -> '1:02:05.0'."""
    seconds = max(0.0, round(seconds, 1 if tenths else 0))
    whole = int(seconds)
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    text = f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"
    if tenths:
        text += f".{int((seconds - whole) * 10)}"
    return text


def suggest_output_path(input_path: str, folder: str = "", precise: bool = False, taken: Collection[str] = ()) -> Path:
    source = Path(input_path)
    base = Path(folder) if folder else source.parent
    ext = output_ext(source, precise)
    reserved = {os.path.normcase(item) for item in taken}
    number = 1
    while True:
        suffix = "" if number == 1 else f"_{number}"
        candidate = base / f"{source.stem}_cut{suffix}.{ext}"
        if not candidate.exists() and os.path.normcase(str(candidate)) not in reserved:
            return candidate
        number += 1


def build_cut_args(input_path: str, partial_path: str, start: float, length: float, precise: bool,
                   engine: Engine | None = None, muxer: str = "") -> list[str]:
    """Arguments for core.ffmpeg.run_ffmpeg. The partial path must stay the last item."""
    head = ["-ss", f"{start:.3f}", "-i", input_path, "-t", f"{length:.3f}"]
    fmt = ["-f", muxer] if muxer else []
    if is_audio(input_path) or not precise:
        maps = ["-map", "0:a?"] if is_audio(input_path) else ["-map", "0:v:0?", "-map", "0:a?"]
        return [*head, *maps, "-c", "copy", "-avoid_negative_ts", "make_zero", "-map_metadata", "0",
                *fmt, partial_path]
    if engine is not None and engine.is_gpu and "h264" in engine.codecs:
        video = encoder_args(engine, "h264", PRECISE_KBPS)
    else:
        video = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p"]
    return [*head, "-map", "0:v:0", "-map", "0:a?", *video, "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", *fmt, partial_path]


_MUXERS = {"mp4": "mp4", "m4v": "mp4", "mov": "mov", "mkv": "matroska", "webm": "webm", "avi": "avi",
           "flv": "flv", "wmv": "asf", "ts": "mpegts", "mts": "mpegts", "m2ts": "mpegts", "3gp": "3gp",
           "mpg": "mpeg", "mpeg": "mpeg", "mp3": "mp3", "m4a": "ipod", "aac": "adts", "wav": "wav",
           "flac": "flac", "ogg": "ogg", "opus": "opus", "wma": "asf", "aif": "aiff", "aiff": "aiff"}


class FastCutTask(BaseMediaTask):
    kind: ClassVar[str] = "fastcut"
    title: ClassVar[str] = "FastCut"

    def __init__(self, input_path: str, output_path: str, start: float, end: float, precise: bool = False) -> None:
        super().__init__(input_path, output_path)
        self.start = start
        self.end = end
        self.precise = precise and not is_audio(input_path)
        self.display_name = Path(output_path).name

    @property
    def length(self) -> float:
        return self.end - self.start

    def summary(self) -> str:
        how = "แม่นยำ" if self.precise else "เร็ว"
        return f"{format_time(self.start)} – {format_time(self.end)} · {how}"

    def resource(self) -> str | None:
        return "h264" if self.precise else None

    def validate(self) -> None:
        super().validate()
        if self.start < 0 or self.length < MIN_LENGTH - 1e-6:
            raise InputError("ช่วงที่ตัดต้องยาวอย่างน้อย 0.5 วินาที")
        if Path(self.input_path).resolve() == Path(self.output_path).resolve():
            raise InputError("ไฟล์ปลายทางต้องไม่ใช่ไฟล์เดียวกับไฟล์ต้นฉบับ")

    def process(self) -> Path:
        Path(self.output_path).parent.mkdir(parents=True, exist_ok=True)
        partial = self.partial_output()
        muxer = _MUXERS.get(Path(self.output_path).suffix.lower().lstrip("."), "")

        def cut(engine: Engine | None, message: str) -> None:
            run_ffmpeg(
                build_cut_args(self.input_path, str(partial), self.start, self.length, self.precise, engine, muxer),
                duration=self.length,
                on_progress=lambda fraction: self.report_progress(fraction * 100, message),
                is_cancelled=self._cancel_event.is_set,
            )

        try:
            cut(self.engine, "กำลังตัด" + (self.engine_note() if self.precise else ""))
        except ToolFailedError:
            if not self.precise or self.engine is None or not self.engine.is_gpu or self._cancel_event.is_set():
                raise
            self.engine = None
            cut(None, "กำลังตัด · GPU ใช้ไม่ได้ ใช้ CPU แทน")
        self.check_cancelled()
        final = Path(self.output_path)
        partial.replace(final)
        return final


def waveform_peaks(path: str, bins: int = 240) -> list[float]:
    """Loudness of the first audio track in `bins` slices, 0.0-1.0 (empty when there is no audio)."""
    cmd = [find_tool("ffmpeg"), "-v", "error", "-i", path, "-map", "0:a:0", "-ac", "1", "-ar", "1000",
           "-f", "s16le", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=120, check=False, **_spawn_kwargs())
    except (OSError, subprocess.SubprocessError):
        return []
    samples = array.array("h")
    samples.frombytes(proc.stdout[: len(proc.stdout) // 2 * 2])
    if sys.byteorder == "big":
        samples.byteswap()
    if not samples:
        return []
    step = max(1, len(samples) // bins)
    peaks = []
    for index in range(0, len(samples), step):
        chunk = samples[index:index + step]
        peaks.append(sum(abs(v) for v in chunk) / len(chunk))  # average level reads better than the max
    top = max(peaks) or 1.0
    return [min(1.0, value / top) for value in peaks[:bins]]
