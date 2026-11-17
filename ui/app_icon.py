"""The ClipHUB app icon (assets/icons/cliphub.svg), rendered for windows, the taskbar and the UI."""
from __future__ import annotations

import re
import sys
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap


def _icons_dir() -> Path:
    root = getattr(sys, "_MEIPASS", None)
    return (Path(root) if root else Path(__file__).resolve().parent.parent) / "assets" / "icons"


@lru_cache(maxsize=1)
def _svg_data() -> QByteArray:
    raw = (_icons_dir() / "cliphub.svg").read_text(encoding="utf-8")
    # The design tool adds style="...color(display-p3 ...)" next to every colour. Qt cannot read
    # Display-P3 and would draw those parts black, so drop the styles and keep the sRGB attributes.
    return QByteArray(re.sub(r'\sstyle="[^"]*"', "", raw).encode("utf-8"))


def render_icon(size: int) -> QImage:
    from PySide6.QtSvg import QSvgRenderer

    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    QSvgRenderer(_svg_data()).render(painter)
    painter.end()
    return image


def icon_pixmap(size: int, device_ratio: float = 2.0) -> QPixmap:
    """Crisp pixmap of `size` logical pixels (rendered at device_ratio)."""
    pixmap = QPixmap.fromImage(render_icon(round(size * device_ratio)))
    pixmap.setDevicePixelRatio(device_ratio)
    return pixmap


@lru_cache(maxsize=1)
def app_icon() -> QIcon:
    """Window / taskbar icon with every common size, so Windows never scales a blurry one."""
    icon = QIcon()
    for size in (16, 20, 24, 32, 40, 48, 64, 128, 256, 512):
        icon.addPixmap(QPixmap.fromImage(render_icon(size)))
    return icon


def set_windows_app_id() -> None:
    """Give the process its own taskbar identity, so Windows shows ClipHUB's icon, not Python's."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ClipHUB.MediaToolkit")
    except (AttributeError, OSError):
        pass
