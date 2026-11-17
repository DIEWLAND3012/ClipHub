"""ClipHUB entry point: theme, boot screen, then the main window."""
from __future__ import annotations

import sys

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from ui import i18n
from ui.app_icon import app_icon, set_windows_app_id
from ui.main_window import MainWindow
from ui.splash import boot
from ui.theme import apply_theme


def main() -> int:
    set_windows_app_id()  # before any window: the taskbar then groups ClipHUB under its own icon
    app = QApplication(sys.argv)
    app.setApplicationName("ClipHUB")
    app.setApplicationVersion("2.1.0")
    app.setWindowIcon(app_icon())  # every window, dialog and the taskbar
    # Every on-screen text goes through ui.i18n; the saved language applies from the first window.
    i18n.install()
    i18n.set_language(str(QSettings("ClipHUB", "ClipHUB").value("ui/language", "th")))
    apply_theme(app)
    # The boot screen preloads yt-dlp and Whisper and tests the GPUs, then fades into the window.
    boot(app, lambda report: MainWindow(hardware=report))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
