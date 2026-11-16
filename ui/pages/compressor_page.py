"""Feature 3 page: compressor UI with live size estimate (owner: member 3)."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QRegularExpression, Qt, QThread, Signal
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QProgressBar, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from core.errors import ClipHubError
from core.ffmpeg import probe_duration
from tasks.compressor import (
    CUSTOM_MODE, PRESET_LABELS, PRESETS, CompressTask, estimate_output_bytes,
    format_size, original_kbps, suggest_output_path,
)
from ui import theme
from ui.widgets import Segmented, card, field_label, page_header, primary_button

VIDEO_FILTER = "วิดีโอ (*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.flv *.wmv *.ts);;ทุกไฟล์ (*)"
DEFAULT_CUSTOM_KBPS = "6000"
PRESET_HINTS = {
    "high": "คุณภาพใกล้เคียงต้นฉบับ ลดขนาดเล็กน้อย",
    "balanced": "สมดุลระหว่างคุณภาพกับขนาด · เหมาะกับการอัปโหลดโซเชียล",
    "small": "ไฟล์เล็กที่สุด ภาพอาจคมชัดน้อยลง",
}
MODE_IDS = {0: "high", 1: "balanced", 2: "small", 3: CUSTOM_MODE}



def _set_prop(widget: QWidget, name: str, value: object) -> None:
    widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def _format_duration(seconds: float) -> str:
    minutes, secs = divmod(int(round(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def _format_kbps(kbps: float) -> str:
    return f"{kbps / 1000:.1f} Mbps" if kbps >= 1000 else f"{kbps:.0f} kbps"


class _ProbeWorker(QThread):
    """Reads size and duration off the GUI thread (ffprobe can take a moment)."""

    probed = Signal(str, int, float)  # path, size in bytes, duration in seconds
    failed = Signal(str, str)         # path, Thai message

    def __init__(self, path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.path = path

    def run(self) -> None:
        try:
            size = os.path.getsize(self.path)
            duration = probe_duration(self.path)
        except ClipHubError as exc:
            self.failed.emit(self.path, str(exc))
        except OSError as exc:
            self.failed.emit(self.path, f"อ่านไฟล์ไม่ได้: {exc}")
        else:
            self.probed.emit(self.path, size, duration)


class _KbpsEdit(QLineEdit):
    activated = Signal()  # focus in: the user is about to type a custom bitrate

    def focusInEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().focusInEvent(event)
        user_reasons = (
            Qt.FocusReason.MouseFocusReason, Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason,
        )
        if event.reason() in user_reasons:  # ignore the initial focus Qt hands out on show
            self.activated.emit()


class CompressorPage(QWidget):
    task_submitted = Signal(object)  # CompressTask

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._path: str | None = None
        self._size = 0
        self._duration = 0.0
        self._probes: set[_ProbeWorker] = set()
        self._reserved: set[str] = set()  # outputs already queued, so names stay unique
        self._last_dir = str(Path.home())
        self._build()
        self._refresh()
        theme.on_change(self._refresh_icons)

    def _refresh_icons(self) -> None:
        self._thumb.setPixmap(theme.icon_pixmap("film", theme.hex_color("text2"), 22, 1.6))
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._wait_for_probes)

    # --- layout ---------------------------------------------------------------

    def _build(self) -> None:
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(16)

        col.addLayout(self._build_head())
        col.addWidget(self._build_file_card())
        col.addWidget(self._build_mode_card())
        col.addWidget(self._build_estimate_card())
        col.addLayout(self._build_footer())
        col.addStretch(1)

    def _build_head(self) -> QHBoxLayout:
        return page_header("บีบอัด")

    def _card(self):
        return card(spacing=10)

    def _field_label(self, text: str) -> QLabel:
        return field_label(text)

    def _build_file_card(self) -> QFrame:
        card, layout = self._card()
        row = QHBoxLayout()
        row.setSpacing(14)
        thumb = QLabel()
        thumb.setObjectName("Thumb")
        thumb.setFixedSize(56, 44)
        self._thumb = thumb
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        meta = QVBoxLayout()
        meta.setSpacing(2)
        self.file_name = QLabel("ยังไม่ได้เลือกไฟล์")
        self.file_name.setObjectName("FileName")
        self.file_meta = QLabel("")
        self.file_meta.setObjectName("FileMeta")
        for label in (self.file_name, self.file_meta):
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            meta.addWidget(label)
        self.file_meta.hide()  # shown once a file is chosen
        self.file_size = QLabel("")
        self.file_size.setObjectName("FileSize")
        self.choose_btn = QPushButton("เลือกไฟล์…")
        self.choose_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.choose_btn.clicked.connect(self._choose_file)
        row.addWidget(thumb)
        row.addLayout(meta, 1)
        row.addWidget(self.file_size)
        row.addWidget(self.choose_btn)
        layout.addLayout(row)
        return card

    def _build_mode_card(self) -> QFrame:
        card, layout = self._card()
        layout.addWidget(self._field_label("คุณภาพ"))

        bar = Segmented()
        self.mode_group = bar.group
        for button_id, mode in MODE_IDS.items():
            if mode == CUSTOM_MODE:
                button = QPushButton(bar)
                button.setCheckable(True)
                button.hide()  # stands in for "no preset"; checked while the kbps field is in use
                self.mode_group.addButton(button, button_id)
            else:
                bar.add(f"{PRESET_LABELS[mode]}  −{round(PRESETS[mode] * 100)}%", button_id)
        self.mode_group.button(1).setChecked(True)
        self.mode_group.idToggled.connect(self._on_mode_toggled)
        layout.addWidget(bar)

        layout.addSpacing(6)
        layout.addWidget(self._field_label("หรือกำหนดเอง"))
        self.control = QFrame()
        self.control.setObjectName("Control")
        self.control.setFixedHeight(theme.CONTROL_HEIGHT)
        control_row = QHBoxLayout(self.control)
        control_row.setContentsMargins(16, 0, 14, 0)
        self.kbps_edit = _KbpsEdit(DEFAULT_CUSTOM_KBPS)
        self.kbps_edit.setObjectName("KbpsEdit")
        self.kbps_edit.setValidator(QRegularExpressionValidator(QRegularExpression(r"[0-9]{0,6}")))
        self.kbps_edit.setAccessibleName("bitrate")
        self.kbps_edit.activated.connect(self._use_custom)
        self.kbps_edit.textEdited.connect(lambda _text: self._use_custom())
        self.kbps_edit.textChanged.connect(lambda _text: self._refresh())
        unit = QLabel("kbps")
        unit.setObjectName("Unit")
        control_row.addWidget(self.kbps_edit, 1)
        control_row.addWidget(unit)
        layout.addWidget(self.control)
        note = QLabel("นับรวมเสียง AAC · ใช้แทนพรีเซ็ตเมื่อคลิกหรือพิมพ์ในช่องนี้")
        note.setObjectName("Note")
        return card

    def _build_estimate_card(self) -> QFrame:
        card, layout = self._card()
        layout.addWidget(self._field_label("ขนาดโดยประมาณ"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(16)
        grid.setColumnMinimumWidth(0, 88)
        grid.setColumnMinimumWidth(2, 84)
        self.bar_orig, self.amount_orig = self._bar_row(grid, 0, "ต้นฉบับ", "BarOrig", False)
        self.bar_new, self.amount_new = self._bar_row(grid, 1, "หลังบีบอัด", "BarNew", True)
        layout.addSpacing(6)
        layout.addLayout(grid)
        layout.addSpacing(6)

        self.saving = QFrame()
        self.saving.setObjectName("Saving")
        saving_row = QHBoxLayout(self.saving)
        saving_row.setContentsMargins(16, 16, 16, 16)
        saving_row.setSpacing(14)
        self.saving_big = QLabel("—")
        self.saving_big.setObjectName("SavingBig")
        text = QVBoxLayout()
        text.setSpacing(2)
        self.saving_title = QLabel()
        self.saving_title.setObjectName("SavingTitle")
        self.saving_sub = QLabel()
        self.saving_sub.setObjectName("SavingSub")
        self.saving_sub.setWordWrap(True)
        self.saving_sub.hide()
        text.addWidget(self.saving_title)
        text.addWidget(self.saving_sub)
        saving_row.addWidget(self.saving_big)
        saving_row.addLayout(text, 1)
        layout.addWidget(self.saving)
        return card

    def _bar_row(
        self, grid: QGridLayout, row: int, text: str, bar_name: str, accent: bool
    ) -> tuple[QProgressBar, QLabel]:
        label = QLabel(text)
        label.setObjectName("BarLabel")
        bar = QProgressBar()
        bar.setObjectName(bar_name)
        bar.setRange(0, 1000)
        bar.setTextVisible(False)
        amount = QLabel("—")
        amount.setObjectName("BarAmount")
        amount.setProperty("accent", accent)
        amount.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(label, row, 0)
        grid.addWidget(bar, row, 1)
        grid.addWidget(amount, row, 2)
        return bar, amount

    def _build_footer(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(18)
        self.start_btn = primary_button("เริ่มบีบอัด", "compress")
        self.start_btn.clicked.connect(self._submit)
        self.foot_hint = QLabel()
        self.foot_hint.setObjectName("Hint")
        row.addWidget(self.start_btn)
        row.addWidget(self.foot_hint, 1)
        return row

    # --- state ----------------------------------------------------------------

    def _mode(self) -> str:
        return MODE_IDS.get(self.mode_group.checkedId(), "balanced")

    def _custom_kbps(self) -> int | None:
        text = self.kbps_edit.text()
        return int(text) if text.isdigit() and int(text) > 0 else None

    def _use_custom(self) -> None:
        if self._mode() != CUSTOM_MODE:
            self.mode_group.button(3).setChecked(True)

    def _on_mode_toggled(self, _button_id: int, checked: bool) -> None:
        if checked:
            self._refresh()

    def _refresh(self) -> None:
        """Recompute the estimate, saving card and button state from the current inputs."""
        mode = self._mode()
        custom = mode == CUSTOM_MODE
        kbps = self._custom_kbps()
        _set_prop(self.control, "active", custom)

        ready = self._size > 0 and self._duration > 0
        usable = ready and (not custom or kbps is not None)
        self.start_btn.setEnabled(usable)
        self.bar_orig.setValue(1000 if ready else 0)
        self.amount_orig.setText(format_size(self._size) if ready else "—")

        if not usable:
            self.bar_new.setValue(0)
            self.amount_new.setText("—")
            self._set_saving("idle", "—", "เลือกไฟล์เพื่อดูขนาด",
                             "เลือกไฟล์วิดีโอ" if not ready else "กรอก Target Bitrate เป็นตัวเลขมากกว่า 0")
            self.foot_hint.setText("")
            return

        estimate = estimate_output_bytes(self._size, self._duration, mode, kbps)
        saved = self._size - estimate
        percent = round(abs(saved) / self._size * 100)
        self.bar_new.setValue(min(1000, round(estimate / self._size * 1000)))
        self.amount_new.setText(format_size(estimate))
        if saved >= 0:
            sub = PRESET_HINTS.get(mode, "ใช้ Target Bitrate ที่กำหนดเอง")
            self._set_saving("ok", f"−{percent}%", f"ประหยัดพื้นที่ {format_size(saved)}", sub)
        else:
            self._set_saving("warn", f"+{percent}%", f"ไฟล์อาจใหญ่ขึ้น {format_size(-saved)}",
                             "Bitrate เป้าหมายสูงกว่าไฟล์ต้นฉบับ ลองลดค่าลง")
        self.foot_hint.setText("")

    def _set_saving(self, state: str, big: str, title: str, sub: str) -> None:
        _set_prop(self.saving, "state", state)
        _set_prop(self.saving_big, "state", state)
        self.saving_big.setText(big)
        self.saving_title.setText(title)
        self.saving_sub.setText(sub)

    # --- file selection and probing -------------------------------------------

    def _choose_file(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "เลือกไฟล์วิดีโอ", self._last_dir, VIDEO_FILTER)
        if path:
            self.set_input_file(path)

    def set_input_file(self, path: str) -> None:
        """Select a source file and start reading its size and duration in the background."""
        self._path = path
        self._last_dir = os.path.dirname(path) or self._last_dir
        self._size = 0
        self._duration = 0.0
        self.file_name.setText(os.path.basename(path))
        self.file_name.setToolTip(path)
        self.file_meta.show()
        self.file_meta.setText("กำลังอ่านข้อมูลไฟล์…")
        _set_prop(self.file_meta, "error", False)
        self.file_size.setText("…")
        self._refresh()

        worker = _ProbeWorker(path, self)
        worker.probed.connect(self._on_probed)
        worker.failed.connect(self._on_probe_failed)
        worker.finished.connect(self._on_probe_finished)
        self._probes.add(worker)
        worker.start()

    def _on_probed(self, path: str, size: int, duration: float) -> None:
        if path != self._path:
            return  # the user already picked another file
        self._size = size
        self._duration = duration
        bitrate = _format_kbps(original_kbps(size, duration))
        self.file_meta.setText(f"{_format_duration(duration)} · {bitrate}")
        self.file_size.setText(format_size(size))
        self._refresh()

    def _on_probe_failed(self, path: str, message: str) -> None:
        if path != self._path:
            return
        self.file_meta.setText(message)
        _set_prop(self.file_meta, "error", True)
        self.file_size.setText("—")
        self._refresh()

    def _on_probe_finished(self) -> None:
        worker = self.sender()
        if isinstance(worker, _ProbeWorker):
            self._probes.discard(worker)
            worker.deleteLater()

    def _wait_for_probes(self) -> None:
        for worker in list(self._probes):
            worker.wait(3000)

    # --- submit ---------------------------------------------------------------

    def _submit(self) -> None:
        if self._path is None or not self.start_btn.isEnabled():
            return
        mode = self._mode()
        output = suggest_output_path(self._path, self._reserved)
        self._reserved.add(str(output))
        task = CompressTask(
            self._path, str(output), mode=mode,
            target_kbps=self._custom_kbps() if mode == CUSTOM_MODE else None,
        )
        self.task_submitted.emit(task)
        self.foot_hint.setText(f"เพิ่มลงคิวแล้ว → {output.name}")

