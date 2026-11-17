"""Main window: glass sidebar, feature pages, and popups for the queue and performance (owner: member 1).

Layout: sidebar | content. The content is the page (scrolls only when the window is too small)
with a fixed action bar underneath, so the action button sits in the same place on every page.
The queue and performance panels open as popups beside the sidebar. Below 940 px the sidebar
collapses to icons.
"""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEasingCurve, QPoint, QPropertyAnimation, QRect, Qt, QThread, QTimer
from PySide6.QtGui import QCloseEvent, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core import hwaccel
from core.task_queue import TaskQueue
from tasks.subtitle import SubtitleTask
from ui import i18n, theme
from ui.overlays import ProgressPill, ToastHost, reveal_in_folder, set_host
from ui.pages import PAGES
from ui.pages.subtitle_page import SubtitlePage
from ui.performance import HardwareProbe, PerformancePanel
from ui.queue_panel import QueuePanel
from ui.sidebar import Sidebar
from ui.widgets import Backdrop, GlassCard, fade_in, pulse

APP_VERSION = "v1.1"
QUEUE_WIDTH = 340
CONTENT_MAX_WIDTH = 1120
COMPACT_SIDEBAR_WIDTH = 940   # below this the sidebar shows icons only
MIN_SIZE = (720, 560)
SHUTDOWN_WAIT_SECONDS = 5.0
STILL_STOPPING_TEXT = (
    "งานที่กำลังทำยังหยุดไม่เสร็จ (เช่น กำลังดึงข้อมูลวิดีโอ หรือโหลดโมเดล AI)\n"
    "โปรดรอสักครู่แล้วปิดหน้าต่างอีกครั้ง"
)


class _Scrim(QWidget):
    """Dims the content behind an open popup; a click on it closes the popup."""

    def __init__(self, on_click, parent: QWidget) -> None:
        super().__init__(parent)
        self._on_click = on_click
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("Scrim")
        self.hide()

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt API)
        self._on_click()


def default_window_rect() -> QRect:
    """Comfortable opening size (about 2/3 of the usable screen, at most 1080 x 720), centred."""
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return QRect(100, 100, 1000, 680)
    area = screen.availableGeometry()
    width = max(MIN_SIZE[0], min(1080, round(area.width() * 0.66)))
    height = max(MIN_SIZE[1], min(720, round(area.height() * 0.82)))
    return QRect(area.center().x() - width // 2, area.center().y() - height // 2, width, height)


class MainWindow(QMainWindow):
    def __init__(self, hardware: hwaccel.HardwareReport | None = None) -> None:
        """`hardware` is the report the boot screen already made; without it the GPUs are probed here."""
        super().__init__()
        self.setWindowTitle("ClipHUB")
        self.setMinimumSize(*MIN_SIZE)
        self.setGeometry(default_window_rect())
        self.queue = TaskQueue(self)
        self.pages: list[QWidget] = []
        self.stack = QStackedWidget()
        self.actions_stack = QStackedWidget()

        central = Backdrop()
        self._central = central
        row = QHBoxLayout(central)
        row.setContentsMargins(10, 10, 10, 10)
        row.setSpacing(6)
        self.sidebar = Sidebar([(text, icon) for text, icon, _cls in PAGES], APP_VERSION)
        self.nav_buttons = self.sidebar.buttons
        self.appearance = self.sidebar.appearance
        row.addWidget(self.sidebar)
        row.addWidget(self._build_content(), 1)
        self.setCentralWidget(central)

        # Popups and floating overlays on top of the backdrop.
        self.scrim = _Scrim(self._close_overlays, central)
        self.rail = GlassCard("drawer", radius=22, parent=central)
        rail_layout = QVBoxLayout(self.rail)
        rail_layout.setContentsMargins(0, 0, 0, 0)
        self.queue_panel = QueuePanel(self.queue)
        self.queue_panel.close_button.show()
        self.queue_panel.close_button.clicked.connect(lambda: self.set_queue_open(False))
        rail_layout.addWidget(self.queue_panel)
        self.rail.hide()
        self.perf = PerformancePanel(central)
        self.perf.close_requested.connect(lambda: self.set_perf_open(False))
        self.perf.settings_changed.connect(self._configure_queue)
        self.perf.size_changed.connect(self._resize_perf_popup)
        self.perf.language_changed.connect(self._on_language)
        self.toasts = ToastHost(central)
        set_host(self.toasts)
        self.pill = ProgressPill(
            self.queue, self.queue_panel.cancel, lambda: len(self.queue_panel.unfinished_tasks()), central,
        )
        self.pill.clicked = lambda: self.set_queue_open(True)

        self._configure_queue()
        # Find out which GPUs really work (a few seconds of test encodes) without blocking the window.
        self._probe = HardwareProbe(self)
        self._probe.done.connect(self._on_hardware)
        if hardware is None:
            QTimer.singleShot(300, self._probe.start)
        else:
            QTimer.singleShot(0, lambda: self._on_hardware(hardware))

        self.queue.task_done.connect(self._toast_done)
        self.queue.task_failed.connect(self._toast_failed)
        for signal in (self.queue.task_added, self.queue.task_done, self.queue.task_failed, self.queue.task_cancelled):
            signal.connect(lambda *_args: self._update_queue_badge())
        self.queue.task_added.connect(lambda _task: pulse(self.sidebar.queue_button))
        self.queue.task_started.connect(lambda _task: self._keep_popups_on_top())  # after the pill raises itself
        self.stack.currentChanged.connect(self._on_page_changed)
        theme.round_combo_popups(self)

        self.sidebar.page_selected.connect(self.stack.setCurrentIndex)
        self.sidebar.appearance_selected.connect(self._on_appearance)
        self.sidebar.queue_clicked.connect(lambda: self.set_queue_open(not self.rail.isVisible()))
        self.sidebar.performance_clicked.connect(lambda: self.set_perf_open(not self.perf.isVisible()))
        self.sidebar.select(0)
        self._on_page_changed(0)
        for index in range(len(self.nav_buttons)):  # Ctrl/Cmd+1..4 jumps between tools
            shortcut = QShortcut(QKeySequence(f"Ctrl+{index + 1}"), self)
            shortcut.activated.connect(lambda i=index: self.nav_buttons[i].click())
        QShortcut(QKeySequence("Ctrl+J"), self, activated=lambda: self.set_queue_open(not self.rail.isVisible()))
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self._close_overlays)

    # --- content ------------------------------------------------------------------
    def _build_content(self) -> QWidget:
        for _label, _icon_key, page_cls in PAGES:
            page = page_cls()
            page.task_submitted.connect(self.queue.enqueue)
            self.pages.append(page)
            self.stack.addWidget(page)
            self.actions_stack.addWidget(page.action_bar)
        self.queue.task_done.connect(self._on_task_done)

        # The page scrolls only if the window is too small for it.
        self.stack.setMaximumWidth(CONTENT_MAX_WIDTH)
        holder = QWidget()
        self._holder_layout = QHBoxLayout(holder)
        self._holder_layout.setContentsMargins(26, 26, 22, 12)
        self._holder_layout.addStretch(1)
        self._holder_layout.addWidget(self.stack, 100)
        self._holder_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(holder)
        scroll.viewport().setAutoFillBackground(False)
        holder.setAutoFillBackground(False)
        self.content_scroll = scroll

        # Fixed action bar: same position on every page.
        self.actions_stack.setMaximumWidth(CONTENT_MAX_WIDTH)
        footer = QWidget()
        self._footer_layout = QHBoxLayout(footer)
        self._footer_layout.setContentsMargins(26, 6, 22, 16)
        self._footer_layout.addStretch(1)
        self._footer_layout.addWidget(self.actions_stack, 100)
        self._footer_layout.addStretch(1)

        column = QWidget()
        box = QVBoxLayout(column)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        box.addWidget(scroll, 1)
        box.addWidget(footer)
        return column

    def _on_page_changed(self, index: int) -> None:
        self.actions_stack.setCurrentIndex(index)
        # A stacked widget is as tall as its tallest page; only the visible page should count,
        # so short pages never scroll because of a long one.
        for i in range(self.stack.count()):  # width and height: hidden pages never stretch the window
            policy = QSizePolicy.Policy.Preferred if i == index else QSizePolicy.Policy.Ignored
            self.stack.widget(i).setSizePolicy(policy, policy)
        self.stack.adjustSize()
        for widget in (self.stack.widget(index), self.actions_stack.widget(index)):
            if widget is not None and self.isVisible():
                fade_in(widget, 220)
        self.content_scroll.verticalScrollBar().setValue(0)

    # --- appearance ---------------------------------------------------------------
    def _on_appearance(self, key: str) -> None:
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
        fade.setDuration(340)
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.Type.InOutQuad)
        fade.finished.connect(snapshot.deleteLater)
        fade.start()

    # --- responsive layout ---------------------------------------------------------
    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().resizeEvent(event)
        self.sidebar.set_compact(self.width() < COMPACT_SIDEBAR_WIDTH, animate=self.isVisible())
        side = 26 if self.width() >= COMPACT_SIDEBAR_WIDTH else 16
        self._holder_layout.setContentsMargins(side, 26, side - 4, 12)
        self._footer_layout.setContentsMargins(side, 6, side - 4, 16)
        QTimer.singleShot(0, self._place_overlays)

    def showEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().showEvent(event)
        self.sidebar.set_compact(self.width() < COMPACT_SIDEBAR_WIDTH, animate=False)
        QTimer.singleShot(0, self._place_overlays)

    def _place_overlays(self) -> None:
        area = QRect(self.content_scroll.mapTo(self._central, self.content_scroll.rect().topLeft()),
                     self.content_scroll.size())
        self.pill.place(area)  # just above the action bar
        self.toasts.place(area)
        self.scrim.setGeometry(self._central.rect())
        if self.rail.isVisible():
            self.rail.setGeometry(self._popup_rect(QUEUE_WIDTH, self._central.height() - 20))
        if self.perf.isVisible():
            self.perf.setGeometry(self._popup_rect(PerformancePanel.WIDTH, self._perf_height()))

    # --- popups: queue and performance -----------------------------------------------------
    def _popup_rect(self, width: int, height: int) -> QRect:
        """Beside the sidebar, bottom-aligned (both popup buttons sit at the bottom of the sidebar)."""
        x = self.sidebar.geometry().right() + 6
        return QRect(x, self._central.height() - height - 10, width, height)

    def _perf_height(self) -> int:
        # Wrapped text (longer in English) needs its height measured at the popup's real width.
        layout = self.perf.layout()
        layout.activate()
        needed = layout.totalHeightForWidth(PerformancePanel.WIDTH) if layout.hasHeightForWidth()             else self.perf.sizeHint().height()
        return min(max(needed, self.perf.sizeHint().height()), self._central.height() - 20)

    def _open_popup(self, panel: QWidget, rect: QRect) -> None:
        self.scrim.setGeometry(self._central.rect())
        self.scrim.setGraphicsEffect(None)
        self.scrim.show()
        self.scrim.raise_()
        panel.setGeometry(rect.translated(-40, 0))
        panel.show()
        panel.raise_()
        slide = QPropertyAnimation(panel, b"pos", panel)
        slide.setDuration(380)
        slide.setStartValue(rect.topLeft() - QPoint(40, 0))
        slide.setEndValue(rect.topLeft())
        slide.setEasingCurve(QEasingCurve.Type.OutBack)  # a small spring, like macOS popovers
        slide.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        fade_in(panel, 240)
        self.toasts.raise_all()

    def set_queue_open(self, open_: bool) -> None:
        if open_ == self.rail.isVisible():
            return
        if open_:
            self.perf.hide()
            self._open_popup(self.rail, self._popup_rect(QUEUE_WIDTH, self._central.height() - 20))
        else:
            self.rail.hide()
            if not self.perf.isVisible():
                self.scrim.hide()

    def set_perf_open(self, open_: bool) -> None:
        if open_ == self.perf.isVisible():
            return
        if open_:
            self.rail.hide()
            self._open_popup(self.perf, self._popup_rect(PerformancePanel.WIDTH, self._perf_height()))
        else:
            self.perf.hide()
            if not self.rail.isVisible():
                self.scrim.hide()

    def _on_language(self, lang: str) -> None:
        """Cross-fade like the theme switch, then every text in the app changes language."""
        central = self.centralWidget()
        snapshot = QLabel(central)
        snapshot.setPixmap(central.grab())
        snapshot.setGeometry(central.rect())
        snapshot.show()
        snapshot.raise_()
        i18n.set_language(lang)
        for page in self.pages:  # summaries rebuilt from current choices
            refresh = getattr(page, "_refresh", None)
            if callable(refresh):
                refresh()
        effect = QGraphicsOpacityEffect(snapshot)
        snapshot.setGraphicsEffect(effect)
        fade = QPropertyAnimation(effect, b"opacity", snapshot)
        fade.setDuration(520)
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.Type.InOutCubic)  # a slow, soft dissolve between languages
        fade.finished.connect(snapshot.deleteLater)
        fade.start()
        QTimer.singleShot(0, self._resize_perf_popup)

    def _resize_perf_popup(self) -> None:
        """Glide the open performance popup to the height its content needs (bottom stays put)."""
        if not self.perf.isVisible():
            return
        target = self._popup_rect(PerformancePanel.WIDTH, self._perf_height())
        grow = QPropertyAnimation(self.perf, b"geometry", self.perf)
        grow.setDuration(220)
        grow.setStartValue(self.perf.geometry())
        grow.setEndValue(target)
        grow.setEasingCurve(QEasingCurve.Type.OutCubic)
        grow.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def _close_overlays(self) -> None:
        self.set_perf_open(False)
        self.set_queue_open(False)

    def _keep_popups_on_top(self) -> None:
        if self.rail.isVisible() or self.perf.isVisible():
            self.scrim.raise_()
            self.rail.raise_()
            self.perf.raise_()
            self.toasts.raise_all()

    def _update_queue_badge(self) -> None:
        self.sidebar.set_queue_count(len(self.queue_panel.unfinished_tasks()))

    # --- hardware ------------------------------------------------------------------------
    def _configure_queue(self) -> None:
        self.queue.configure(
            engines=lambda: [e for e in hwaccel.report().engines if not e.is_gpu] + self.perf.chosen_gpus(),
            limit=lambda: 0,  # always as many jobs at once as the hardware allows
            use_gpu=self.perf.use_gpu,
        )

    def _on_hardware(self, report: hwaccel.HardwareReport) -> None:
        self.perf.set_report(report)
        self._configure_queue()  # waiting jobs may now start on a GPU
        if self.perf.isVisible():
            self.perf.setGeometry(self._popup_rect(PerformancePanel.WIDTH, self._perf_height()))

    # --- notifications --------------------------------------------------------------
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

    # --- shutdown -------------------------------------------------------------------
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
        return pages_idle and not self._probe.isRunning() and not self.queue.is_busy()
