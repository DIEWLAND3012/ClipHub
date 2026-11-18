"""Small helper scripts that run after ClipHUB quits (update swap, uninstall), written safely.

- Paths are quoted for the shell that runs them: cmd.exe would expand %VAR% inside a path, /bin/sh would
  expand $(...) and backticks, so both are escaped.
- Each script gets a fresh, unpredictable file name (mkstemp), so nothing else can plant or swap it.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path


def cmd_quote(path: str | Path) -> str:
    """A path for a .cmd file: in double quotes, with % doubled ("" cannot appear in Windows paths)."""
    text = str(path)
    if '"' in text or "\n" in text or "\r" in text:
        raise ValueError(f"unsafe path: {text!r}")
    return '"' + text.replace("%", "%%") + '"'


def sh_quote(path: str | Path) -> str:
    return shlex.quote(str(path))


def write(text: str, suffix: str) -> Path:
    fd, name = tempfile.mkstemp(prefix="cliphub-", suffix=suffix)
    with os.fdopen(fd, "w", encoding="mbcs" if suffix == ".cmd" else "utf-8", newline="") as out:
        out.write(text)
    if suffix != ".cmd":
        os.chmod(name, 0o700)
    return Path(name)


def run_detached(script: Path) -> None:
    """Start the script on its own, so it outlives the app."""
    quiet = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
             "close_fds": True}
    if sys.platform == "win32":
        # A hidden console of its own (not DETACHED_PROCESS): the commands inside (ping, tasklist, find) share it
        # instead of each opening a terminal window, which is what happened from the windowless app.
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        subprocess.Popen(["cmd.exe", "/c", str(script)], creationflags=flags, **quiet)  # noqa: S603
    else:
        subprocess.Popen(["/bin/sh", str(script)], start_new_session=True, **quiet)  # noqa: S603
