"""Long one-off jobs shown on the boot screen: app updates and the GPU pack download.

The window fades out, the black screen shows a title ("Update", "GPU Pack") and a progress bar, then the
window comes back (or, for an update, the app quits so the new version can be swapped in).
"""
from __future__ import annotations

import time
from typing import Callable

from PySide6.QtCore import QObject, QThread, QTimer, Signal
from PySide6.QtWidgets import QApplication, QWidget

from core import app_update
from ui import i18n
from ui.splash import BootSplash


class UpdateChecker(QThread):
    """Lists ClipHUB's releases off the GUI thread (quietly: no internet just means no red dot)."""

    found = Signal(list)  # list[AppRelease], newest first

    def run(self) -> None:
        try:
            releases = app_update.list_releases()
        except Exception:
            releases = []
        self.found.emit(releases)


class _Job(QThread):
    progress = Signal(float)
    failed = Signal(str)
    succeeded = Signal(object)

    def __init__(self, work: Callable, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._work = work
        self.cancelled = False

    def run(self) -> None:
        try:
            result = self._work(self.progress.emit, lambda: self.cancelled)
        except InterruptedError:
            self.failed.emit("")
        except Exception as exc:
            self.failed.emit(str(exc) or exc.__class__.__name__)
        else:
            self.succeeded.emit(result)


class TaskScreen(QObject):
    """Run `work(progress, is_cancelled)` behind a boot-style screen titled `title`."""

    finished = Signal(bool, object)  # ok, result

    def __init__(self, window: QWidget, title: str, status: str, work: Callable,
                 cancellable: bool = True) -> None:
        super().__init__(window)
        self._window = window
        self._status = status
        self.screen = BootSplash(title, crawl=False)
        self.screen.setGeometry(window.geometry())
        self.screen.cancel_hint = cancellable
        self.screen.cancel_requested.connect(self._cancel)
        self._job = _Job(work, self)
        self._job.progress.connect(self._on_progress)
        self._job.succeeded.connect(self._on_success)
        self._job.failed.connect(self._on_failed)
        self._started = 0.0

    def start(self) -> None:
        self.screen.set_status(self._line(0.0))
        self.screen.show()
        self.screen.raise_()
        self.screen.activateWindow()
        self._window.hide()
        self._started = time.monotonic()
        self._job.start()

    def _line(self, value: float) -> str:
        text = f"{i18n.tr(self._status)}  {round(value * 100)}%"
        if self.screen.cancel_hint:
            text += "   ·   " + i18n.tr("กด Esc เพื่อยกเลิก")
        return text

    def _on_progress(self, value: float) -> None:
        self.screen.set_target(value)
        self.screen.set_status(self._line(value))

    def _cancel(self) -> None:
        self._job.cancelled = True
        self.screen.set_status(i18n.tr("กำลังยกเลิก…"))

    def _on_success(self, result: object) -> None:
        self.screen.set_target(1.0)
        self.screen.set_status(self._line(1.0))
        QTimer.singleShot(600, lambda: self._close(True, result))

    def _on_failed(self, message: str) -> None:
        if not message:  # cancelled
            self._close(False, None)
            return
        self.screen.set_status(i18n.tr("ไม่สำเร็จ: ") + message)
        QTimer.singleShot(3500, lambda: self._close(False, message))

    def _close(self, ok: bool, result: object) -> None:
        self.finished.emit(ok, result)
        if ok and getattr(result, "quit_app", False):
            return  # the app is about to quit; keep the screen up until it does
        self._window.show()
        self.screen.finish(self._window)


class _Quit:
    quit_app = True


def install_release(window: QWidget, release: app_update.AppRelease) -> TaskScreen:
    """Download `release`, start the swap script, and quit: the new version opens by itself."""

    def work(progress, is_cancelled):
        app_update.clean_downloads()
        installer = app_update.download(release, progress, is_cancelled)
        app_update.install_after_exit(installer)
        return _Quit()

    screen = TaskScreen(window, "Update " + release.label, "กำลังดาวน์โหลด " + release.label + "…", work)

    def done(ok: bool, _result: object) -> None:
        if ok:  # close the window properly (it cancels and waits for jobs), then quit
            QTimer.singleShot(300, lambda: (window.close(), QApplication.instance().quit()))

    screen.finished.connect(done)
    screen.start()
    return screen
