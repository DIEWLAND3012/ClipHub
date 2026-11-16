"""Main window: floating glass sidebar, feature pages and the task queue rail (owner: member 1)."""
from __future__ import annotations

import time

from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEasingCurve, QPropertyAnimation, QRect, QSize, Qt, QThread, QTimer
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.task_queue import TaskQueue
from tasks.subtitle import SubtitleTask
from ui import theme
from ui.pages import PAGES
from ui.pages.subtitle_page import SubtitlePage
from ui.overlays import ProgressPill, ToastHost, reveal_in_folder, set_host
from ui.queue_panel import QueuePanel
from ui.widgets import Backdrop, GlassCard, Segmented, bind_icon, label

APP_VERSION = "v1.1"
SIDEBAR_WIDTH = 240
RAIL_WIDTH = 320
CONTENT_MAX_WIDTH = 760
SHUTDOWN_WAIT_SECONDS = 5.0
STILL_STOPPING_TEXT = (
    "งานที่กำลังทำยังหยุดไม่เสร็จ (เช่น กำลังดึงข้อมูลวิดีโอ หรือโหลดโมเดล AI)\n"
    "โปรดรอสักครู่แล้วปิดหน้าต่างอีกครั้ง"
)
APPEARANCE_ICONS = {"system": "auto", "light": "sun", "dark": "moon"}


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("ClipHUB")
        self.setMinimumSize(1080, 700)
        self.resize(1260, 830)
        self.queue = TaskQueue(self)
        self.nav_buttons: list[QPushButton] = []
        self.pages: list[QWidget] = []

        central = Backdrop()
        row = QHBoxLayout(central)
        row.setContentsMargins(10, 10, 10, 10)
        row.setSpacing(6)
        self.nav_group = QButtonGroup(self)
        self.stack = QStackedWidget()
        row.addWidget(self._build_sidebar())
        row.addWidget(self._build_content(), 1)
        rail = GlassCard("panel", radius=24)
        rail.setFixedWidth(RAIL_WIDTH)
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(0, 0, 0, 0)
        self.queue_panel = QueuePanel(self.queue)
        rail_layout.addWidget(self.queue_panel)
        row.addWidget(rail)
        self.setCentralWidget(central)
        self._central = central

        # Floating overlays live on top of the backdrop, over the content column.
        self.toasts = ToastHost(central)
        set_host(self.toasts)
        self.pill = ProgressPill(
            self.queue, self.queue_panel.cancel, lambda: len(self.queue_panel.unfinished_tasks()), central,
        )
        self.queue.task_done.connect(self._toast_done)
        self.queue.task_failed.connect(self._toast_failed)
        self.stack.currentChanged.connect(self._fade_in_page)
        theme.round_combo_popups(self)

        self.nav_group.idClicked.connect(self.stack.setCurrentIndex)
        self.nav_buttons[0].setChecked(True)
        for index in range(len(self.nav_buttons)):  # Ctrl/Cmd+1..4 jumps between tools
            shortcut = QShortcut(QKeySequence(f"Ctrl+{index + 1}"), self)
            shortcut.activated.connect(lambda i=index: self.nav_buttons[i].click())

    def _build_sidebar(self) -> GlassCard:
        sidebar = GlassCard("panel", radius=24)
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(SIDEBAR_WIDTH)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(12, 18, 12, 14)
        layout.setSpacing(2)

        mark = QLabel()
        mark.setObjectName("BrandMark")
        mark.setFixedSize(40, 40)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setPixmap(theme.make_icon("play", "#ffffff", 20).pixmap(20, 20))
        texts = QVBoxLayout()
        texts.setSpacing(0)
        texts.addWidget(label("ClipHUB", "BrandName"))
        texts.addWidget(label(APP_VERSION, "BrandVersion"))
        brand = QHBoxLayout()
        brand.setContentsMargins(6, 0, 6, 18)
        brand.setSpacing(11)
        brand.addWidget(mark)
        brand.addLayout(texts)
        brand.addStretch(1)
        layout.addLayout(brand)

        for index, (text, icon_key, _page_cls) in enumerate(PAGES):
            button = QPushButton(text)
            button.setProperty("nav", True)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(f"{text}  (Ctrl+{index + 1})")
            bind_icon(button, icon_key, "accent", 19, checked_role="accent")
            self.nav_group.addButton(button, index)
            self.nav_buttons.append(button)
            layout.addWidget(button)
        layout.addStretch(1)
        layout.addWidget(self._build_appearance())
        return sidebar

    def _build_appearance(self) -> QWidget:
        """Three-way switch: follow the OS, always light, always dark."""
        box = QWidget()
        column = QVBoxLayout(box)
        column.setContentsMargins(2, 0, 2, 0)
        column.setSpacing(7)
        self.appearance = Segmented()
        manager = theme.manager()
        current = manager.appearance if manager is not None else "system"
        for index, key in enumerate(theme.APPEARANCES):
            button = self.appearance.add("", index, APPEARANCE_ICONS[key], theme.APPEARANCE_LABELS[key])
            button.setIconSize(QSize(16, 16))
            button.setChecked(key == current)
        self.appearance.group.idClicked.connect(self._on_appearance)
        column.addWidget(self.appearance)
        return box

    def _on_appearance(self, index: int) -> None:
        key = theme.APPEARANCES[index]
        manager = theme.manager()
        if manager is None or manager.appearance == key:
            return
        # Cross-fade: freeze the old look in a snapshot, switch, then fade the snapshot away.
        central = self.centralWidget()
        snapshot = QLabel(central)
        snapshot.setPixmap(central.grab())
        snapshot.setGeometry(central.rect())
        snapshot.show()
        snapshot.raise_()
        manager.set_appearance(key)
        effect = QGraphicsOpacityEffect(snapshot)
        snapshot.setGraphicsEffect(effect)
        fade = QPropertyAnimation(effect, b"opacity", snapshot)
        fade.setDuration(320)
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.Type.InOutQuad)
        fade.finished.connect(snapshot.deleteLater)
        fade.start()

    def _fade_in_page(self, index: int) -> None:
        page = self.stack.widget(index)
        if page is None or not self.isVisible():
            return
        effect = QGraphicsOpacityEffect(page)
        page.setGraphicsEffect(effect)
        fade = QPropertyAnimation(effect, b"opacity", page)
        fade.setDuration(200)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade.finished.connect(lambda: page.setGraphicsEffect(None))  # keep text crisp afterwards
        fade.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def _build_content(self) -> QScrollArea:
        for _label, _icon_key, page_cls in PAGES:
            page = page_cls()
            page.task_submitted.connect(self.queue.enqueue)
            self.pages.append(page)
            self.stack.addWidget(page)
        self.queue.task_done.connect(self._on_task_done)

        # One centred column: comfortable line length on wide windows, Apple-style whitespace.
        self.stack.setMaximumWidth(CONTENT_MAX_WIDTH)
        holder = QWidget()
        holder_layout = QHBoxLayout(holder)
        holder_layout.setContentsMargins(28, 34, 24, 110)  # bottom room for the progress pill
        holder_layout.addStretch(1)
        holder_layout.addWidget(self.stack, 100)
        holder_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(holder)
        scroll.viewport().setAutoFillBackground(False)
        holder.setAutoFillBackground(False)
        self.content_scroll = scroll
        return scroll

    # --- overlays -------------------------------------------------------------
    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().resizeEvent(event)
        QTimer.singleShot(0, self._place_overlays)

    def showEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().showEvent(event)
        QTimer.singleShot(0, self._place_overlays)

    def _place_overlays(self) -> None:
        area = QRect(self.content_scroll.mapTo(self._central, self.content_scroll.rect().topLeft()),
                     self.content_scroll.size())
        self.pill.place(area)
        self.toasts.place(area)

    def _toast_done(self, task: object, output: object) -> None:
        name = getattr(task, "display_name", "") or getattr(task, "title", "งาน")
        result = getattr(task, "result", None)
        saved = Path(str(output))
        if result is not None:
            body = result.describe()  # compressor: original vs new size
        else:
            body = f"บันทึกใน {saved.parent.name or saved.parent}" if name in saved.stem else saved.name
        if getattr(task, "write_file", True):
            actions = [("แสดงในโฟลเดอร์", lambda: reveal_in_folder(str(output)))]
        else:  # subtitles are previewed first, then exported from the subtitle page
            body = f"{len(getattr(task, 'cues', ()))} ประโยค · พร้อมส่งออก"
            actions = [("ดูผลถอดความ", lambda: self.nav_buttons[3].click())]
        self.toasts.show("success", f"{name} เสร็จแล้ว", body, actions)

    def _toast_failed(self, task: object, message: str) -> None:
        name = getattr(task, "display_name", "") or getattr(task, "title", "งาน")
        self.toasts.show("error", f"{name} ไม่สำเร็จ", message)

    def _on_task_done(self, task: object, _output: object) -> None:
        """A finished subtitle job shows its cues in the subtitle page, whether or not a file was written."""
        if not isinstance(task, SubtitleTask):
            return
        for page in self.pages:
            if isinstance(page, SubtitlePage):
                page.show_preview(task)

    def closeEvent(self, event: QCloseEvent) -> None:
        """Cancel running work and wait for it. Qt aborts if a live QThread is destroyed, so a close that cannot finish is refused."""
        for task in self.queue_panel.unfinished_tasks():
            self.queue.cancel(task)
        deadline = time.monotonic() + SHUTDOWN_WAIT_SECONDS
        while not self._is_idle() and time.monotonic() < deadline:
            QCoreApplication.processEvents()
            QThread.msleep(20)
        if not self._is_idle():
            event.ignore()
            QMessageBox.information(self, "ยังหยุดงานไม่เสร็จ", STILL_STOPPING_TEXT)
            return
        super().closeEvent(event)

    def _is_idle(self) -> bool:
        """True when no task runs and every page reports its own worker has finished."""
        pages_idle = all(page.shutdown() for page in self.pages if callable(getattr(page, "shutdown", None)))
        return pages_idle and not self.queue.is_busy()
