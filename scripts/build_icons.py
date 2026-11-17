"""Render assets/icons/cliphub.svg into the files each platform needs (run after changing the SVG).

Writes assets/icons/cliphub.ico (Windows exe, taskbar, shortcuts), cliphub.icns (macOS app)
and cliphub.png (512 px). Usage: python scripts/build_icons.py
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QBuffer, QByteArray, QIODevice  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

from ui.app_icon import render_icon  # noqa: E402

ICONS = ROOT / "assets" / "icons"


def png_bytes(size: int) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    render_icon(size).save(buffer, "PNG")
    return bytes(data)


def write_ico(path: Path, sizes: tuple[int, ...]) -> None:
    """ICO with PNG-compressed entries (supported since Windows Vista)."""
    images = [png_bytes(size) for size in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = b""
    for size, data in zip(sizes, images):
        side = 0 if size >= 256 else size  # 0 means 256 in the ICO format
        entries += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    path.write_bytes(header + entries + b"".join(images))


def write_icns(path: Path) -> None:
    """ICNS with PNG entries: ic07 128, ic08 256, ic09 512, ic10 1024 (512@2x)."""
    chunks = b""
    for kind, size in (("ic07", 128), ("ic08", 256), ("ic09", 512), ("ic10", 1024)):
        data = png_bytes(size)
        chunks += kind.encode() + struct.pack(">I", len(data) + 8) + data
    path.write_bytes(b"icns" + struct.pack(">I", len(chunks) + 8) + chunks)


def main() -> int:
    QGuiApplication(sys.argv)
    write_ico(ICONS / "cliphub.ico", (16, 20, 24, 32, 40, 48, 64, 128, 256))
    write_icns(ICONS / "cliphub.icns")
    (ICONS / "cliphub.png").write_bytes(png_bytes(512))
    for name in ("cliphub.ico", "cliphub.icns", "cliphub.png"):
        print(f"{name}: {(ICONS / name).stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
