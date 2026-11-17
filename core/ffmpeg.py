"""FFmpeg / ffprobe helpers shared by Features 2-4.

find_tool() prefers a copy bundled with the app (bin/ in the repo during
development, or the PyInstaller bundle), then the copy ClipHUB downloaded itself
(core.ffmpeg_setup), and falls back to PATH.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from core.errors import TaskCancelledError, ToolFailedError, ToolNotFoundError


def _bundle_root() -> Path:
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass else Path(__file__).resolve().parent.parent


def _spawn_kwargs() -> dict[str, Any]:
    """No stdin and no console window flash when the packaged GUI app starts a tool."""
    kwargs: dict[str, Any] = {"stdin": subprocess.DEVNULL}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return kwargs


# A macOS app started from Finder or the Dock does not inherit the shell PATH,
# so Homebrew installs are invisible unless their folders are checked directly.
_EXTRA_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")


def managed_dir() -> Path:
    """Where ClipHUB keeps the FFmpeg it downloads itself (see core.ffmpeg_setup)."""
    from core.ffmpeg_setup import data_dir
    return data_dir() / "ffmpeg"


def find_tool(name: str) -> str:
    exe = f"{name}.exe" if sys.platform == "win32" else name
    for folder in (_bundle_root() / "bin", managed_dir() / "bin"):
        if (folder / exe).is_file():
            return str(folder / exe)
    found = shutil.which(name)
    if found is None:
        for directory in _EXTRA_DIRS:
            candidate = Path(directory) / exe
            if candidate.is_file():
                return str(candidate)
        raise ToolNotFoundError(f"ไม่พบโปรแกรม {name} กรุณาติดตั้ง FFmpeg ก่อนใช้งาน")
    return found


def probe_format(path: str) -> dict[str, Any]:
    """ffprobe's 'format' block: duration (str, seconds), bit_rate, size, format_name."""
    cmd = [
        find_tool("ffprobe"), "-v", "error",
        "-show_entries", "format=duration,bit_rate,size,format_name",
        "-of", "json", path,
    ]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False, **_spawn_kwargs(),
    )
    try:
        return json.loads(proc.stdout)["format"]
    except (KeyError, json.JSONDecodeError) as exc:
        raise ToolFailedError("อ่านข้อมูลไฟล์ไม่ได้ ไฟล์อาจเสียหาย") from exc


def _fraction(text: str | None) -> float:
    """'30000/1001' -> 29.97; anything unusable -> 0."""
    try:
        num, _, den = str(text or "").partition("/")
        value = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return 0.0
    return value if value > 0 else 0.0


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def probe_media(path: str) -> dict[str, Any]:
    """Everything the converter page shows about a file, from one ffprobe call.

    Returns {"format_name", "format_long", "duration", "size", "bit_rate",
             "video": {...} or None, "audio": {...} or None, "audio_streams", "subtitle_streams"}.
    """
    cmd = [find_tool("ffprobe"), "-v", "error", "-show_format", "-show_streams", "-of", "json", path]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False, **_spawn_kwargs(),
    )
    try:
        data = json.loads(proc.stdout)
        fmt = data["format"]
    except (KeyError, json.JSONDecodeError) as exc:
        raise ToolFailedError("อ่านข้อมูลไฟล์ไม่ได้ ไฟล์อาจเสียหาย") from exc
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)  # skip cover art
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    info: dict[str, Any] = {
        "format_name": fmt.get("format_name", ""),
        "format_long": fmt.get("format_long_name", ""),
        "duration": _fraction(fmt.get("duration")),
        "size": _int(fmt.get("size")),
        "bit_rate": _int(fmt.get("bit_rate")),
        "video": None,
        "audio": None,
        "audio_streams": len(audios),
        "subtitle_streams": sum(1 for s in streams if s.get("codec_type") == "subtitle"),
    }
    if video is not None:
        info["video"] = {
            "codec": video.get("codec_name", ""),
            "profile": video.get("profile", ""),
            "width": _int(video.get("width")),
            "height": _int(video.get("height")),
            "fps": round(_fraction(video.get("avg_frame_rate")) or _fraction(video.get("r_frame_rate")), 3),
            "pix_fmt": video.get("pix_fmt", ""),
            "bit_rate": _int(video.get("bit_rate")),
        }
    if audios:
        audio = audios[0]
        info["audio"] = {
            "codec": audio.get("codec_name", ""),
            "sample_rate": _int(audio.get("sample_rate")),
            "channels": _int(audio.get("channels")),
            "bit_rate": _int(audio.get("bit_rate")),
        }
    return info


_encoders_cache: set[str] | None = None


def available_encoders() -> set[str]:
    """Encoder names this FFmpeg build has (cached). Empty when FFmpeg is missing."""
    global _encoders_cache
    if _encoders_cache is None:
        try:
            proc = subprocess.run(
                [find_tool("ffmpeg"), "-hide_banner", "-encoders"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", check=False, **_spawn_kwargs(),
            )
            names = set()
            for line in proc.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 2 and len(parts[0]) == 6 and parts[0][0] in "VAS":
                    names.add(parts[1])
            _encoders_cache = names
        except (ToolNotFoundError, OSError):
            _encoders_cache = set()
    return _encoders_cache


def probe_codecs(path: str) -> tuple[str, str]:
    """(first video codec, first audio codec) names as ffprobe reports them; "" when absent."""
    cmd = [
        find_tool("ffprobe"), "-v", "error",
        "-show_entries", "stream=codec_type,codec_name", "-of", "json", path,
    ]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False, **_spawn_kwargs(),
    )
    try:
        streams = json.loads(proc.stdout).get("streams", [])
    except json.JSONDecodeError as exc:
        raise ToolFailedError("อ่านข้อมูลไฟล์ไม่ได้ ไฟล์อาจเสียหาย") from exc
    first = {}
    for stream in streams:
        first.setdefault(stream.get("codec_type"), stream.get("codec_name") or "")
    return first.get("video", ""), first.get("audio", "")


def probe_duration(path: str) -> float:
    try:
        duration = float(probe_format(path).get("duration") or 0)
    except ValueError as exc:
        raise ToolFailedError("ไม่พบความยาวของไฟล์") from exc
    if duration <= 0:
        raise ToolFailedError("ไม่พบความยาวของไฟล์")
    return duration


def run_ffmpeg(
    args: Sequence[str],
    *,
    duration: float,
    on_progress: Callable[[float], None] | None = None,
    is_cancelled: Callable[[], bool] = lambda: False,
) -> None:
    """Run ffmpeg with the given input/output options. Progress is reported as 0.0-1.0.

    Output muxer should be given explicitly with -f, because temp names end in .part.
    Raises TaskCancelledError (after killing ffmpeg) when is_cancelled() returns True,
    and ToolFailedError (message = last ffmpeg error line, .stderr = all of it) on a non-zero exit.
    """
    cmd = [
        find_tool("ffmpeg"), "-hide_banner", "-nostdin", "-y",
        "-loglevel", "error", "-nostats", "-progress", "pipe:1",
        *args,
    ]
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", **_spawn_kwargs(),
        )
    except OSError as exc:
        raise ToolNotFoundError(f"เรียกใช้ FFmpeg ไม่ได้: {exc}") from exc

    # Drain stderr on a thread: a corrupt input can print enough errors to fill the pipe
    # and block ffmpeg while we are still reading progress from stdout.
    stderr_parts: list[str] = []
    drain = threading.Thread(
        target=lambda: stderr_parts.append(proc.stderr.read()), daemon=True
    )
    drain.start()
    seen_us = False
    try:
        for line in proc.stdout:
            if is_cancelled():
                raise TaskCancelledError("ยกเลิกงานแล้ว")
            key, _, value = line.strip().partition("=")
            seen_us = seen_us or key == "out_time_us"
            # out_time_ms is misnamed (also microseconds); only older builds lack out_time_us
            wanted = key == "out_time_us" or (key == "out_time_ms" and not seen_us)
            if wanted and duration > 0 and on_progress is not None:
                try:
                    seconds = int(value) / 1_000_000
                except ValueError:
                    continue
                on_progress(max(0.0, min(seconds / duration, 1.0)))
        proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        drain.join(timeout=5)
        for pipe in (proc.stdout, proc.stderr):
            if pipe is not None:
                pipe.close()

    if proc.returncode != 0:
        stderr = "".join(stderr_parts).strip()
        lines = stderr.splitlines()
        last = lines[-1] if lines else "ไม่ทราบสาเหตุ"
        raise ToolFailedError(f"FFmpeg ทำงานไม่สำเร็จ: {last}", stderr=stderr)
