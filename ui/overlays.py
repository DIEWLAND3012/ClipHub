"""Floating glass overlays: the bottom progress pill for the running job and toast notifications."""
from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve, QParallelAnimationGroup, QPoint, QPropertyAnimation, QRect, QTimer, QUrl, Qt,
)
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from core.base_task import BaseMediaTask
from core.task_queue import TaskQueue
from ui import theme
from ui.theme import repolish
from ui.widgets import ElidedLabel, GlassCard, label

Action = tuple[str, Callable[[], None]]


def reveal_in_folder(path: str | Path) -> None:
    """Open the file's folder with the file selected (Explorer / Finder)."""
    target = Path(path)
    try:
        if sys.platform == "win32" and target.exists():
            subprocess.Popen(["explorer", "/select,", str(target)])
            return
        if sys.platform == "darwin" and target.exists():
            subprocess.Popen(["open", "-R", str(target)])
            return
    except OSError:
        pass
    folder = target if target.is_dir() else target.parent
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


def _slide(widget: QWidget, start: QPoint, end: QPoint, fade_in: bool, duration: int = 260,
           done: Callable[[], None] | None = None) -> QParallelAnimationGroup:
    effect = widget.graphicsEffect()
    if not isinstance(effect, QGraphicsOpacityEffect):
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
    group = QParallelAnimationGroup(widget)
    move = QPropertyAnimation(widget, b"pos", group)
    move.setDuration(duration)
    move.setStartValue(start)
    move.setEndValue(end)
    move.setEasingCurve(QEasingCurve.Type.OutCubic if fade_in else QEasingCurve.Type.InCubic)
    fade = QPropertyAnimation(effect, b"opacity", group)
    fade.setDuration(duration)
    fade.setStartValue(0.0 if fade_in else 1.0)
    fade.setEndValue(1.0 if fade_in else 0.0)
    group.addAnimation(move)
    group.addAnimation(fade)

    def finished() -> None:
        if fade_in:
            widget.setGraphicsEffect(None)  # crisp text once settled
        if done is not None:
            done()

    group.finished.connect(finished)
    group.start(QParallelAnimationGroup.DeletionPolicy.DeleteWhenStopped)
    return group


class ProgressPill(GlassCard):
    """Long capsule at the bottom of the window showing the job that is running right now."""

    HEIGHT = 74

    def __init__(self, queue: TaskQueue, cancel: Callable[[BaseMediaTask], None],
                 pending_count: Callable[[], int], parent: QWidget) -> None:
        super().__init__("pill", radius=26, parent=parent)
        self.setObjectName("ProgressPill")
        self._cancel = cancel
        self._pending_count = pending_count
        self.task: BaseMediaTask | None = None
        self._shown = False
        self._anchor = QRect()

        self.icon = QLabel()
        self.icon.setObjectName("JobIcon")
        self.icon.setFixedSize(38, 38)
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title = ElidedLabel()
        self.title.setObjectName("PillTitle")
        self.status = ElidedLabel()
        self.status.setObjectName("PillStatus")
        texts = QVBoxLayout()
        texts.setSpacing(0)
        texts.addWidget(self.title)
        texts.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.setObjectName("PillBar")
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.percent = QLabel("0%")
        self.percent.setObjectName("PillPercent")
        self.percent.setMinimumWidth(52)
        self.percent.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.more = label("", "PillMore")
        self.cancel_button = QPushButton()
        self.cancel_button.setObjectName("JobCancel")
        self.cancel_button.setFixedSize(30, 30)
        self.cancel_button.setToolTip("ยกเลิกงานนี้")
        self.cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_button.clicked.connect(self._on_cancel)

        row = QHBoxLayout(self)
        row.setContentsMargins(16, 10, 14, 10)
        row.setSpacing(14)
        row.addWidget(self.icon)
        row.addLayout(texts, 2)
        row.addWidget(self.bar, 3)
        row.addWidget(self.percent)
        row.addWidget(self.more)
        row.addWidget(self.cancel_button)
        self.hide()

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(450)  # the next queued job starts right after the last one ends
        self._hide_timer.timeout.connect(self._maybe_hide)

        queue.task_started.connect(self._on_started)
        queue.task_progress.connect(self._on_progress)
        for signal in (queue.task_done, queue.task_failed, queue.task_cancelled):
            signal.connect(lambda *args: self._on_ended(args[0]))
        queue.task_added.connect(lambda _task: self._refresh_more())
        theme.on_change(self._refresh_icons)

    # --- geometry -----------------------------------------------------------
    def place(self, area: QRect) -> None:
        """Centre along the bottom of `area` (the content column, in parent coordinates)."""
        self._anchor = area
        width = min(area.width() - 32, 680)
        self.resize(width, self.HEIGHT)
        if self._shown:
            self.move(self._home())

    def _home(self) -> QPoint:
        a = self._anchor
        return QPoint(a.x() + (a.width() - self.width()) // 2, a.bottom() - self.height() - 14)

    def _below(self) -> QPoint:
        return self._home() + QPoint(0, self.height() + 30)

    # --- queue events -------------------------------------------------------
    def _on_started(self, task: BaseMediaTask) -> None:
        self._hide_timer.stop()
        self.task = task
        self.title.set_full_text(getattr(task, "display_name", "") or task.title)
        self.status.set_full_text(task.summary() or "กำลังเริ่ม…")
        self.bar.setProperty("kind", getattr(task, "kind", ""))
        self.icon.setProperty("kind", getattr(task, "kind", ""))
        repolish(self.bar)
        repolish(self.icon)
        self._set_percent(0)
        self.cancel_button.setEnabled(True)
        self._refresh_icons()
        self._refresh_more()
        if not self._shown:
            self._shown = True
            self.show()
            self.raise_()
            _slide(self, self._below(), self._home(), fade_in=True)

    def _on_progress(self, task: BaseMediaTask, percent: int, message: str) -> None:
        if task is not self.task:
            return
        self._set_percent(percent)
        if message:
            self.status.set_full_text(message)

    def _on_ended(self, task: BaseMediaTask) -> None:
        if task is self.task:
            self._refresh_more()
            self._hide_timer.start()

    def _maybe_hide(self) -> None:
        if not self._shown or (self.task is not None and self.task.status.value == "running"):
            return
        self._shown = False
        self.task = None
        _slide(self, self._home(), self._below(), fade_in=False, duration=220, done=self.hide)

    def _set_percent(self, percent: int) -> None:
        self.bar.setValue(percent)
        self.percent.setText(f"{percent}%")

    def _refresh_more(self) -> None:
        waiting = max(self._pending_count() - (1 if self.task is not None else 0), 0)
        self.more.setText(f"+{waiting} รอคิว" if waiting else "")
        self.more.setVisible(bool(waiting))

    def _on_cancel(self) -> None:
        if self.task is not None:
            self.cancel_button.setEnabled(False)
            self.status.set_full_text("กำลังยกเลิก…")
            self._cancel(self.task)

    def _refresh_icons(self) -> None:
        self.cancel_button.setIcon(theme.themed_icon("close", "text2", 13, 2.2))
        kind = getattr(self.task, "kind", "queue") if self.task is not None else "queue"
        self.icon.setPixmap(theme.icon_pixmap(kind, theme.kind_color(kind).name(), 20))


class Toast(GlassCard):
    """One notification card: coloured icon, title, message, optional actions and a close button."""

    WIDTH = 420
    ICONS = {"success": ("check", "ok"), "error": ("close", "err"), "info": ("sparkles", "accent")}

    def __init__(self, kind: str, title: str, body: str, actions: list[Action], host: ToastHost) -> None:
        super().__init__("toast", radius=20, parent=host.parent_widget)
        self.setObjectName("Toast")
        self.kind = kind
        self.host = host
        self.setFixedWidth(self.WIDTH)

        self.badge = QLabel()
        self.badge.setObjectName("ToastBadge")
        self.badge.setProperty("kind", kind)
        self.badge.setFixedSize(34, 34)
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label = label(title, "ToastTitle")
        body_label = label(body, "ToastBody", wrap=True)
        body_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body_label.setVisible(bool(body))
        close = QPushButton()
        close.setObjectName("ToastClose")
        close.setFixedSize(24, 24)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.clicked.connect(self.dismiss)
        self._close = close

        texts = QVBoxLayout()
        texts.setSpacing(3)
        texts.addWidget(title_label)
        texts.addWidget(body_label)
        if actions:
            buttons = QHBoxLayout()
            buttons.setContentsMargins(0, 6, 0, 0)
            buttons.setSpacing(8)
            for text, callback in actions:
                button = QPushButton(text)
                button.setObjectName("ToastAction")
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                button.clicked.connect(lambda _=False, cb=callback: (cb(), self.dismiss()))
                buttons.addWidget(button)
            buttons.addStretch(1)
            texts.addLayout(buttons)
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 14, 12, 14)
        row.setSpacing(12)
        row.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignTop)
        row.addLayout(texts, 1)
        row.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
        theme.on_change(self._refresh_icons)
        # Wrapped text needs its final width before the height is known.
        left, top, right, bottom = self.SHADOW[self.level]
        self.setFixedHeight(row.totalHeightForWidth(self.WIDTH - left - right) + top + bottom)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        if kind != "error":  # errors stay until the user has read them
            self._timer.start(6000)

    def _refresh_icons(self) -> None:
        icon, role = self.ICONS.get(self.kind, self.ICONS["info"])
        self.badge.setPixmap(theme.icon_pixmap(icon, theme.hex_color(role), 18, 2.4))
        self._close.setIcon(theme.themed_icon("close", "text2", 11, 2.2))

    def enterEvent(self, event) -> None:  # noqa: N802 (Qt API) - keep it while the pointer is on it
        self._timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if self.kind != "error":
            self._timer.start(3000)
        super().leaveEvent(event)

    def dismiss(self) -> None:
        self.host.remove(self)


class ToastHost:
    """Stacks toasts at the top centre of the content column, newest on top."""

    MAX = 4
    GAP = 10

    def __init__(self, parent_widget: QWidget) -> None:
        self.parent_widget = parent_widget
        self.enabled = True  # scripted runs switch this off
        self._toasts: list[Toast] = []
        self._anchor = QRect()

    def place(self, area: QRect) -> None:
        self._anchor = area
        self._layout(animate=False)

    def show(self, kind: str, title: str, body: str = "", actions: list[Action] | None = None) -> Toast | None:
        if not self.enabled:
            return None
        toast = Toast(kind, title, body, actions or [], self)
        self._toasts.insert(0, toast)
        while len(self._toasts) > self.MAX:
            self.remove(self._toasts[-1])
        toast.show()
        toast.raise_()
        target = self._positions()[0]
        _slide(toast, target - QPoint(0, 26), target, fade_in=True, duration=300)
        self._layout(animate=True, skip=toast)
        return toast

    def remove(self, toast: Toast) -> None:
        if toast not in self._toasts:
            return
        self._toasts.remove(toast)
        _slide(toast, toast.pos(), toast.pos() - QPoint(0, 14), fade_in=False, duration=180,
               done=toast.deleteLater)
        self._layout(animate=True)

    def _positions(self) -> list[QPoint]:
        a = self._anchor
        x = a.x() + (a.width() - Toast.WIDTH) // 2
        y = a.y() + 16
        points = []
        for toast in self._toasts:
            points.append(QPoint(x, y))
            y += toast.height() - 6 + self.GAP  # cards overlap their own shadow margin
        return points

    def _layout(self, animate: bool, skip: Toast | None = None) -> None:
        for toast, point in zip(self._toasts, self._positions()):
            if toast is skip:
                continue
            toast.raise_()
            if animate and toast.pos() != point:
                move = QPropertyAnimation(toast, b"pos", toast)
                move.setDuration(220)
                move.setEndValue(point)
                move.setEasingCurve(QEasingCurve.Type.OutCubic)
                move.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
            else:
                toast.move(point)


_host: ToastHost | None = None


def set_host(host: ToastHost | None) -> None:
    global _host
    _host = host


def notify(kind: str, title: str, body: str = "", actions: list[Action] | None = None) -> bool:
    """Show a toast from anywhere. Returns False when no window hosts toasts (callers fall back)."""
    if _host is None:
        return False
    _host.show(kind, title, body, actions)
    return True
