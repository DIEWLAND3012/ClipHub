"""Optional GPU pack for AI subtitles: NVIDIA's cuBLAS and cuDNN, downloaded on request (like a DLC).

Without it subtitles run on the CPU. With it, and an NVIDIA card, faster-whisper runs on CUDA (about 2.5x faster).
The libraries come from NVIDIA's official wheels on PyPI (checked against PyPI's SHA-256); only their DLLs are
kept, in <data dir>/gpu-pack. Windows only: macOS has no CUDA.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from core import net
from core.ffmpeg_setup import _clean_leftovers, data_dir

Progress = Callable[[float], None]

# Versions tested with ctranslate2 4.x (CUDA 12). Wheels are plain zip files.
PACKAGES = (("nvidia-cublas-cu12", "12.9.2.10"), ("nvidia-cudnn-cu12", "9.28.1.4"))
_WANTED_PREFIXES = ("cublas64_", "cublaslt64_", "cudnn")
MARKER = "cublas64_12.dll"
_TIMEOUT = 60


def folder() -> Path:
    return data_dir() / "gpu-pack"


def _pending_file() -> Path:
    return data_dir() / "gpu-pack.remove"


def platform_supported() -> bool:
    return sys.platform == "win32"


def has_nvidia() -> bool:
    """An NVIDIA card with a working driver (CUDA sees a device)."""
    if not platform_supported():
        return False
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def installed() -> bool:
    return (folder() / MARKER).is_file() and not _pending_file().exists()


def size_on_disk() -> int:
    return sum(p.stat().st_size for p in folder().glob("*") if p.is_file()) if folder().is_dir() else 0


def add_to_dll_path() -> None:
    """Let CUDA find the pack's DLLs (call before faster-whisper loads them)."""
    if not installed():
        return
    path = str(folder())
    try:
        os.add_dll_directory(path)
    except (OSError, AttributeError):
        pass
    if path not in os.environ.get("PATH", ""):
        os.environ["PATH"] = path + os.pathsep + os.environ.get("PATH", "")


def _get(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": "ClipHUB"})
    return urllib.request.urlopen(request, timeout=_TIMEOUT)  # noqa: S310 (fixed https URLs)


def wheel_info(name: str, version: str) -> tuple[str, int, str]:
    """(url, size, sha256) of the Windows wheel, from PyPI."""
    with _get(f"https://pypi.org/pypi/{name}/{version}/json") as response:
        files = json.loads(response.read())["urls"]
    for item in files:
        if item["filename"].endswith("win_amd64.whl"):
            return item["url"], int(item["size"]), item["digests"]["sha256"]
    raise RuntimeError(f"{name} {version} has no Windows build")


def total_download_size() -> int:
    return sum(wheel_info(n, v)[1] for n, v in PACKAGES)


def _wanted(member: str) -> bool:
    name = Path(member).name.lower()
    return name.endswith(".dll") and "/bin/" in member.replace("\\", "/") and name.startswith(_WANTED_PREFIXES)


def install(progress: Progress | None = None, is_cancelled: Callable[[], bool] = lambda: False) -> None:
    """Download both wheels, check them, and keep their DLLs. About 1.25 GB to download, 1.9 GB on disk."""
    if not platform_supported():
        raise RuntimeError("The GPU pack is for Windows with an NVIDIA graphics card.")
    data_dir().mkdir(parents=True, exist_ok=True)
    _pending_file().unlink(missing_ok=True)
    _clean_leftovers()  # a download that was cut off last time
    infos = [wheel_info(n, v) for n, v in PACKAGES]
    total = sum(size for _url, size, _sha in infos) or 1
    done = 0  # bytes of the wheels already downloaded
    staging = data_dir() / "gpu-pack.new"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        with tempfile.TemporaryDirectory(dir=data_dir()) as tmp:
            for index, (url, size, sha) in enumerate(infos):
                wheel = Path(tmp) / f"pack-{index}.whl"
                start = done
                report = (lambda v, s=start, n=size: progress(0.92 * (s + v * n) / total)) if progress else None
                if net.download(url, wheel, report, is_cancelled, size) != sha:
                    raise RuntimeError("The download is damaged (checksum mismatch). Try again.")
                done += size
                with zipfile.ZipFile(wheel) as bundle:
                    for member in bundle.namelist():
                        if is_cancelled():
                            raise InterruptedError("cancelled")
                        if _wanted(member):
                            with bundle.open(member) as src, open(staging / Path(member).name, "wb") as dst:
                                shutil.copyfileobj(src, dst)
                wheel.unlink()
            if progress:
                progress(0.98)
        if not (staging / MARKER).is_file():
            raise RuntimeError("cuBLAS is missing from the download")
        shutil.rmtree(folder(), ignore_errors=True)
        os.replace(staging, folder())
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    if progress:
        progress(1.0)


def uninstall() -> bool:
    """Delete the pack. Returns False when some DLLs are still in use: they go at the next start."""
    shutil.rmtree(folder(), ignore_errors=True)
    if folder().exists():
        data_dir().mkdir(parents=True, exist_ok=True)
        _pending_file().write_text("1", encoding="utf-8")
        return False
    _pending_file().unlink(missing_ok=True)
    return True


def finish_pending_removal() -> None:
    """At start-up, before anything loads the DLLs: finish an uninstall that was blocked last time."""
    if _pending_file().exists():
        shutil.rmtree(folder(), ignore_errors=True)
        if not folder().exists():
            _pending_file().unlink(missing_ok=True)
