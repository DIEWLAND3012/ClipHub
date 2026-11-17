"""Make sure FFmpeg is on this computer: find it, or download it on first start, and keep it updated.

Start-up cost is kept near zero: the first successful check is saved to ffmpeg.json in the app's
data folder, and later starts only confirm those two files still exist (no subprocess, no network).

A copy ClipHUB downloads lives in its own data folder (no admin rights needed) and is the only copy
it ever updates. An FFmpeg the user installed (PATH, Homebrew) is used as is and never touched.
Updates are checked in the background at most every few days, downloaded to a staging folder, and
swapped in on the next start, so a running conversion never loses its ffmpeg.exe.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from core import ffmpeg

UPDATE_EVERY = 3 * 24 * 3600  # seconds between update checks
_TIMEOUT = 30
_USER_AGENT = "ClipHUB"

Progress = Callable[[float], None]  # 0.0 - 1.0 of the download and install


@dataclass(frozen=True)
class Release:
    version: str
    archives: tuple[tuple[str, str], ...]  # (zip url, sha256 url) pairs


def data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "ClipHUB"


def _state_file() -> Path:
    return data_dir() / "ffmpeg.json"


def _staging_dir() -> Path:
    return data_dir() / "ffmpeg-update"


def _exe(name: str) -> str:
    return f"{name}.exe" if sys.platform == "win32" else name


# --- remembered result --------------------------------------------------------------------------
def load_state() -> dict:
    try:
        return json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    try:
        _state_file().parent.mkdir(parents=True, exist_ok=True)
        _state_file().write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass


def _cached_ok(state: dict) -> bool:
    paths = [state.get("ffmpeg"), state.get("ffprobe")]
    return all(p and Path(p).is_file() for p in paths)


# --- versions -------------------------------------------------------------------------------------
def version_of(ffmpeg_path: str) -> str:
    """'9.0.2' from `ffmpeg -version` ('' when it cannot be read)."""
    try:
        out = subprocess.run([ffmpeg_path, "-version"], capture_output=True, text=True, timeout=20,
                             check=False, **ffmpeg._spawn_kwargs()).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    match = re.search(r"ffmpeg version (?:n)?([0-9][0-9.]*)", out)
    return match.group(1).rstrip(".") if match else ""


def version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", text)[:3])


def is_newer(latest: str, installed: str) -> bool:
    return bool(latest) and version_tuple(latest) > version_tuple(installed or "0")


# --- where to download from -------------------------------------------------------------------------
def _get(url: str) -> urllib.request.addinfourl:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    return urllib.request.urlopen(request, timeout=_TIMEOUT)  # noqa: S310 (fixed https URLs)


def latest_release() -> Release:
    """The newest release build for this platform (Windows: gyan.dev, macOS: martin-riedl.de)."""
    if sys.platform == "win32":
        base = "https://www.gyan.dev/ffmpeg/builds/"
        with _get(base + "release-version") as response:
            version = response.read().decode().strip()
        zip_url = f"{base}packages/ffmpeg-{version}-essentials_build.zip"
        return Release(version, ((zip_url, zip_url + ".sha256"),))
    if sys.platform == "darwin":
        arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "amd64"
        archives = []
        version = ""
        for tool in ("ffmpeg", "ffprobe"):
            with _get(f"https://ffmpeg.martin-riedl.de/redirect/latest/macos/{arch}/release/{tool}.zip") as r:
                url = r.geturl()  # .../download/macos/arm64/<build>_9.0.2/ffmpeg.zip
            version = version or (re.search(r"_([0-9][0-9.]*)/", url) or [None, ""])[1]
            archives.append((url, url + ".sha256"))
        return Release(version, tuple(archives))
    raise RuntimeError("Automatic FFmpeg install is available on Windows and macOS only")


# --- download and install ---------------------------------------------------------------------------
def _download(url: str, target: Path, progress: Progress | None, start: float, span: float) -> None:
    with _get(url) as response, open(target, "wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        while chunk := response.read(256 * 1024):
            out.write(chunk)
            done += len(chunk)
            if progress and total:
                progress(start + span * done / total)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _install_to(folder: Path, release: Release, progress: Progress | None) -> None:
    """Download the release's archives and put ffmpeg + ffprobe in folder/bin."""
    bin_dir = folder / "bin"
    shutil.rmtree(folder, ignore_errors=True)
    bin_dir.mkdir(parents=True)
    wanted = {_exe("ffmpeg"), _exe("ffprobe")}
    span = 0.95 / len(release.archives)
    with tempfile.TemporaryDirectory(dir=data_dir()) as tmp:
        for index, (zip_url, sha_url) in enumerate(release.archives):
            archive = Path(tmp) / f"ffmpeg-{index}.zip"
            _download(zip_url, archive, progress, index * span, span)
            with _get(sha_url) as response:
                expected = response.read().decode().split()[0].lower()
            if _sha256(archive) != expected:
                raise RuntimeError("FFmpeg download is damaged (checksum mismatch)")
            with zipfile.ZipFile(archive) as bundle:
                for member in bundle.infolist():
                    name = Path(member.filename).name
                    if name in wanted and not member.is_dir():
                        with bundle.open(member) as src, open(bin_dir / name, "wb") as dst:
                            shutil.copyfileobj(src, dst)
    for name in wanted:
        tool = bin_dir / name
        if not tool.is_file():
            raise RuntimeError(f"{name} is missing from the FFmpeg download")
        tool.chmod(0o755)
    if progress:
        progress(1.0)


def _remember(source: str, ffmpeg_path: str, ffprobe_path: str, version: str = "") -> dict:
    state = load_state()
    state.update(source=source, ffmpeg=ffmpeg_path, ffprobe=ffprobe_path,
                 version=version or version_of(ffmpeg_path))
    state.setdefault("checked", time.time())
    save_state(state)
    return state


def apply_staged_update() -> None:
    """Swap in an update downloaded during the previous run (a quick folder rename)."""
    staged = _staging_dir()
    if not (staged / "bin" / _exe("ffmpeg")).is_file():
        return
    current = ffmpeg.managed_dir()
    old = current.with_name("ffmpeg-old")
    try:
        shutil.rmtree(old, ignore_errors=True)
        if current.exists():
            current.rename(old)
        staged.rename(current)
        shutil.rmtree(old, ignore_errors=True)
        bin_dir = current / "bin"
        _remember("managed", str(bin_dir / _exe("ffmpeg")), str(bin_dir / _exe("ffprobe")))
    except OSError:
        pass  # files busy: try again next start


def needs_install() -> bool:
    """True when FFmpeg has to be downloaded before the app can work (fast: no subprocess if cached)."""
    apply_staged_update()
    if _cached_ok(load_state()):
        return False
    try:
        found = ffmpeg.find_tool("ffmpeg"), ffmpeg.find_tool("ffprobe")
    except Exception:
        return True
    managed = Path(found[0]).is_relative_to(ffmpeg.managed_dir())
    _remember("managed" if managed else "system", *found)
    return False


def install(progress: Progress | None = None) -> dict:
    """Download and install FFmpeg into the app's data folder. Raises on failure (e.g. offline)."""
    data_dir().mkdir(parents=True, exist_ok=True)
    release = latest_release()
    target = ffmpeg.managed_dir()
    _install_to(target, release, progress)
    bin_dir = target / "bin"
    return _remember("managed", str(bin_dir / _exe("ffmpeg")), str(bin_dir / _exe("ffprobe")),
                     release.version)


def check_for_update(force: bool = False) -> str:
    """Download a newer FFmpeg for the next start, if ClipHUB installed its own copy.

    Returns the staged version, or '' when there is nothing to do. Meant for a background thread.
    """
    state = load_state()
    if state.get("source") != "managed" or not _cached_ok(state):
        return ""
    if not force and time.time() - float(state.get("checked") or 0) < UPDATE_EVERY:
        return ""
    try:
        release = latest_release()
        state["checked"] = time.time()
        save_state(state)
        if not is_newer(release.version, state.get("version", "")):
            return ""
        _install_to(_staging_dir(), release, None)
        return release.version
    except Exception:
        shutil.rmtree(_staging_dir(), ignore_errors=True)
        return ""
