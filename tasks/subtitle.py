"""Feature 4: speech-to-text and subtitle engine (owner: member 4)."""
from __future__ import annotations

import itertools
import math
import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from core.base_task import BaseMediaTask
from core.errors import ClipHubError, InputError, ToolFailedError, ToolNotFoundError
from core.ffmpeg import probe_duration, run_ffmpeg

# "auto" lets Whisper detect the language: forcing Thai on a Japanese song produced nonsense.
LANGUAGE_NAMES = {
    "auto": "ตรวจจับอัตโนมัติ", "th": "ภาษาไทย", "en": "ภาษาอังกฤษ", "ja": "ภาษาญี่ปุ่น",
    "ko": "ภาษาเกาหลี", "zh": "ภาษาจีน",
}
# Smallest to most accurate. large-v3-turbo is close to large-v3 at several times the speed.
MODEL_SIZES = ("tiny", "base", "small", "medium", "large-v3-turbo", "large-v3")
DEFAULT_MODEL = "large-v3-turbo"
CONTENT_TYPES = ("speech", "music")  # music: no speech filter, no carrying text between lines

MAX_CUE_CHARS = 42      # one comfortable subtitle line
MAX_CUE_SECONDS = 6.0
# Lines Whisper is known to invent over silence or music (from its YouTube training data).
HALLUCINATIONS = (
    "ขอบคุณที่รับชม", "ฝากกดติดตาม", "กดติดตามช่อง", "thank you for watching", "thanks for watching",
    "subtitles by", "please subscribe", "ご視聴ありがとうございました", "チャンネル登録", "字幕",
)
SUBTITLE_SUFFIXES = (".srt", ".vtt")

EXTRACT_END = 30      # progress % when the audio track has been extracted
TRANSCRIBE_END = 95   # progress % when the last segment has been transcribed


def _split_time(seconds: float) -> tuple[int, int, int, int]:
    """Round to whole milliseconds first so 59.9996 carries into the next minute."""
    if math.isfinite(seconds) and seconds > 0:
        total_ms = math.floor(seconds * 1000 + 0.5)
    else:
        total_ms = 0
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return hours, minutes, secs, millis


def format_srt_time(seconds: float) -> str:
    """HH:MM:SS,mmm"""
    hours, minutes, secs, millis = _split_time(seconds)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def format_vtt_time(seconds: float) -> str:
    """HH:MM:SS.mmm"""
    hours, minutes, secs, millis = _split_time(seconds)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


@dataclass(frozen=True)
class Cue:
    start: float  # seconds
    end: float    # seconds
    text: str


def _clean_text(text: str) -> str:
    """Strip every line and drop blank ones (a blank line would end the cue early)."""
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _usable(cues: Iterable[Cue]) -> list[Cue]:
    return [Cue(c.start, c.end, text) for c in cues if (text := _clean_text(c.text))]


def _escape_vtt(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_srt(cues: Iterable[Cue]) -> str:
    return "".join(
        f"{index}\n{format_srt_time(cue.start)} --> {format_srt_time(cue.end)}\n{cue.text}\n\n"
        for index, cue in enumerate(_usable(cues), start=1)
    )


def render_vtt(cues: Iterable[Cue]) -> str:
    body = "".join(
        f"{format_vtt_time(cue.start)} --> {format_vtt_time(cue.end)}\n{_escape_vtt(cue.text)}\n\n"
        for cue in _usable(cues)
    )
    return "WEBVTT\n\n" + body


def write_subtitles(cues: Iterable[Cue], path: str | Path) -> Path:
    """Write .srt or .vtt (chosen by suffix) as UTF-8 without BOM, via a .part file."""
    target = Path(path)
    suffix = target.suffix.lower()
    if suffix == ".srt":
        content = render_srt(cues)
    elif suffix == ".vtt":
        content = render_vtt(cues)
    else:
        raise InputError("รองรับเฉพาะไฟล์ซับไตเติลนามสกุล .srt และ .vtt")
    partial = target.with_name(target.name + ".part")
    try:
        partial.write_text(content, encoding="utf-8", newline="\n")
        partial.replace(target)
    except OSError as exc:
        partial.unlink(missing_ok=True)
        raise ClipHubError(f"บันทึกไฟล์ซับไตเติลไม่สำเร็จ: {exc.strerror or exc}") from exc
    return target


def transcribe_options(language: str, content: str) -> dict:
    """faster-whisper settings for the chosen language and kind of audio."""
    options: dict = {
        "language": None if language == "auto" else language,
        "beam_size": 5,
        "word_timestamps": True,  # timing per word: lines start when the words do
    }
    if content == "music":
        # The speech filter (VAD) is trained on talking and cuts out singing; carrying the previous
        # line into the next makes Whisper repeat lyrics over and over.
        options.update(vad_filter=False, condition_on_previous_text=False)
    else:
        options.update(vad_filter=True, vad_parameters={"min_silence_duration_ms": 500})
    return options


def looks_hallucinated(segment, text: str) -> bool:
    """Whisper's invented lines: very likely silence, or a stock YouTube phrase on its own."""
    if getattr(segment, "no_speech_prob", 0.0) > 0.6 and getattr(segment, "avg_logprob", 0.0) < -1.0:
        return True
    lowered = text.lower()
    return len(text) <= 40 and any(phrase in lowered for phrase in HALLUCINATIONS)


def segment_cues(segment, max_chars: int = MAX_CUE_CHARS, max_seconds: float = MAX_CUE_SECONDS) -> list[Cue]:
    """One Whisper segment as subtitle cues: timed by its words, long ones split into readable lines."""
    text = segment.text.strip()
    if not text or looks_hallucinated(segment, text):
        return []
    words = [w for w in (getattr(segment, "words", None) or []) if w.word.strip()]
    if not words:
        return [Cue(float(segment.start), float(segment.end), text)]
    if len(text) <= max_chars and words[-1].end - words[0].start <= max_seconds:
        return [Cue(float(words[0].start), float(words[-1].end), text)]
    cues: list[Cue] = []
    chunk: list = []
    for word in words:
        if chunk:
            joined = "".join(w.word for w in chunk).strip()
            too_long = len(joined) + len(word.word.strip()) > max_chars
            too_slow = word.end - chunk[0].start > max_seconds
            if too_long or too_slow:
                cues.append(Cue(float(chunk[0].start), float(chunk[-1].end), joined))
                chunk = []
        chunk.append(word)
    if chunk:
        cues.append(Cue(float(chunk[0].start), float(chunk[-1].end), "".join(w.word for w in chunk).strip()))
    return cues


def _import_whisper_model():
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise ToolNotFoundError(
            "ไม่พบไลบรารี faster-whisper กรุณาติดตั้งด้วยคำสั่ง pip install faster-whisper"
        ) from exc
    return WhisperModel


class SubtitleTask(BaseMediaTask):
    kind: ClassVar[str] = "subtitle"
    title: ClassVar[str] = "ถอดเสียงซับไตเติล"

    def __init__(
        self,
        input_path: str,
        output_path: str,
        language: str = "th",
        model_size: str = "base",
        write_file: bool = True,
        content: str = "speech",
    ) -> None:
        super().__init__(input_path, output_path)
        self.language = language
        self.model_size = model_size
        self.write_file = write_file
        self.content = content
        self.cues: tuple[Cue, ...] = ()
        self.device_label = "CPU"
        self.detected_language = ""  # what Whisper heard, e.g. "ja"
        self.display_name = Path(input_path).name  # shown on the queue card

    def summary(self) -> str:
        return f"{LANGUAGE_NAMES.get(self.language, self.language)} · {self.model_size}"

    def validate(self) -> None:
        super().validate()
        if self.language not in LANGUAGE_NAMES:
            raise InputError("ภาษาที่เลือกไม่รองรับ")
        if self.model_size not in MODEL_SIZES:
            raise InputError("ขนาดโมเดลไม่ถูกต้อง")
        if self.content not in CONTENT_TYPES:
            raise InputError("ประเภทเสียงไม่ถูกต้อง")
        if Path(self.output_path).suffix.lower() not in SUBTITLE_SUFFIXES:
            raise InputError("ไฟล์ปลายทางต้องมีนามสกุล .srt หรือ .vtt")

    def process(self) -> Path:
        whisper_model = _import_whisper_model()  # fail before spending time on ffmpeg
        duration = probe_duration(self.input_path)

        wav = self._temp_wav()
        self.report_progress(0, "กำลังแยกเสียงจากไฟล์")
        run_ffmpeg(
            [
                "-i", self.input_path, "-vn", "-ac", "1", "-ar", "16000",
                "-c:a", "pcm_s16le", "-f", "wav", str(wav),
            ],
            duration=duration,
            on_progress=lambda fraction: self.report_progress(
                fraction * EXTRACT_END, "กำลังแยกเสียงจากไฟล์"
            ),
            is_cancelled=self._cancel_event.is_set,
        )
        self.check_cancelled()

        self.report_progress(EXTRACT_END, "กำลังโหลดโมเดล AI (ครั้งแรกต้องดาวน์โหลดจากอินเทอร์เน็ต)")
        model, where = self._load_model(whisper_model)
        self.check_cancelled()

        options = transcribe_options(self.language, self.content)
        try:
            segments, info = model.transcribe(str(wav), **options)
            segments = iter(segments)
            first = next(segments, None)  # CUDA libraries fail here, not when the model loads
        except RuntimeError:
            if where == "CPU":
                raise
            model, where = self._load_model(whisper_model, force_cpu=True)
            segments, info = model.transcribe(str(wav), **options)
            segments = iter(segments)
            first = next(segments, None)
        self.device_label = where
        self.detected_language = getattr(info, "language", "") or ""
        segments = itertools.chain([first] if first is not None else [], segments)
        cues: list[Cue] = []
        for segment in segments:
            self.check_cancelled()
            cues.extend(segment_cues(segment))
            done = min(float(segment.end) / duration, 1.0)
            self.report_progress(
                EXTRACT_END + (TRANSCRIBE_END - EXTRACT_END) * done,
                f"กำลังถอดเสียง ({self.device_label}) · {len(cues)} ประโยค",
            )
        self.check_cancelled()

        self.cues = tuple(cues)
        if self.write_file:
            self.report_progress(TRANSCRIBE_END, "กำลังบันทึกไฟล์ซับไตเติล")
            write_subtitles(self.cues, self.output_path)
        return Path(self.output_path)

    def resource(self) -> str | None:
        return "whisper"  # one speech model at a time: it already uses the whole CPU or GPU

    def _load_model(self, whisper_model, force_cpu: bool = False):
        """CUDA (float16) when the GPU and its libraries are usable, else int8 on every CPU core."""
        from core import hwaccel
        attempts = []
        if not force_cpu and hwaccel.report().whisper_gpu:
            attempts.append(({"device": "cuda", "compute_type": "float16"}, "GPU"))
        attempts.append(({"device": "cpu", "compute_type": "int8", "cpu_threads": os.cpu_count() or 4},
                         f"CPU {os.cpu_count() or 4} เธรด"))
        last_error: Exception | None = None
        for kwargs, where in attempts:
            try:
                return whisper_model(self.model_size, **kwargs), where
            except Exception as exc:  # e.g. CUDA libraries missing: try the next device
                last_error = exc
        raise ToolFailedError(
            f"โหลดโมเดล AI ({self.model_size}) ไม่สำเร็จ "
            f"หากใช้งานครั้งแรกกรุณาตรวจสอบการเชื่อมต่ออินเทอร์เน็ต: {last_error}"
        ) from last_error

    def _temp_wav(self) -> Path:
        fd, name = tempfile.mkstemp(prefix="cliphub_", suffix=".wav")
        os.close(fd)
        return self.register_temp(Path(name))

