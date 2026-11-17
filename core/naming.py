"""Output file names: make user-typed names safe on Windows/macOS and never overwrite a file."""
from __future__ import annotations

import os
import re
from collections.abc import Iterable
from pathlib import Path

MAX_NAME = 180
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def safe_filename(name: str) -> str:
    """Remove characters Windows or macOS forbid, collapse spaces, trim trailing dots. "" if nothing is left."""
    cleaned = _ILLEGAL.sub(" ", name or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(". ").strip()
    cleaned = cleaned[:MAX_NAME].rstrip(". ").strip()
    if cleaned.lower() in _RESERVED:
        cleaned = f"{cleaned}_"
    return cleaned


def strip_extension(name: str, ext: str) -> str:
    """'clip.mp4' typed for an MP4 output becomes 'clip' (the extension is added by the app)."""
    suffix = f".{ext.lower().lstrip('.')}"
    return name[: -len(suffix)] if name.lower().endswith(suffix) else name


def unique_path(folder: str | Path, stem: str, ext: str, taken: Iterable[str | Path] = ()) -> Path:
    """folder/stem.ext, or 'stem (2).ext', 'stem (3).ext'... when that name exists or is reserved."""
    folder = Path(folder)
    ext = ext.lower().lstrip(".")
    reserved = {os.path.normcase(str(p)) for p in taken}
    count = 1
    while True:
        name = stem if count == 1 else f"{stem} ({count})"
        candidate = folder / f"{name}.{ext}"
        if not candidate.exists() and os.path.normcase(str(candidate)) not in reserved:
            return candidate
        count += 1
