"""Performance panel: one switch to use the graphics card, and which card when there are several.

Jobs always run in parallel where the hardware allows; there is nothing else to set.
"""
from __future__ import annotations

from PySide6.QtCore import QSettings, Qt, QThread, Signal
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QListView, QPushButton, QVBoxLayout, QWidget

from core import hwaccel
from core.hwaccel import Engine, HardwareReport
from ui import theme
from ui.widgets import GlassCard, label, reveal

ALL_GPUS = "all"


class HardwareProbe(QThread):
    """Runs hwaccel.refresh() (a few seconds of test encodes) off the GUI thread."""

    done = Signal(object)  # HardwareReport

    def run(self) -> None:
        try:
            report = hwaccel.refresh()
        except Exception:  # detection must never break the app; the CPU always works
            report = HardwareReport(ready=True)
        self.done.emit(report)


class PerformancePanel(GlassCard):
    settings_changed = Signal()
    close_requested = Signal()

    WIDTH = 360

    def __init__(self, parent: QWidget) -> None:
        super().__init__("drawer", radius=22, parent=parent)
        self.setObjectName("PerformancePanel")
        self.setFixedWidth(self.WIDTH)
        self._settings = QSettings("ClipHUB", "ClipHUB")
        self._report = hwaccel.report()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 16, 20)
        layout.setSpacing(12)

        head = QHBoxLayout()
        head.addWidget(label("ประสิทธิภาพ", "RailTitle"))
        head.addStretch(1)
        close = QPushButton()
        close.setObjectName("JobCancel")
        close.setFixedSize(26, 26)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setToolTip("ปิด (Esc)")
        close.clicked.connect(self.close_requested)
        theme.on_change(lambda: close.setIcon(theme.themed_icon("close", "text2", 12, 2.2)))
        head.addWidget(close)
        layout.addLayout(head)

        switch_row = QHBoxLayout()
        texts = QVBoxLayout()
        texts.setSpacing(1)
        texts.addWidget(label("ใช้การ์ดจอช่วยประมวลผล", "ToggleTitle"))
        texts.addWidget(label("แปลงและบีบอัดวิดีโอเร็วขึ้น ใช้ CPU น้อยลง", "Note"))
        from ui.pages.converter_page import SwitchCheckBox  # the Apple-style toggle
        self.gpu_switch = SwitchCheckBox("ใช้การ์ดจอช่วยประมวลผล")
        self.gpu_switch.setChecked(self._settings.value("perf/use_gpu", True, type=bool))
        self.gpu_switch.toggled.connect(self._on_switch)
        switch_row.addLayout(texts, 1)
        switch_row.addWidget(self.gpu_switch, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(switch_row)

        self.gpu_box = QWidget()
        box = QVBoxLayout(self.gpu_box)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(6)
        box.addWidget(label("การ์ดจอที่ใช้", "FieldLabel"))
        self.gpu_combo = QComboBox()
        self.gpu_combo.setView(QListView())
        self.gpu_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.gpu_combo.activated.connect(lambda _i: self._save())
        box.addWidget(self.gpu_combo)
        self.note = label("", "Note", wrap=True)
        box.addWidget(self.note)
        layout.addWidget(self.gpu_box)
        self.gpu_box.setVisible(self.gpu_switch.isChecked())
        self.set_report(self._report)
        self.hide()

    # --- settings ---------------------------------------------------------------------
    def use_gpu(self) -> bool:
        return self.gpu_switch.isChecked() and self.gpu_switch.isEnabled()

    def chosen_gpus(self) -> list[Engine]:
        """The GPU engines jobs may use: the selected card, or all of them."""
        if not self.use_gpu():
            return []
        gpus = self._report.gpu_engines()
        choice = self.gpu_combo.currentData()
        if choice in (None, ALL_GPUS):
            return gpus
        return [e for e in gpus if e.key == choice] or gpus

    def _on_switch(self, on: bool) -> None:
        if on:
            reveal(self.gpu_box, 260)
        else:
            self.gpu_box.hide()
        self.adjustSize()
        self._save()

    def _save(self) -> None:
        self._settings.setValue("perf/use_gpu", self.gpu_switch.isChecked())
        choice = self.gpu_combo.currentData()
        if choice:
            self._settings.setValue("perf/gpu", choice)
        self.settings_changed.emit()

    # --- the card list ----------------------------------------------------------------
    def set_report(self, report: HardwareReport) -> None:
        self._report = report
        model = QStandardItemModel(self.gpu_combo)
        gpus = report.gpu_engines()
        if not report.ready:
            model.appendRow(QStandardItem("กำลังตรวจสอบการ์ดจอ…"))
        if len(gpus) > 1:
            item = QStandardItem(f"ทุกการ์ด ({len(gpus)} ใบ กระจายงาน)")
            item.setData(ALL_GPUS, Qt.ItemDataRole.UserRole)
            model.appendRow(item)
        for engine in gpus:
            item = QStandardItem(engine.label)
            item.setData(engine.key, Qt.ItemDataRole.UserRole)
            model.appendRow(item)
        for problem in report.problems:  # cards that exist but cannot be used yet
            name = problem.split(":")[0]
            item = QStandardItem(f"{name} — ยังใช้ไม่ได้")
            item.setEnabled(False)
            model.appendRow(item)
        if report.ready and not gpus:
            model.appendRow(QStandardItem("ไม่พบการ์ดจอที่ใช้ได้"))
        self.gpu_combo.setModel(model)
        saved = str(self._settings.value("perf/gpu", ALL_GPUS))
        index = self.gpu_combo.findData(saved)
        self.gpu_combo.setCurrentIndex(index if index >= 0 else 0)
        # One usable card (or none): nothing to choose, so the choice is locked.
        self.gpu_combo.setEnabled(len(gpus) > 1)
        self.gpu_switch.setEnabled(bool(gpus) or not report.ready)
        notes = list(report.problems)
        if report.whisper_gpu:
            notes.append("ซับไตเติล AI ใช้ GPU (CUDA)")
        self.note.setText("\n".join(notes))
        self.note.setVisible(bool(notes))
        self.adjustSize()
