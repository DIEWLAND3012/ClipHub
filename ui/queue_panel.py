"""Task queue rail: job cards with progress, cancel and completion alerts (owner: member 1)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.base_task import BaseMediaTask
from core.task_queue import TaskQueue
from ui import theme
from ui.theme import repolish
from ui.widgets import ElidedLabel, label

STATUS_TEXT = {
    "pending": "รอในคิว",
    "running": "กำลังประมวลผล",
    "done": "เสร็จแล้ว",
    "failed": "ผิดพลาด",
    "cancelled": "ยกเลิกแล้ว",
}
FINISHED_STATES = {"done", "failed", "cancelled"}


class JobCard(QFrame):
    """One row of the queue: icon, title, summary, progress bar, status and cancel button."""

    def __init__(self, task: BaseMediaTask, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("JobCard")
        self.task = task
        self.state = ""
        self.cancelling = False
        self.kind = getattr(task, "kind", "generic")
        kind = self.kind

        self.icon_label = QLabel()
        self.icon_label.setObjectName("JobIcon")
        self.icon_label.setProperty("kind", kind)
        self.icon_label.setFixedSize(34, 34)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        name = getattr(task, "display_name", "") or task.title
        self.title_label = ElidedLabel(name)
        self.title_label.setObjectName("JobTitle")
        self.summary_label = ElidedLabel(task.summary())
        self.summary_label.setObjectName("JobSummary")

        self.cancel_button = QPushButton()
        self.cancel_button.setObjectName("JobCancel")
        self.cancel_button.setFixedSize(26, 26)
        self.cancel_button.setToolTip("ยกเลิกงาน")
        self.cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setProperty("kind", kind)

        self.status_label = ElidedLabel()
        self.status_label.setObjectName("JobStatus")
        self.percent_label = QLabel("0%")
        self.percent_label.setObjectName("JobPercent")

        meta = QVBoxLayout()
        meta.setSpacing(1)
        meta.addWidget(self.title_label)
        meta.addWidget(self.summary_label)
        top = QHBoxLayout()
        top.setSpacing(11)
        top.addWidget(self.icon_label)
        top.addLayout(meta, 1)
        top.addWidget(self.cancel_button, 0, Qt.AlignmentFlag.AlignTop)
        bottom = QHBoxLayout()
        bottom.addWidget(self.status_label, 1)
        bottom.addWidget(self.percent_label)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(13, 13, 13, 11)
        layout.setSpacing(10)
        layout.addLayout(top)
        layout.addWidget(self.progress)
        layout.addLayout(bottom)
        self.set_state("pending")
        theme.on_change(self._refresh_icons)

    def _refresh_icons(self) -> None:
        self.cancel_button.setIcon(theme.themed_icon("close", "text2", 12, 2.2))
        if self.state == "done":
            pixmap = theme.icon_pixmap("check", theme.hex_color("ok"), 18, 2.4)
        elif self.state == "failed":
            pixmap = theme.icon_pixmap("close", theme.hex_color("err"), 16, 2.4)
        else:
            pixmap = theme.icon_pixmap(self.kind, theme.kind_color(self.kind).name(), 18)
        self.icon_label.setPixmap(pixmap)

    def is_finished(self) -> bool:
        return self.state in FINISHED_STATES

    def set_state(self, state: str, detail: str = "") -> None:
        changed = state != self.state
        self.state = state
        text = STATUS_TEXT[state]
        self.status_label.set_full_text(f"{text} · {detail}" if detail else text)
        if not changed:
            return
        self.setProperty("state", state)
        self.icon_label.setProperty("state", state)
        self.cancel_button.setVisible(state not in FINISHED_STATES)
        if state == "done":
            self.progress.setValue(100)
            self.percent_label.setText("100%")
        self._refresh_icons()
        for widget in (self, self.icon_label, self.progress):
            repolish(widget)

    def set_progress(self, percent: int, message: str = "") -> None:
        if self.is_finished() or self.cancelling:
            return
        self.progress.setValue(percent)
        self.percent_label.setText(f"{percent}%")
        self.set_state("running", message)

    def mark_cancelling(self) -> None:
        self.cancelling = True
        self.cancel_button.setEnabled(False)
        self.status_label.set_full_text("กำลังยกเลิก…")


class QueuePanel(QWidget):
    def __init__(self, queue: TaskQueue, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.queue = queue
        self._cards: dict[BaseMediaTask, JobCard] = {}
        self.setObjectName("Rail")

        self.queue_icon = QLabel()
        title = label("คิวงาน", "RailTitle")
        self.count_badge = label("0 งาน", "CountBadge")
        self.clear_button = QPushButton("ล้าง")
        self.clear_button.setObjectName("RailClear")
        self.clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_button.setToolTip("ล้างงานที่เสร็จแล้วออกจากคิว")
        self.clear_button.clicked.connect(self.clear_finished)
        self.clear_button.hide()
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(self.queue_icon)
        head.addWidget(title)
        head.addWidget(self.count_badge)
        head.addStretch(1)
        head.addWidget(self.clear_button)

        self.empty_label = label("ยังไม่มีงาน", "RailEmpty")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.jobs_layout = QVBoxLayout()
        self.jobs_layout.setContentsMargins(0, 0, 4, 0)
        self.jobs_layout.setSpacing(10)
        self.jobs_layout.addWidget(self.empty_label)
        self.jobs_layout.addStretch(1)
        holder = QWidget()
        holder.setLayout(self.jobs_layout)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(holder)
        scroll.viewport().setAutoFillBackground(False)
        holder.setAutoFillBackground(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 18, 10, 14)
        layout.setSpacing(14)
        layout.addLayout(head)
        layout.addWidget(scroll, 1)

        queue.task_added.connect(self._on_added)
        queue.task_started.connect(self._on_started)
        queue.task_progress.connect(self._on_progress)
        queue.task_done.connect(self._on_done)
        queue.task_failed.connect(self._on_failed)
        queue.task_cancelled.connect(self._on_cancelled)
        theme.on_change(self._refresh_icons)

    def _refresh_icons(self) -> None:
        self.queue_icon.setPixmap(theme.icon_pixmap("queue", theme.hex_color("accent"), 17, 2.0))

    def unfinished_tasks(self) -> list[BaseMediaTask]:
        return [task for task, card in self._cards.items() if not card.is_finished()]

    def card_for(self, task: BaseMediaTask) -> JobCard | None:
        return self._cards.get(task)

    def clear_finished(self) -> None:
        """Remove done, failed and cancelled cards from the rail."""
        for task, card in list(self._cards.items()):
            if card.is_finished():
                del self._cards[task]
                card.hide()
                card.deleteLater()
        self._update_count()

    def _update_count(self) -> None:
        self.count_badge.setText(f"{len(self.unfinished_tasks())} งาน")
        self.clear_button.setVisible(any(card.is_finished() for card in self._cards.values()))
        self.empty_label.setVisible(not self._cards)

    def _on_added(self, task: BaseMediaTask) -> None:
        card = JobCard(task)
        card.cancel_button.clicked.connect(lambda: self.cancel(task))
        self._cards[task] = card
        self._move_to_top(card)  # newest first
        self._update_count()

    def _move_to_top(self, card: JobCard) -> None:
        """Index 0 is the (hidden) empty label, so the top card sits at index 1."""
        self.jobs_layout.removeWidget(card)
        self.jobs_layout.insertWidget(1, card)

    def cancel(self, task: BaseMediaTask) -> None:
        card = self._cards.get(task)
        if card is None or card.is_finished():
            return
        if card.state == "running":
            card.mark_cancelling()  # the worker confirms through task_cancelled
        self.queue.cancel(task)

    def _on_started(self, task: BaseMediaTask) -> None:
        card = self._cards.get(task)
        if card is not None:
            card.set_state("running")
            self._move_to_top(card)  # the job being worked on is always on top

    def _on_progress(self, task: BaseMediaTask, percent: int, message: str) -> None:
        card = self._cards.get(task)
        if card is not None:
            card.set_progress(percent, message)

    def _on_done(self, task: BaseMediaTask, _output: object) -> None:
        card = self._cards.get(task)
        if card is not None:
            card.set_state("done")
        self._update_count()

    def _on_failed(self, task: BaseMediaTask, message: str) -> None:
        card = self._cards.get(task)
        if card is not None:
            card.set_state("failed")
            card.status_label.setToolTip(message)
        self._update_count()

    def _on_cancelled(self, task: BaseMediaTask) -> None:
        card = self._cards.get(task)
        if card is not None:
            card.set_state("cancelled")
        self._update_count()
