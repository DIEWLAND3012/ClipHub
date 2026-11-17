"""Feature 3 page: compressor UI with live size estimate (owner: member 3).

Flow: drop or pick a video -> its details and the size estimate (left) and the quality and
file name (right) pop in -> start from the action bar.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from PySide6.QtCore import QRegularExpression, Qt, QThread, Signal
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QProgressBar, QPushButton,
    QVBoxLayout, QWidget,
)

from core.errors import ClipHubError
from core.ffmpeg import probe_media
from core.naming import safe_filename, strip_extension, unique_path
from tasks.compressor import (
    CUSTOM_MODE, PRESET_LABELS, PRESETS, CompressTask, estimate_output_bytes, format_size, original_kbps,
    suggest_output_path,
)
from ui import theme
from ui.widgets import (
    ActionBar, Columns, DropZone, ElidedLabel, NameField, PageDrop, Segmented, card, field_label, label,
    page_header, pop_in, primary_button,
)

VIDEO_FILTER = "วิดีโอ (*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.flv *.wmv *.ts);;ทุกไฟล์ (*)"
DEFAULT_CUSTOM_KBPS = "6000"
PRESET_HINTS = {
    "high": "คุณภาพใกล้เคียงต้นฉบับ ลดขนาดเล็กน้อย",
    "balanced": "สมดุลระหว่างคุณภาพกับขนาด · เหมาะกับการอัปโหลดโซเชียล",
    "small": "ไฟล์เล็กที่สุด ภาพอาจคมชัดน้อยลง",
}
MODE_IDS = {0: "high", 1: "balanced", 2: "small", 3: CUSTOM_MODE}
CODEC_NAMES = {"h264": "H.264", "hevc": "H.265 (HEVC)", "av1": "AV1", "vp9": "VP9", "prores": "ProRes",
               "mpeg4": "MPEG-4"}


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
    """Reads size, duration and stream details off the GUI thread (ffprobe can take a moment)."""

    probed = Signal(str, int, float, object)  # path, size in bytes, duration in seconds, probe_media() dict
    failed = Signal(str, str)                 # path, Thai message

    def __init__(self, path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.path = path

    def run(self) -> None:
        try:
            size = os.path.getsize(self.path)
            info = probe_media(self.path)
            if info["video"] is None:
                raise ClipHubError("ไฟล์นี้ไม่มีภาพวิดีโอให้บีบอัด")
            if info["duration"] <= 0:
                raise ClipHubError("ไม่พบความยาวของไฟล์")
        except ClipHubError as exc:
            self.failed.emit(self.path, str(exc))
        except OSError as exc:
            self.failed.emit(self.path, f"อ่านไฟล์ไม่ได้: {exc}")
        else:
            self.probed.emit(self.path, size, info["duration"], info)


class _KbpsEdit(QLineEdit):
    activated = Signal()  # focus in: the user is about to type a custom bitrate

    def focusInEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().focusInEvent(event)
        user_reasons = (
            Qt.FocusReason.MouseFocusReason, Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason,
        )
        if event.reason() in user_reasons:  # ignore the initial focus Qt hands out on show
            self.activated.emit()


def _info_tile(key: str) -> tuple[QFrame, ElidedLabel]:
    tile = QFrame()
    tile.setObjectName("InfoTile")
    box = QVBoxLayout(tile)
    box.setContentsMargins(11, 8, 10, 9)
    box.setSpacing(1)
    box.addWidget(label(key, "InfoKey"))
    value = ElidedLabel()
    value.setObjectName("InfoValue")
    box.addWidget(value)
    return tile, value


class CompressorPage(QWidget):
    task_submitted = Signal(object)  # CompressTask

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._path: str | None = None
        self._size = 0
        self._duration = 0.0
        self._probes: set[_ProbeWorker] = set()
        self._reserved: set[str] = set()  # outputs already queued, so names stay unique
        self._build()
        self._refresh()
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._wait_for_probes)

    # --- layout ---------------------------------------------------------------
    def _build(self) -> None:
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(16)
        col.addLayout(page_header("บีบอัด"))
        self.dropzone = DropZone("ลากไฟล์วิดีโอมาวางที่นี่", VIDEO_FILTER, "เลือกไฟล์วิดีโอ")
        self.dropzone.setMinimumHeight(300)
        self.dropzone.file_chosen.connect(self.set_input_file)
        self.dropzone.rejected.connect(self._set_notice)
        col.addWidget(self.dropzone)
        PageDrop(self, self.set_input_file, self._set_notice)
        self.notice = label("", "ErrorText", wrap=True)
        self.notice.hide()
        col.addWidget(self.notice)

        self.columns = Columns(breakpoint=820, stretch=(5, 6))
        self.file_card = self._build_file_card()
        self.estimate_card = self._build_estimate_card()
        self.mode_card = self._build_mode_card()
        for widget in (self.file_card, self.estimate_card, self.mode_card):
            widget.hide()
        self.columns.left.addWidget(self.file_card)
        self.columns.left.addWidget(self.estimate_card)
        self.columns.left.addStretch(1)
        self.columns.right.addWidget(self.mode_card)
        self.columns.right.addStretch(1)
        col.addWidget(self.columns)
        col.addStretch(1)

        self.action_bar = ActionBar()
        self.start_btn = self.action_bar.add_button(primary_button("เริ่มบีบอัด", "compress"))
        self.start_btn.clicked.connect(self._submit)

    def _build_file_card(self) -> QFrame:
        frame, layout = card(spacing=14, margins=(20, 18, 20, 20))
        top = QHBoxLayout()
        top.setSpacing(16)
        self.format_badge = label("—", "FormatBadge")
        self.format_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.format_badge.setMinimumSize(96, 76)
        texts = QVBoxLayout()
        texts.setSpacing(3)
        self.file_name = ElidedLabel(mode=Qt.TextElideMode.ElideMiddle)
        self.file_name.setObjectName("FileTitle")
        self.file_meta = ElidedLabel()
        self.file_meta.setObjectName("FileMeta")
        texts.addStretch(1)
        texts.addWidget(self.file_name)
        texts.addWidget(self.file_meta)
        texts.addStretch(1)
        change = QPushButton("เปลี่ยนไฟล์")
        change.setCursor(Qt.CursorShape.PointingHandCursor)
        change.clicked.connect(lambda: self.dropzone.browse())
        top.addWidget(self.format_badge)
        top.addLayout(texts, 1)
        top.addWidget(change, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(top)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        self.tiles: dict[str, tuple[QFrame, ElidedLabel]] = {}
        for index, (key, text) in enumerate((("duration", "ความยาว"), ("size", "ขนาด"),
                                             ("bitrate", "บิตเรต"), ("video", "วิดีโอ"))):
            self.tiles[key] = _info_tile(text)
            grid.addWidget(self.tiles[key][0], *divmod(index, 2))
        layout.addLayout(grid)
        return frame

    def _build_mode_card(self) -> QFrame:
        frame, layout = card(spacing=10, margins=(22, 20, 22, 22))
        layout.addWidget(field_label("คุณภาพ"))
        bar = Segmented()
        bar.setFixedHeight(theme.CONTROL_HEIGHT)
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
        self.mode_group.idToggled.connect(lambda _id, checked: checked and self._refresh())
        layout.addWidget(bar)
        self.preset_note = label("", "Note", wrap=True)
        layout.addWidget(self.preset_note)

        layout.addSpacing(8)
        layout.addWidget(field_label("หรือกำหนดบิตเรตเอง"))
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

        layout.addSpacing(8)
        layout.addWidget(field_label("ชื่อไฟล์"))
        self.name_field = NameField()
        self.name_field.set_extension("mp4")
        self.name_field.changed.connect(self._refresh_summary)
        layout.addWidget(self.name_field)
        return frame

    def _build_estimate_card(self) -> QFrame:
        frame, layout = card(spacing=10)
        layout.addWidget(field_label("ขนาดโดยประมาณ"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)
        grid.setColumnMinimumWidth(0, 80)
        grid.setColumnMinimumWidth(2, 76)
        self.bar_orig, self.amount_orig = self._bar_row(grid, 0, "ต้นฉบับ", "BarOrig", False)
        self.bar_new, self.amount_new = self._bar_row(grid, 1, "หลังบีบอัด", "BarNew", True)
        layout.addLayout(grid)
        layout.addSpacing(4)
        self.saving = QFrame()
        self.saving.setObjectName("Saving")
        saving_row = QHBoxLayout(self.saving)
        saving_row.setContentsMargins(16, 14, 16, 14)
        saving_row.setSpacing(14)
        self.saving_big = QLabel("—")
        self.saving_big.setObjectName("SavingBig")
        self.saving_title = QLabel()
        self.saving_title.setObjectName("SavingTitle")
        self.saving_title.setWordWrap(True)
        saving_row.addWidget(self.saving_big)
        saving_row.addWidget(self.saving_title, 1)
        layout.addWidget(self.saving)
        return frame

    def _bar_row(self, grid: QGridLayout, row: int, text: str, bar_name: str, accent: bool
                 ) -> tuple[QProgressBar, QLabel]:
        name = QLabel(text)
        name.setObjectName("BarLabel")
        bar = QProgressBar()
        bar.setObjectName(bar_name)
        bar.setRange(0, 1000)
        bar.setTextVisible(False)
        amount = QLabel("—")
        amount.setObjectName("BarAmount")
        amount.setProperty("accent", accent)
        amount.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(name, row, 0)
        grid.addWidget(bar, row, 1)
        grid.addWidget(amount, row, 2)
        return bar, amount

    # --- state ----------------------------------------------------------------
    def _mode(self) -> str:
        return MODE_IDS.get(self.mode_group.checkedId(), "balanced")

    def _custom_kbps(self) -> int | None:
        text = self.kbps_edit.text()
        return int(text) if text.isdigit() and int(text) > 0 else None

    def _use_custom(self) -> None:
        if self._mode() != CUSTOM_MODE:
            self.mode_group.button(3).setChecked(True)

    def _set_notice(self, message: str) -> None:
        self.notice.setText(message)
        self.notice.setVisible(bool(message))

    def _refresh(self) -> None:
        """Recompute the estimate, saving card and button state from the current inputs."""
        mode = self._mode()
        custom = mode == CUSTOM_MODE
        kbps = self._custom_kbps()
        _set_prop(self.control, "active", custom)
        self.preset_note.setText(PRESET_HINTS.get(mode, "นับรวมเสียง AAC 128 kbps"))

        ready = self._size > 0 and self._duration > 0
        usable = ready and (not custom or kbps is not None)
        self.start_btn.setEnabled(usable)
        self.bar_orig.setValue(1000 if ready else 0)
        self.amount_orig.setText(format_size(self._size) if ready else "—")
        if not usable:
            self.bar_new.setValue(0)
            self.amount_new.setText("—")
            self._set_saving("idle", "—", "กรอกบิตเรตเป็นตัวเลขมากกว่า 0" if ready else "กำลังอ่านข้อมูลไฟล์…")
            self._estimate = 0
            self._refresh_summary()
            return
        estimate = estimate_output_bytes(self._size, self._duration, mode, kbps)
        self._estimate = estimate
        saved = self._size - estimate
        percent = round(abs(saved) / self._size * 100)
        self.bar_new.setValue(min(1000, round(estimate / self._size * 1000)))
        self.amount_new.setText(format_size(estimate))
        if saved >= 0:
            self._set_saving("ok", f"−{percent}%", f"ประหยัดพื้นที่ {format_size(saved)}")
        else:
            self._set_saving("warn", f"+{percent}%", f"ไฟล์อาจใหญ่ขึ้น {format_size(-saved)} ลองลดบิตเรต")
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        if not self._path or not getattr(self, "_estimate", 0):
            self.action_bar.set_summary("")
            return
        mode = self._mode()
        label_text = f"{self._custom_kbps()} kbps" if mode == CUSTOM_MODE else PRESET_LABELS[mode]
        self.action_bar.set_summary(f"≈ {format_size(self._estimate)} · {label_text}")  # no file name: they can be long

    def _set_saving(self, state: str, big: str, title: str) -> None:
        _set_prop(self.saving, "state", state)
        _set_prop(self.saving_big, "state", state)
        self.saving_big.setText(big)
        self.saving_title.setText(title)

    def _output_path(self) -> Path:
        stem = safe_filename(strip_extension(self.name_field.name(), "mp4")) or Path(self._path or "video").stem
        return unique_path(Path(self._path or ".").parent, stem, "mp4", taken=self._reserved)

    # --- file selection and probing -------------------------------------------
    def set_input_file(self, path: str) -> None:
        """Select a source file and start reading its details in the background."""
        self._set_notice("")
        self._path = path
        self.dropzone.start_dir = os.path.dirname(path) or self.dropzone.start_dir
        self._size = 0
        self._duration = 0.0
        self.dropzone.hide()
        self.format_badge.setText(Path(path).suffix.lstrip(".").upper() or "?")
        self.file_name.set_full_text(os.path.basename(path))
        self.file_meta.set_full_text("กำลังอ่านข้อมูลไฟล์…")
        _set_prop(self.file_meta, "error", False)
        for _tile, value in self.tiles.values():
            value.set_full_text("…")
        self.name_field.edited = False  # a new file gets a fresh suggested name
        self.name_field.set_default(suggest_output_path(path, self._reserved).stem)
        if self.file_card.isHidden():  # first file: the cards rise in one after another
            pop_in(self.file_card)
            pop_in(self.mode_card, delay=110)
            pop_in(self.estimate_card, delay=220)
        self._refresh()
        worker = _ProbeWorker(path, self)
        worker.probed.connect(self._on_probed)
        worker.failed.connect(self._on_probe_failed)
        worker.finished.connect(self._on_probe_finished)
        self._probes.add(worker)
        worker.start()

    def _on_probed(self, path: str, size: int, duration: float, info: dict[str, Any]) -> None:
        if path != self._path:
            return  # the user already picked another file
        self._size = size
        self._duration = duration
        video = info["video"]
        self.file_meta.set_full_text(f"วิดีโอ · {info['format_long'] or info['format_name']}")
        values = {
            "duration": _format_duration(duration),
            "size": format_size(size),
            "bitrate": _format_kbps(original_kbps(size, duration)),
            "video": f"{CODEC_NAMES.get(video['codec'], video['codec'].upper())} · {video['width']}×{video['height']}",
        }
        for key, (_tile, value) in self.tiles.items():
            value.set_full_text(values[key])
        self._refresh()

    def _on_probe_failed(self, path: str, message: str) -> None:
        if path != self._path:
            return
        self.file_meta.set_full_text(message)
        _set_prop(self.file_meta, "error", True)
        for _tile, value in self.tiles.values():
            value.set_full_text("—")
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
        output = self._output_path()
        self._reserved.add(str(output))
        task = CompressTask(
            self._path, str(output), mode=mode,
            target_kbps=self._custom_kbps() if mode == CUSTOM_MODE else None,
        )
        self.task_submitted.emit(task)
        self.action_bar.flash("เพิ่มเข้าคิวแล้ว ✓")
        self.name_field.edited = False
        self.name_field.set_default(suggest_output_path(self._path, self._reserved).stem)
