"""Boot screen in the spirit of a Mac starting up: black, the ClipHUB mark, a thin progress bar.

The bar follows real work done while it shows (loading yt-dlp and Whisper, testing the GPUs,
building the window), so the app is ready the moment the screen fades away. A startup sound
plays if assets/sounds/startup.wav exists.
"""
from __future__ import annotations

import importlib
import sys
import time
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRectF, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget

from core import hwaccel
from ui.app_icon import app_icon, icon_pixmap

MIN_SECONDS = 3.2  # long enough to feel like a boot, short enough not to wait


def _asset(*parts: str) -> Path:
    root = getattr(sys, "_MEIPASS", None)
    return (Path(root) if root else Path(__file__).resolve().parent.parent).joinpath("assets", *parts)


class Preloader(QThread):
    """The slow parts of start-up, done off the GUI thread while the boot screen shows."""

    progress = Signal(float)  # 0.0 - 1.0 of the background work

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.report: hwaccel.HardwareReport | None = None

    def run(self) -> None:
        self.progress.emit(0.08)
        try:
            importlib.import_module("yt_dlp")  # the downloader's first use is instant afterwards
        except Exception:
            pass
        self.progress.emit(0.25)
        try:
            importlib.import_module("faster_whisper")
        except Exception:
            pass
        self.progress.emit(0.4)
        try:
            self.report = hwaccel.refresh()  # test encodes on every GPU
        except Exception:
            self.report = hwaccel.HardwareReport(ready=True)
        self.progress.emit(1.0)


class BootSplash(QWidget):
    """Frameless black window with the logo, the name and a thin progress bar."""

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.SplashScreen)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._value = 0.0    # what the bar shows
        self._target = 0.0   # where the work has got to
        self._logo = 0.0     # logo fade-in
        self._started = time.monotonic()
        self._tick = QTimer(self)
        self._tick.timeout.connect(self._step)
        self._tick.start(16)
        self._sound = None
        self._icon = icon_pixmap(96, max(self.devicePixelRatioF(), 2.0))
        self.setWindowIcon(app_icon())

    # --- sound -----------------------------------------------------------------------
    def play_sound(self) -> None:
        path = _asset("sounds", "startup.wav")
        if not path.is_file():
            return
        try:
            from PySide6.QtMultimedia import QSoundEffect
        except ImportError:
            return
        self._sound = QSoundEffect(self.parent() or None)
        self._sound.setSource(QUrl.fromLocalFile(str(path)))
        self._sound.setVolume(0.9)
        self._sound.play()

    def take_sound(self):
        """Hand the playing sound to someone who outlives the splash, so it is not cut off."""
        sound, self._sound = self._sound, None
        return sound

    # --- progress ----------------------------------------------------------------------
    def set_target(self, value: float) -> None:
        self._target = max(self._target, min(value, 1.0))

    def elapsed(self) -> float:
        return time.monotonic() - self._started

    def done(self) -> bool:
        return self._value >= 0.995 and self.elapsed() >= MIN_SECONDS

    def _step(self) -> None:
        # Never run ahead of the minimum time, then ease toward the real progress (macOS-like crawl).
        time_cap = min(self.elapsed() / MIN_SECONDS, 1.0)
        goal = min(self._target, 0.15 + 0.85 * time_cap)
        speed = 0.3 if self._target >= 1.0 else 0.1  # once everything is ready, finish briskly
        self._value += (goal - self._value) * speed
        if goal - self._value < 0.002:
            self._value = goal
        self._logo = min(1.0, self.elapsed() / 0.7)
        self.update()

    # --- drawing -------------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 (Qt API)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        frame = QPainterPath()
        frame.addRoundedRect(QRectF(self.rect()), 12, 12)
        painter.fillPath(frame, QColor("#000000"))

        w, h = self.width(), self.height()
        painter.setOpacity(self._logo)
        mark = 96
        top = h / 2 - 92
        logo = QRectF(w / 2 - mark / 2, top, mark, mark)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(logo.toRect(), self._icon)  # the ClipHUB app icon

        font = QFont(self.font())
        font.setPixelSize(22)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(QRectF(0, logo.bottom() + 18, w, 32), Qt.AlignmentFlag.AlignCenter, "ClipHUB")

        painter.setOpacity(1.0)
        bar_w, bar_h = 220, 5
        bar = QRectF(w / 2 - bar_w / 2, logo.bottom() + 80, bar_w, bar_h)
        track = QPainterPath()
        track.addRoundedRect(bar, bar_h / 2, bar_h / 2)
        painter.fillPath(track, QColor(255, 255, 255, 52))
        if self._value > 0:
            fill = QPainterPath()
            fill.addRoundedRect(QRectF(bar.left(), bar.top(), max(bar_h, bar_w * self._value), bar_h),
                                bar_h / 2, bar_h / 2)
            painter.fillPath(fill, QColor("#ffffff"))

    # --- hand-over ---------------------------------------------------------------------------
    def finish(self, window: QWidget, on_done=None) -> None:
        """Cross-fade from the boot screen to the main window."""
        self._tick.stop()
        window.setWindowOpacity(0.0)
        window.show()
        window.raise_()
        window.activateWindow()
        show = QPropertyAnimation(window, b"windowOpacity", window)
        show.setDuration(420)
        show.setStartValue(0.0)
        show.setEndValue(1.0)
        show.setEasingCurve(QEasingCurve.Type.OutCubic)
        hide = QPropertyAnimation(self, b"windowOpacity", self)
        hide.setDuration(360)
        hide.setStartValue(1.0)
        hide.setEndValue(0.0)
        hide.finished.connect(self.close)
        if on_done is not None:
            show.finished.connect(on_done)
        show.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        hide.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)


def boot(app, make_window) -> None:
    """Show the boot screen, preload in the background, build the window, then cross-fade to it.

    make_window(report) builds and returns the main window; it is kept alive on the app.
    """
    from ui.main_window import default_window_rect  # same place and size as the window it becomes

    splash = BootSplash()
    splash.setGeometry(default_window_rect())
    splash.show()
    splash.play_sound()
    loader = Preloader(splash)
    loader.progress.connect(lambda v: splash.set_target(0.85 * v))

    def build() -> None:
        window = make_window(loader.report)
        app._main_window = window  # keep a reference for the app's lifetime
        app._startup_sound = splash.take_sound()  # let the chime finish under the window
        splash.set_target(1.0)

        def wait_then_finish() -> None:
            if splash.done():
                splash.finish(window)
            else:
                QTimer.singleShot(30, wait_then_finish)

        wait_then_finish()

    loader.finished.connect(build)
    loader.start()

