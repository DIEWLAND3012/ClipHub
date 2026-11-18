"""ClipHUB's own updates: list the releases on GitHub, download one, and swap it in after the app quits.

Updating is never forced. The app checks quietly once per start; the settings show a red dot when a newer
version exists, and the user picks when to update, or goes back to an older version, from there.

Windows: the single ClipHUB .exe is replaced by a small script that waits for the app to close.
macOS: the .dmg is mounted and its ClipHUB.app copied over the running bundle, also after the app closes.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from core import net, scripts
from core.ffmpeg_setup import data_dir, is_newer, version_tuple
from core.version import __version__

REPO = "DIEWLAND3012/ClipHub"
API = f"https://api.github.com/repos/{REPO}/releases?per_page=30"
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"
_TIMEOUT = 30
Progress = Callable[[float], None]


@dataclass
class AppRelease:
    version: str      # "2.2.0"
    url: str          # the installer for this platform
    size: int = 0     # bytes
    sha256: str = ""  # from GitHub's asset digest, when it has one
    notes: str = ""

    @property
    def label(self) -> str:
        return "v" + self.version


def _get(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": "ClipHUB", "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(request, timeout=_TIMEOUT)  # noqa: S310 (fixed https URL)


def _platform_asset(assets: list[dict]) -> dict | None:
    """The installer for this computer: ClipHUB-Windows*.exe or ClipHUB-macOS*.dmg."""
    prefix, ext = ("ClipHUB-Windows", ".exe") if sys.platform == "win32" else ("ClipHUB-macOS", ".dmg")
    for asset in assets:
        name = asset.get("name", "")
        if name.startswith(prefix) and name.endswith(ext):
            return asset
    return None


def parse_releases(data: list[dict]) -> list[AppRelease]:
    """Published (not draft or pre-release) versions that have an installer for this platform, newest first."""
    found: dict[str, AppRelease] = {}
    for item in data:
        if item.get("draft") or item.get("prerelease"):
            continue
        version = str(item.get("tag_name", "")).lstrip("vV")
        asset = _platform_asset(item.get("assets") or [])
        if not version or not version_tuple(version) or asset is None:
            continue
        if not str(asset.get("browser_download_url", "")).startswith(DOWNLOAD_PREFIX):
            continue  # only installers from this repository's own releases
        digest = str(asset.get("digest") or "")
        found[version] = AppRelease(version, asset["browser_download_url"], int(asset.get("size") or 0),
                                    digest.split(":", 1)[1] if digest.startswith("sha256:") else "",
                                    str(item.get("body") or ""))
    return sorted(found.values(), key=lambda r: version_tuple(r.version), reverse=True)


def list_releases() -> list[AppRelease]:
    with _get(API) as response:
        return parse_releases(json.loads(response.read()))


def newest(releases: list[AppRelease]) -> AppRelease | None:
    """The newest release when it is newer than this app, else None."""
    if releases and is_newer(releases[0].version, __version__):
        return releases[0]
    return None


def can_self_update() -> bool:
    """Only an installed build can replace itself (not `python main.py`)."""
    return bool(getattr(sys, "frozen", False)) and sys.platform in ("win32", "darwin")


def _update_dir() -> Path:
    folder = data_dir() / "app-update"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def download(release: AppRelease, progress: Progress | None = None,
             is_cancelled: Callable[[], bool] = lambda: False) -> Path:
    """Fetch the installer into the app's data folder and check it. Returns its path."""
    target = _update_dir() / release.url.rsplit("/", 1)[-1]
    partial = target.with_suffix(target.suffix + ".part")
    try:
        sha = net.download(release.url, partial, progress, is_cancelled, release.size)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    if release.sha256 and sha != release.sha256.lower():
        partial.unlink(missing_ok=True)
        raise RuntimeError("The download is damaged (checksum mismatch). Try again.")
    if release.size and partial.stat().st_size != release.size:
        partial.unlink(missing_ok=True)
        raise RuntimeError("The download is incomplete. Try again.")
    os.replace(partial, target)
    return target


def windows_swap_script(new_exe: str, app_exe: str) -> str:
    """Wait until the running .exe can be replaced, replace it, start it, then delete this script."""
    new, app = scripts.cmd_quote(new_exe), scripts.cmd_quote(app_exe)
    return "\r\n".join([
        "@echo off",
        "set tries=0",
        ":wait",
        "ping -n 2 127.0.0.1 >nul",  # 1 s wait (timeout fails without a console)
        f"move /y {new} {app} >nul 2>&1",
        "if not errorlevel 1 goto run",
        "set /a tries+=1",
        "if %tries% lss 120 goto wait",
        "goto end",
        ":run",
        f'start "" {app}',
        ":end",
        '(goto) 2>nul & del "%~f0"',
        "",
    ])


def mac_swap_script(dmg: str, app_bundle: str, pid: int) -> str:
    """Wait for the app to quit, copy ClipHUB.app from the disk image over it, then open it."""
    q = scripts.sh_quote
    app, old, image = q(app_bundle), q(app_bundle + ".old"), q(dmg)
    return "\n".join([
        "#!/bin/sh",
        f"while kill -0 {int(pid)} 2>/dev/null; do sleep 0.5; done",
        'MNT=$(mktemp -d)',
        f'hdiutil attach -nobrowse -quiet -mountpoint "$MNT" {image} || exit 1',
        f"rm -rf {old}; mv {app} {old}",
        f'if ditto "$MNT/ClipHUB.app" {app}; then rm -rf {old}; else mv {old} {app}; fi',
        'hdiutil detach -quiet "$MNT"',
        f"xattr -dr com.apple.quarantine {app} 2>/dev/null",
        f"rm -f {image}",
        f"open {app}",
        'rm -f "$0"',
        "",
    ])


def app_bundle() -> Path:
    """macOS: …/ClipHUB.app (sys.executable is …/ClipHUB.app/Contents/MacOS/ClipHUB)."""
    return Path(sys.executable).resolve().parents[2]


def install_after_exit(installer: Path) -> None:
    """Start the swap script; the caller must quit the app right after."""
    if not can_self_update():
        raise RuntimeError("Updating works in the installed app only.")
    if sys.platform == "win32":
        script = scripts.write(windows_swap_script(str(installer), sys.executable), ".cmd")
    else:
        script = scripts.write(mac_swap_script(str(installer), str(app_bundle()), os.getpid()), ".sh")
    scripts.run_detached(script)


def clean_downloads() -> None:
    """Old installers left from earlier updates."""
    shutil.rmtree(data_dir() / "app-update", ignore_errors=True)
