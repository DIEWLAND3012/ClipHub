"""Downloads that keep going: a stalled or crawling connection is dropped and resumed where it stopped.

CDNs (PyPI's in particular) sometimes hand out a connection that runs at a few KB/s while the next one runs at
MB/s. So the speed is watched, and a slow connection is replaced by a new HTTP Range request.
"""
from __future__ import annotations

import hashlib
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

Progress = Callable[[float], None]

CHUNK = 1024 * 1024
SLOW_BYTES_PER_SEC = 256 * 1024  # below this for SLOW_WINDOW seconds: reconnect
SLOW_WINDOW = 8.0
READ_TIMEOUT = 20
MAX_RECONNECTS = 40


def _open(url: str, start: int):
    headers = {"User-Agent": "ClipHUB"}
    if start:
        headers["Range"] = f"bytes={start}-"
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=READ_TIMEOUT)  # noqa: S310


def download(url: str, target: Path, progress: Progress | None = None,
             is_cancelled: Callable[[], bool] = lambda: False, size: int = 0) -> str:
    """Download url to target and return its SHA-256. progress gets 0..1 when the size is known."""
    done = 0
    total = size
    reconnects = 0
    with open(target, "wb") as out:
        while True:
            try:
                response = _open(url, done)
            except (urllib.error.URLError, TimeoutError, OSError):
                reconnects += 1
                if reconnects > MAX_RECONNECTS:
                    raise
                time.sleep(1)
                continue
            with response:
                if done and response.status != 206:  # the server ignored Range: start again
                    out.seek(0)
                    out.truncate()
                    done = 0
                if not total:
                    length = int(response.headers.get("Content-Length") or 0)
                    total = length + done if length else 0
                window_start, window_bytes = time.monotonic(), 0
                finished = False
                try:
                    while True:
                        if is_cancelled():
                            raise InterruptedError("cancelled")
                        chunk = response.read1(CHUNK)
                        if not chunk:
                            finished = True
                            break
                        out.write(chunk)
                        done += len(chunk)
                        window_bytes += len(chunk)
                        if progress and total:
                            progress(min(done / total, 1.0))
                        elapsed = time.monotonic() - window_start
                        if elapsed >= SLOW_WINDOW:
                            if window_bytes / elapsed < SLOW_BYTES_PER_SEC and reconnects < MAX_RECONNECTS:
                                break  # crawling: try a fresh connection
                            window_start, window_bytes = time.monotonic(), 0
                except (TimeoutError, OSError):
                    pass  # stalled: reconnect and resume
            if finished and (not total or done >= total):
                break
            reconnects += 1
            if reconnects > MAX_RECONNECTS:
                raise RuntimeError("The download keeps stalling. Check your internet connection.")
    digest = hashlib.sha256()
    with open(target, "rb") as data:
        while block := data.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()
