"""Hardware acceleration: find which video encoders really work on this machine and pick one per job.

Engines are verified with a tiny test encode, because FFmpeg lists encoders (NVENC, Quick Sync,
AMF, VideoToolbox) whether or not the hardware or a new-enough driver is present. Several engines
can run at the same time (e.g. NVIDIA GPU + Intel iGPU + CPU), so the queue gives each video job the
least busy engine that supports its codec.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from core.ffmpeg import _spawn_kwargs, available_encoders, find_tool

CODEC_TO_FFMPEG = {"h264": "h264", "h265": "hevc", "av1": "av1"}  # ClipHUB key -> encoder name prefix
CPU = "cpu"


@dataclass(frozen=True)
class Engine:
    key: str                 # "cpu", "nvenc:0", "qsv", "amf", "videotoolbox"
    family: str              # "cpu" | "nvenc" | "qsv" | "amf" | "videotoolbox"
    label: str               # shown in the UI, e.g. "NVIDIA GeForce GTX 1660 Ti"
    codecs: frozenset[str]   # ClipHUB codec keys it can encode: h264, h265, av1
    gpu_index: int | None = None
    slots: int = 1           # jobs it runs at once: GPUs encode several sessions in parallel

    @property
    def is_gpu(self) -> bool:
        return self.family != CPU

    @property
    def short(self) -> str:
        """Tag for queue cards: 'CPU', 'NVENC', 'Quick Sync'..."""
        return {"cpu": "CPU", "nvenc": "NVIDIA", "qsv": "Intel GPU", "amf": "AMD GPU",
                "videotoolbox": "Apple GPU"}[self.family]


def cpu_engine() -> Engine:
    threads = os.cpu_count() or 1
    return Engine(CPU, CPU, f"CPU · {threads} เธรด", frozenset({"h264", "h265", "av1", "vp9", "prores"}))


@dataclass
class HardwareReport:
    engines: list[Engine] = field(default_factory=lambda: [cpu_engine()])
    devices: list[str] = field(default_factory=list)   # display adapters the OS reports
    problems: list[str] = field(default_factory=list)  # e.g. "update the NVIDIA driver"
    whisper_gpu: bool = False                          # subtitles can run on CUDA
    ready: bool = False                                # detection finished

    def gpu_engines(self) -> list[Engine]:
        return [e for e in self.engines if e.is_gpu]


# --- encoder arguments ------------------------------------------------------------------

# Constant-quality targets matched to libx264 CRF 17 / libx265 CRF 19 (SSIM within 0.001 in our tests).
_NVENC_CQ = {"h264": 19, "h265": 21, "av1": 26}
_QSV_GQ = {"h264": 20, "h265": 20, "av1": 24}
_AMF_QP = {"h264": 20, "h265": 22, "av1": 26}
_VT_Q = {"h264": 65, "h265": 60}


def encoder_args(engine: Engine, codec: str, bitrate_kbps: int | None = None) -> list[str]:
    """Video encoder options for a GPU engine. Constant quality, or a target bitrate when given."""
    name = f"{CODEC_TO_FFMPEG[codec]}_{engine.family}"
    if engine.family == "nvenc":
        args = ["-c:v", name, "-gpu", str(engine.gpu_index or 0), "-preset", "p5", "-tune", "hq"]
        if bitrate_kbps:
            args += ["-rc", "vbr", "-b:v", f"{bitrate_kbps}k", "-maxrate", f"{bitrate_kbps}k",
                     "-bufsize", f"{bitrate_kbps * 2}k"]
        else:
            args += ["-rc", "vbr", "-cq", str(_NVENC_CQ[codec]), "-b:v", "0"]
        return [*args, "-pix_fmt", "yuv420p"]
    if engine.family == "qsv":
        args = ["-c:v", name, "-preset", "medium"]
        if bitrate_kbps:
            args += ["-b:v", f"{bitrate_kbps}k", "-maxrate", f"{bitrate_kbps}k", "-bufsize", f"{bitrate_kbps * 2}k"]
        else:
            args += ["-global_quality", str(_QSV_GQ[codec])]
        return [*args, "-pix_fmt", "nv12"]
    if engine.family == "amf":
        args = ["-c:v", name, "-quality", "quality"]
        if bitrate_kbps:
            args += ["-rc", "vbr_peak", "-b:v", f"{bitrate_kbps}k", "-maxrate", f"{bitrate_kbps}k"]
        else:
            qp = _AMF_QP[codec]
            args += ["-rc", "cqp", "-qp_i", str(qp), "-qp_p", str(qp + 2), "-qp_b", str(qp + 4)]
        return [*args, "-pix_fmt", "nv12"]
    if engine.family == "videotoolbox":
        args = ["-c:v", name, "-allow_sw", "1"]
        args += ["-b:v", f"{bitrate_kbps}k"] if bitrate_kbps else ["-q:v", str(_VT_Q.get(codec, 60))]
        return [*args, "-pix_fmt", "yuv420p"]
    raise ValueError(f"not a GPU engine: {engine.key}")


# --- choosing an engine for a job ---------------------------------------------------------

_PREFERENCE = {"nvenc": 0, "videotoolbox": 1, "amf": 2, "qsv": 3, CPU: 9}  # fastest first


def pick_engine(codec: str | None, engines: Iterable[Engine], busy: Mapping[str, int],
                use_gpu: bool = True) -> Engine | None:
    """The engine a job should use, or None to wait.

    A codec a GPU can encode always goes to a GPU (the least loaded one with a free slot), even if
    that means waiting: measured on an Intel iGPU, 3 parallel GPU jobs took 5.5 s, the same jobs
    one by one 7.7 s, and adding a CPU encode alongside made everything slower (it starves the
    decoding the GPU jobs need). The CPU takes the codecs no GPU supports, or everything when the
    GPU is switched off. Jobs that do not encode video (codec None) never need an engine.
    """
    if codec is None:
        return None
    engines = list(engines)
    gpus = [e for e in engines if e.is_gpu and codec in e.codecs] if use_gpu else []
    pool = gpus or [e for e in engines if not e.is_gpu and codec in e.codecs]
    free = [e for e in pool if busy.get(e.key, 0) < e.slots]
    if not free:
        return None
    return min(free, key=lambda e: (busy.get(e.key, 0) / e.slots, _PREFERENCE.get(e.family, 5), e.gpu_index or 0))


def parallel_limit(engines: Iterable[Engine], setting: int = 0) -> int:
    """How many jobs may run at once: the user's number, or every engine slot (2 to 6)."""
    if setting > 0:
        return setting
    return max(2, min(6, sum(e.slots for e in engines)))


# --- detection -----------------------------------------------------------------------------

def _run(cmd: list[str], timeout: float = 15) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, check=False, **_spawn_kwargs())


def _display_adapters() -> list[str]:
    try:
        if sys.platform == "win32":
            proc = _run(["powershell", "-NoProfile", "-Command",
                         "(Get-CimInstance Win32_VideoController).Name"], timeout=20)
            return [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        if sys.platform == "darwin":
            proc = _run(["system_profiler", "SPDisplaysDataType"], timeout=20)
            return [m.strip() for m in re.findall(r"Chipset Model:\s*(.+)", proc.stdout)]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return []


def _nvidia_gpus() -> list[str]:
    try:
        proc = _run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], timeout=10)
        return [line.strip() for line in proc.stdout.splitlines() if line.strip()] if proc.returncode == 0 else []
    except (OSError, subprocess.TimeoutExpired):
        return []


def _try_encoder(ffmpeg: str, encoder: str, extra: list[str]) -> tuple[bool, str]:
    """Encode 3 tiny frames; returns (works, ffmpeg error text)."""
    cmd = [ffmpeg, "-hide_banner", "-v", "error", "-f", "lavfi", "-i", "color=black:s=256x256:r=10:d=0.3",
           "-frames:v", "3", "-c:v", encoder, *extra, "-f", "null", "-"]
    try:
        proc = _run(cmd, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    return proc.returncode == 0, proc.stderr


def _whisper_gpu_ready() -> bool:
    """True when faster-whisper can run on CUDA: a GPU is visible and the cuBLAS library loads."""
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() <= 0:
            return False
    except Exception:
        return False
    add_cuda_dll_dirs()
    if sys.platform == "win32":
        import ctypes
        try:
            ctypes.WinDLL("cublas64_12.dll")
            return True
        except OSError:
            return False
    return True


def add_cuda_dll_dirs() -> None:
    """Let Windows find cuBLAS / cuDNN installed as pip packages (nvidia-cublas-cu12, nvidia-cudnn-cu12)."""
    if sys.platform != "win32":
        return
    try:
        import nvidia  # namespace package from the nvidia-* wheels
    except ImportError:
        return
    for root in getattr(nvidia, "__path__", []):
        for sub in ("cublas", "cudnn", "cuda_runtime"):
            folder = os.path.join(root, sub, "bin")
            if os.path.isdir(folder):
                try:
                    os.add_dll_directory(folder)
                except OSError:
                    pass
                os.environ["PATH"] = folder + os.pathsep + os.environ.get("PATH", "")


def detect() -> HardwareReport:
    """Probe every hardware encoder family. Takes a few seconds: call it off the GUI thread."""
    report = HardwareReport(engines=[cpu_engine()])
    report.devices = _display_adapters()
    try:
        ffmpeg = find_tool("ffmpeg")
    except Exception:
        report.ready = True
        return report
    listed = available_encoders()

    def family_codecs(family: str, extra: list[str]) -> tuple[set[str], str]:
        works, errors = set(), ""
        for key, prefix in CODEC_TO_FFMPEG.items():
            encoder = f"{prefix}_{family}"
            if encoder not in listed:
                continue
            ok, err = _try_encoder(ffmpeg, encoder, extra)
            if ok:
                works.add(key)
            else:
                errors += err
        return works, errors

    nvidia = _nvidia_gpus()
    for index, name in enumerate(nvidia or ([""] if any("NVIDIA" in d for d in report.devices) else [])):
        codecs, errors = family_codecs("nvenc", ["-gpu", str(index)])
        if codecs:
            report.engines.append(Engine(f"nvenc:{index}", "nvenc", name or "NVIDIA GPU", frozenset(codecs), index, 3))
        else:
            driver = re.search(r"minimum required Nvidia driver for nvenc is ([\d.]+)", errors)
            if driver:
                report.problems.append(
                    f"{name or 'การ์ดจอ NVIDIA'}: ต้องอัปเดตไดรเวอร์ NVIDIA เป็นเวอร์ชัน {driver.group(1)} ขึ้นไป "
                    "จึงจะใช้เร่งความเร็วได้")
            elif name:
                report.problems.append(f"{name}: ใช้ NVENC ไม่ได้")
    if sys.platform == "darwin":
        codecs, _ = family_codecs("videotoolbox", ["-allow_sw", "0"])
        if codecs:
            report.engines.append(Engine("videotoolbox", "videotoolbox", "Apple Media Engine", frozenset(codecs), slots=3))
    else:
        for family, vendor_words, fallback in (("qsv", ("Intel",), "Intel GPU"), ("amf", ("AMD", "Radeon"), "AMD GPU")):
            codecs, _ = family_codecs(family, [])
            if codecs:
                name = next((d for d in report.devices if any(w in d for w in vendor_words)), fallback)
                report.engines.append(Engine(family, family, name, frozenset(codecs), slots=3))
    report.whisper_gpu = _whisper_gpu_ready()
    report.ready = True
    return report


# --- process-wide state ---------------------------------------------------------------------

_report = HardwareReport()
_lock = threading.Lock()


def report() -> HardwareReport:
    return _report


def refresh() -> HardwareReport:
    global _report
    fresh = detect()
    with _lock:
        _report = fresh
    return fresh


def engine_by_key(key: str) -> Engine | None:
    return next((e for e in _report.engines if e.key == key), None)
