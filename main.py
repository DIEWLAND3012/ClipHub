"""ClipHUB entry point: theme, boot screen, then the main window."""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow
from ui.splash import boot
from ui.theme import apply_theme


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("ClipHUB")
    apply_theme(app)
    # The boot screen preloads yt-dlp and Whisper and tests the GPUs, then fades into the window.
    boot(app, lambda report: MainWindow(hardware=report))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
