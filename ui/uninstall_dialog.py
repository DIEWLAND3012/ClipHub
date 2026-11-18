"""The uninstall popup: a large card in the middle of the window with the app icon, Cancel / OK.

OK turns the buttons into a progress bar while ClipHUB's files are removed; when it is full the app quits
(a small script then deletes the app itself and whatever was still in use).
"""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget

from core import uninstaller
from ui.app_icon import icon_pixmap
from ui.widgets import fade_in, label


class _Remover(QThread):
    progress = Signal(float)

    def run(self) -> None:
        try:
            uninstaller.remove_now(self.progress.emit)
        except Exception:
            pass  # whatever is left goes with the script after the app quits
        self.progress.emit(1.0)


class UninstallDialog(QWidget):
    """Covers the window (dimmed); the card sits in the middle."""

    cancelled = Signal()
    removed = Signal()  # the files are gone: quit now

    CARD_WIDTH = 440

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.card = QFrame(self)  # solid, so nothing behind shows through the text
        self.card.setObjectName("DialogCard")
        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(60)
        shadow.setOffset(0, 18)
        shadow.setColor(QColor(0, 0, 0, 90))
        self.card.setGraphicsEffect(shadow)
        self.card.setFixedWidth(self.CARD_WIDTH)
        box = QVBoxLayout(self.card)
        box.setContentsMargins(36, 34, 32, 30)
        box.setSpacing(10)
        icon = QLabel()
        icon.setPixmap(icon_pixmap(96, max(self.devicePixelRatioF(), 2.0)))
        icon.setFixedSize(96, 96)
        icon.setScaledContents(True)
        box.addWidget(icon, 0, Qt.AlignmentFlag.AlignHCenter)
        box.addSpacing(6)
        self.title = label("ถอนการติดตั้ง ClipHUB", "DialogTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(self.title)
        self.text = label("ลบ ClipHUB, FFmpeg, ทรัพยากรการ์ดจอ, โมเดล AI ซับไตเติล และการตั้งค่าทั้งหมด "
                          "ออกจากเครื่องนี้ ย้อนกลับไม่ได้", "DialogText", wrap=True)
        self.text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(self.text)
        box.addSpacing(12)

        self.buttons = QWidget()
        row = QHBoxLayout(self.buttons)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        self.cancel_button = QPushButton("ยกเลิก")
        self.cancel_button.setObjectName("DialogCancel")
        self.ok_button = QPushButton("ตกลง")
        self.ok_button.setObjectName("DialogDanger")
        for button in (self.cancel_button, self.ok_button):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            row.addWidget(button, 1)
        self.cancel_button.clicked.connect(self.close_dialog)
        self.ok_button.clicked.connect(self._start)
        box.addWidget(self.buttons)

        self.progress = QProgressBar()
        self.progress.setObjectName("UninstallBar")
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.hide()
        box.addWidget(self.progress)
        self.status = label("", "Note")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.hide()
        box.addWidget(self.status)
        self._remover = _Remover(self)
        self._remover.progress.connect(self._on_progress)
        self._value = 0.0
        self._done = False
        self.hide()

    # --- open / close ------------------------------------------------------------------------
    def open_dialog(self) -> None:
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        self._place()
        fade_in(self, 200)
        target = self.card.pos()
        grow = QPropertyAnimation(self.card, b"pos", self.card)
        grow.setDuration(320)
        grow.setStartValue(target + type(target)(0, 24))
        grow.setEndValue(target)
        grow.setEasingCurve(QEasingCurve.Type.OutBack)
        grow.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self.cancel_button.setFocus()

    def close_dialog(self) -> None:
        if self._remover.isRunning():
            return
        self.hide()
        self.cancelled.emit()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt API)
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 110))  # dim the app behind the popup

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().resizeEvent(event)
        self._place()

    def _place(self) -> None:
        self.card.adjustSize()
        self.card.move((self.width() - self.card.width()) // 2, (self.height() - self.card.height()) // 2)

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if event.key() == Qt.Key.Key_Escape:
            self.close_dialog()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if not self.card.geometry().contains(event.position().toPoint()):
            self.close_dialog()  # a click outside the card cancels, like the other popups

    # --- removing ----------------------------------------------------------------------------
    def _start(self) -> None:
        self.buttons.hide()
        self.text.setText("กำลังถอนการติดตั้ง… อย่าปิดโปรแกรม")
        self.progress.show()
        self.status.show()
        self._on_progress(0.0)
        self._place()
        self._remover.start()

    def _on_progress(self, value: float) -> None:
        self._value = max(self._value, value)
        self.progress.setValue(round(self._value * 1000))
        self.status.setText(f"{round(self._value * 100)}%")
        if self._value >= 1.0 and not self._done:
            self._done = True
            self.text.setText("ถอนการติดตั้งเสร็จแล้ว")
            QTimer.singleShot(900, self.removed.emit)
