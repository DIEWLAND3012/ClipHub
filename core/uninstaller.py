"""Remove ClipHUB from this computer: the app itself and everything it put there.

That is the data folder (FFmpeg, the GPU pack, downloaded updates), the AI subtitle models in the Hugging Face
cache, FastCut's preview files, and the saved settings. Files the app still has open (the running .exe, loaded
DLLs) are deleted by a small script that runs once the app has quit.

Running from source (`python main.py`) removes the data only, never the source folder.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QSettings

from core.ffmpeg_setup import data_dir


def model_dirs() -> list[Path]:
    """faster-whisper models ClipHUB downloaded (only these, the cache is shared with other apps)."""
    root = Path(os.environ.get("HF_HUB_CACHE") or os.environ.get("HF_HOME", "") or "")
    candidates = [root, root / "hub"] if str(root) else []
    candidates.append(Path.home() / ".cache" / "huggingface" / "hub")
    found: list[Path] = []
    for folder in candidates:
        if folder.is_dir():
            found += [p for p in folder.glob("models--*faster-whisper*") if p.is_dir()]
    return found


def targets() -> list[Path]:
    """Every folder and file to remove, the app last."""
    paths = [data_dir(), Path(tempfile.gettempdir()) / "cliphub-preview", *model_dirs()]
    if sys.platform == "darwin":
        paths.append(Path.home() / "Library" / "Preferences" / "com.cliphub.ClipHUB.plist")
    if getattr(sys, "frozen", False):
        if sys.platform == "darwin":
            paths.append(Path(sys.executable).resolve().parents[2])  # …/ClipHUB.app
        else:
            paths.append(Path(sys.executable).resolve())
    return paths


def windows_script(paths: list[Path], pid: int) -> str:
    lines = ["@echo off", ":wait", "timeout /t 1 /nobreak >nul",  # until the app has quit
             f'tasklist /fi "PID eq {pid}" /nh | find "{pid}" >nul && goto wait',
             "set tries=0", ":again", "timeout /t 1 /nobreak >nul", "set left=0"]
    for path in paths:
        lines += [f'if exist "{path}\\*" rmdir /s /q "{path}" >nul 2>&1',
                  f'if exist "{path}" del /f /q "{path}" >nul 2>&1',
                  f'if exist "{path}" set left=1']
    lines += [r"reg delete HKCU\Software\ClipHUB /f >nul 2>&1",  # settings (after the app wrote its last)
              "set /a tries+=1", "if %left%==1 if %tries% lss 60 goto again",
              '(goto) 2>nul & del "%~f0"', ""]
    return "\r\n".join(lines)


def mac_script(paths: list[Path], pid: int) -> str:
    lines = ["#!/bin/sh", f"while kill -0 {pid} 2>/dev/null; do sleep 0.5; done"]
    lines.append("defaults delete com.cliphub.ClipHUB 2>/dev/null")  # settings, also from the prefs cache
    lines += [f'rm -rf "{path}"' for path in paths]
    lines += ['rm -f "$0"', ""]
    return "\n".join(lines)


def remove_everything_after_exit() -> None:
    """Forget the settings now, and start the script that deletes the files once the app quits."""
    settings = QSettings("ClipHUB", "ClipHUB")
    settings.clear()
    settings.sync()
    paths = targets()
    if sys.platform == "win32":
        script = Path(tempfile.gettempdir()) / "cliphub-uninstall.cmd"
        script.write_text(windows_script(paths, os.getpid()), encoding="mbcs")
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        subprocess.Popen(["cmd.exe", "/c", str(script)], creationflags=flags, close_fds=True,  # noqa: S603
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        script = Path(tempfile.gettempdir()) / "cliphub-uninstall.sh"
        script.write_text(mac_script(paths, os.getpid()), encoding="utf-8")
        script.chmod(0o755)
        subprocess.Popen(["/bin/sh", str(script)], start_new_session=True, close_fds=True,  # noqa: S603
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
