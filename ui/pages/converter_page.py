"""Feature 2 page: converter and audio separator UI (owner: member 2)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    Property, QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt, QThread, QTimer, Signal,
)
from PySide6.QtGui import (
    QColor, QDragEnterEvent, QDropEvent, QPainter, QPen, QStandardItem, QStandardItemModel,
)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QListView,
    QPushButton, QVBoxLayout, QWidget,
)

from core.errors import ClipHubError
from core.ffmpeg import probe_duration
from tasks.converter import (
    SEPARATOR_FORMATS, VIDEO_TARGETS, ConverterTask, audio_sidecar_path,
    describe_conversion, suggest_output_path,
)
from ui import theme
from ui.widgets import ElidedLabel, card, page_header, primary_button

_VIDEO_EXTS = "*.mp4 *.mkv *.avi *.mov *.webm *.m4v *.flv *.wmv *.ts"
_AUDIO_EXTS = "*.mp3 *.wav *.aac *.flac *.m4a *.ogg *.opus *.wma"
FILE_FILTERS = ";;".join([
    f"ไฟล์วิดีโอและเสียง ({_VIDEO_EXTS} {_AUDIO_EXTS})",
    f"ไฟล์วิดีโอ ({_VIDEO_EXTS})",
    f"ไฟล์เสียง ({_AUDIO_EXTS})",
    "ทุกไฟล์ (*)",
])

def format_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            digits = 0 if unit == "B" or size >= 100 else 1
            return f"{size:.{digits}f} {unit}"
        size /= 1024
    return f"{num_bytes} B"


def format_duration(seconds: float) -> str:
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


class _ProbeThread(QThread):
    """Reads size and duration of the chosen file so the GUI thread never touches the disk."""

    probed = Signal(int, object)  # serial, {"size": int | None, "duration": float | None, "error": str | None}

    def __init__(self, serial: int, path: str) -> None:
        super().__init__()
        self._serial = serial
        self._path = path

    def run(self) -> None:
        info: dict[str, object] = {"size": None, "duration": None, "error": None}
        try:
            info["size"] = Path(self._path).stat().st_size
            info["duration"] = probe_duration(self._path)
        except ClipHubError as exc:
            info["error"] = str(exc)
        except OSError as exc:
            info["error"] = f"อ่านไฟล์ไม่ได้: {exc.strerror or exc}"
        self.probed.emit(self._serial, info)


class _ElidedLabel(ElidedLabel):
    """Single-line label that shortens the middle of long file names."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, Qt.TextElideMode.ElideMiddle, parent)


class _Combo(QComboBox):
    """QComboBox whose popup is a QListView, so the stylesheet can round and pad its items."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setView(QListView())
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class SwitchCheckBox(QCheckBox):
    """A QCheckBox drawn as an Apple-style toggle. The text is for accessibility only."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setFixedSize(46, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._offset = 1.0 if self.isChecked() else 0.0
        self._animation = QPropertyAnimation(self, b"offset", self)
        self._animation.setDuration(180)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)

    def _animate(self, checked: bool) -> None:
        self._animation.stop()
        self._animation.setStartValue(self._offset)
        self._animation.setEndValue(1.0 if checked else 0.0)
        self._animation.start()

    def _get_offset(self) -> float:
        return self._offset

    def _set_offset(self, value: float) -> None:
        self._offset = value
        self.update()

    offset = Property(float, _get_offset, _set_offset)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt API)
        return QSize(46, 28)

    def hitButton(self, pos) -> bool:  # noqa: N802 (Qt API)
        return self.rect().contains(pos)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt API)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(0.4)
        mix = self._offset
        off, on = theme.color("fill_hover"), theme.color("switch_on")
        track = QColor(
            round(off.red() + (on.red() - off.red()) * mix),
            round(off.green() + (on.green() - off.green()) * mix),
            round(off.blue() + (on.blue() - off.blue()) * mix),
            round(off.alpha() + (on.alpha() - off.alpha()) * mix),
        )
        painter.setPen(QPen(theme.color("accent"), 2) if self.hasFocus() else Qt.PenStyle.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(QRectF(1, 1, 44, 26), 13, 13)
        knob = QRectF(3.0 + 18.0 * mix, 3.0, 22, 22)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 40))
        painter.drawEllipse(knob.translated(0, 1))
        painter.setBrush(theme.color("knob"))
        painter.drawEllipse(knob)


class _DropZone(QFrame):
    """Dashed target that takes one file by drag and drop, click or the browse button."""

    file_dropped = Signal(str, int)  # first path, number of files dropped
    drop_rejected = Signal(str)      # Thai message
    browse_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        icon = QLabel()
        icon.setObjectName("DzIcon")
        icon.setFixedSize(60, 60)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon = icon
        theme.on_change(self._refresh_icon)
        title = QLabel("ลากไฟล์มาวางที่นี่")
        title.setObjectName("DzTitle")
        # two fixed lines instead of word wrap: wrapped labels in nested layouts get clipped
        sub = QLabel("หรือคลิกเพื่อเลือกไฟล์")
        sub.setObjectName("DzSub")
        self.browse_button = QPushButton("เลือกไฟล์จากเครื่อง")
        self.browse_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.browse_button.clicked.connect(self.browse_requested)
        self.browse_button.hide()  # the whole drop zone is clickable

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 30, 24, 28)
        layout.setSpacing(6)
        for widget in (icon, title, sub):
            layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(8)
        layout.addWidget(self.browse_button, 0, Qt.AlignmentFlag.AlignHCenter)
        for label in (title, sub):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def _refresh_icon(self) -> None:
        self._icon.setPixmap(theme.icon_pixmap("upload", theme.hex_color("accent"), 28, 2.0))

    def _set_drag(self, active: bool) -> None:
        self.setProperty("drag", active)
        self.style().unpolish(self)
        self.style().polish(self)

    @staticmethod
    def _local_paths(event) -> list[str]:
        return [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802 (Qt API)
        if self._local_paths(event):
            event.acceptProposedAction()
            self._set_drag(True)
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802 (Qt API)
        self._set_drag(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 (Qt API)
        self._set_drag(False)
        paths = self._local_paths(event)
        if not paths:
            self.drop_rejected.emit("วางได้เฉพาะไฟล์จากเครื่องนี้")
            return
        if Path(paths[0]).is_dir():
            self.drop_rejected.emit("ไม่รองรับโฟลเดอร์ กรุณาเลือกไฟล์เดียว")
            return
        event.acceptProposedAction()
        self.file_dropped.emit(paths[0], len(paths))

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if event.button() == Qt.MouseButton.LeftButton:
            self.browse_requested.emit()
        super().mousePressEvent(event)


def _card(tight: bool = False):
    margin = 14 if tight else 20
    return card(spacing=12, margins=(margin, margin - 2, margin, margin))


def _label(text: str, name: str = "") -> QLabel:
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    return label


class ConverterPage(QWidget):
    task_submitted = Signal(object)  # ConverterTask

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ConverterPage")
        self._input_path: str | None = None
        self._ready = False          # probe finished OK for the current file
        self._serial = 0
        self._probes: list[_ProbeThread] = []
        self._reserved: set[Path] = set()  # outputs of queued jobs that are not on disk yet
        self._last_dir = str(Path.home())
        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(lambda: self.status_label.setText(""))

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)
        root.addLayout(self._build_header())
        root.addWidget(self._build_drop_card())
        root.addWidget(self._build_file_card())
        root.addWidget(self._build_target_card())
        root.addWidget(self._build_separator_card())
        root.addLayout(self._build_actions())
        root.addStretch(1)

        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._wait_probes)
        self._refresh()
        theme.on_change(self._refresh_icons)

    def _refresh_icons(self) -> None:
        self._thumb.setPixmap(theme.icon_pixmap("film", theme.hex_color("text2"), 22, 1.6))

    # ----- layout -------------------------------------------------------------------
    def _build_header(self) -> QHBoxLayout:
        return page_header("แปลงไฟล์")

    def _build_drop_card(self) -> QFrame:
        card, layout = _card()
        self.dropzone = _DropZone()
        self.dropzone.file_dropped.connect(self._on_file_dropped)
        self.dropzone.drop_rejected.connect(lambda message: self._set_notice(message, error=True))
        self.dropzone.browse_requested.connect(self._browse)
        self.notice_label = _ElidedLabel()
        self.notice_label.setObjectName("NoticeText")
        self.notice_label.hide()
        layout.addWidget(self.dropzone)
        layout.addWidget(self.notice_label)
        return card

    def _build_file_card(self) -> QFrame:
        self.file_card, layout = _card(tight=True)
        self.file_card.hide()
        row_frame = QFrame()
        row_frame.setObjectName("FileRow")
        row = QHBoxLayout(row_frame)
        row.setContentsMargins(14, 14, 14, 14)
        row.setSpacing(14)

        thumb = QLabel()
        thumb.setObjectName("Thumb")
        thumb.setFixedSize(56, 44)
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb = thumb
        meta = QVBoxLayout()
        meta.setSpacing(2)
        self.file_name_label = _ElidedLabel()
        self.file_name_label.setObjectName("FileName")
        self.file_meta_label = _label("", "FileMeta")
        meta.addWidget(self.file_name_label)
        meta.addWidget(self.file_meta_label)
        self.file_size_label = _label("", "FileSize")

        row.addWidget(thumb)
        row.addLayout(meta, 1)
        row.addWidget(self.file_size_label)
        self.file_error_label = _ElidedLabel()
        self.file_error_label.setObjectName("ErrorText")
        self.file_error_label.hide()
        layout.addWidget(row_frame)
        layout.addWidget(self.file_error_label)
        return self.file_card

    def _build_target_card(self) -> QFrame:
        card, layout = _card()
        grid = QHBoxLayout()
        grid.setSpacing(14)

        left = QVBoxLayout()
        left.setSpacing(9)
        left.addWidget(_label("แปลงเป็น", "FieldLabel"))
        self.target_combo = _Combo()
        model = QStandardItemModel(self.target_combo)
        groups = (
            ("วิดีโอ", [("MP4 (H.264)", "mp4"), ("MKV", "mkv"), ("AVI", "avi"), ("MOV", "mov")]),
            ("เสียง", [("MP3", "mp3"), ("WAV", "wav"), ("AAC", "aac"), ("FLAC", "flac")]),
        )
        for title, entries in groups:
            header = QStandardItem(title)
            header.setFlags(Qt.ItemFlag.NoItemFlags)  # group heading, not selectable
            model.appendRow(header)
            for text, ext in entries:
                item = QStandardItem(text)
                item.setData(ext, Qt.ItemDataRole.UserRole)
                model.appendRow(item)
        self.target_combo.setModel(model)
        self.target_combo.setCurrentIndex(1)  # MP4
        self.target_combo.currentIndexChanged.connect(self._refresh)
        left.addWidget(self.target_combo)

        right = QVBoxLayout()
        right.setSpacing(9)
        right.addWidget(_label("ไฟล์ผลลัพธ์", "FieldLabel"))
        self.output_label = _ElidedLabel()
        self.output_label.setObjectName("OutName")
        self.output_label.setFixedHeight(theme.CONTROL_HEIGHT)
        right.addWidget(self.output_label)

        grid.addLayout(left, 1)
        grid.addLayout(right, 1)
        layout.addLayout(grid)
        return card

    def _build_separator_card(self) -> QFrame:
        card, layout = _card()
        row = QHBoxLayout()
        row.setSpacing(14)
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(_label("แยกไฟล์เสียงออกมาด้วย", "ToggleTitle"))
        self.separator_hint = _label("", "Note")
        text.addWidget(self.separator_hint)
        self.separator_hint.hide()  # the switch is simply disabled for audio targets

        self.audio_format_combo = _Combo()
        self.audio_format_combo.setObjectName("FmtCombo")
        for ext in SEPARATOR_FORMATS:
            self.audio_format_combo.addItem(ext.upper(), ext)
        self.audio_format_combo.currentIndexChanged.connect(self._refresh)
        self.extract_switch = SwitchCheckBox("แยกสตรีมเสียงออกจากวิดีโอ")
        self.extract_switch.toggled.connect(self._refresh)

        row.addLayout(text, 1)
        row.addWidget(self.audio_format_combo, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.extract_switch, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(row)
        return card

    def _build_actions(self) -> QVBoxLayout:
        box = QVBoxLayout()
        box.setSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(18)
        self.start_button = primary_button("เริ่มแปลงไฟล์", "convert")
        self.start_button.clicked.connect(self._submit)
        self.hint_label = _label("", "Hint")
        row.addWidget(self.start_button)
        row.addWidget(self.hint_label, 1)
        self.status_label = _label("", "OkText")
        box.addLayout(row)
        box.addWidget(self.status_label)
        return box

    # ----- state --------------------------------------------------------------------
    def target_ext(self) -> str:
        return str(self.target_combo.currentData(Qt.ItemDataRole.UserRole) or "mp4")

    def _extract_active(self) -> bool:
        return self.extract_switch.isChecked() and self.target_ext() in VIDEO_TARGETS

    def _sidecar_format(self) -> str | None:
        if not self._extract_active():
            return None
        return str(self.audio_format_combo.currentData() or "mp3")

    def _refresh(self, *_args: object) -> None:
        ext = self.target_ext()
        is_video = ext in VIDEO_TARGETS
        self.extract_switch.setEnabled(is_video)
        self.audio_format_combo.setEnabled(is_video and self.extract_switch.isChecked())
        self.separator_hint.setText(
            "บันทึกเป็น MP3 / WAV — วิดีโอต้นฉบับไม่ถูกแก้ไข" if is_video else
            "ใช้ได้เมื่อเลือกแปลงเป็นฟอร์แมตวิดีโอ"
        )
        ready = self._input_path is not None and self._ready
        self.start_button.setEnabled(ready)
        if self._input_path is None:
            self.output_label.set_full_text("—")
            self.hint_label.setText("")
            return
        sidecar = self._sidecar_format()
        output = suggest_output_path(
            self._input_path, ext, sidecar_ext=sidecar, taken=self._reserved
        )
        self.output_label.set_full_text(output.name)
        self.hint_label.setText(
            describe_conversion(Path(self._input_path).suffix, ext, sidecar)
        )

    def _set_notice(self, message: str, error: bool = False) -> None:
        self.notice_label.setObjectName("ErrorText" if error else "NoticeText")
        self.notice_label.style().unpolish(self.notice_label)
        self.notice_label.style().polish(self.notice_label)
        self.notice_label.set_full_text(message)
        self.notice_label.setVisible(bool(message))

    # ----- choosing a file ----------------------------------------------------------
    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "เลือกไฟล์วิดีโอหรือเสียง", self._last_dir, FILE_FILTERS
        )
        if path:
            self.set_input_file(path)

    def _on_file_dropped(self, path: str, count: int) -> None:
        self.set_input_file(path)
        if count > 1:
            self._set_notice(f"รับครั้งละ 1 ไฟล์ — ใช้ไฟล์แรก ({Path(path).name})")

    def set_input_file(self, path: str) -> None:
        """Select the file to convert; size and duration are read in a QThread."""
        self._set_notice("")
        self._input_path = path
        self._ready = False
        self._last_dir = str(Path(path).parent)
        self._serial += 1
        self._reap_probes()
        self.file_card.show()
        self.file_name_label.set_full_text(Path(path).name)
        self.file_meta_label.setText("กำลังอ่านข้อมูลไฟล์…")
        self.file_size_label.setText("")
        self.file_error_label.hide()
        self.status_label.setText("")
        probe = _ProbeThread(self._serial, path)
        probe.probed.connect(self._on_probed)
        probe.finished.connect(self._reap_probes)
        self._probes.append(probe)
        probe.start()
        self._refresh()

    def _on_probed(self, serial: int, info: dict) -> None:
        if serial != self._serial:
            return  # the user picked another file meanwhile
        if info.get("size") is not None:
            self.file_size_label.setText(format_size(int(info["size"])))
        error = info.get("error")
        if error:
            self._ready = False
            self.file_meta_label.setText("ความยาว —")
            self.file_error_label.set_full_text(str(error))
            self.file_error_label.show()
        else:
            self._ready = True
            self.file_meta_label.setText(f"ความยาว {format_duration(float(info['duration']))}")
        self._refresh()

    def _reap_probes(self) -> None:
        for probe in list(self._probes):
            if probe.wait(0):  # finished; the rest are swept on the next call
                self._probes.remove(probe)
                probe.deleteLater()

    def _wait_probes(self) -> None:
        for probe in list(self._probes):
            probe.wait(3000)

    # ----- submit -------------------------------------------------------------------
    def build_task(self) -> ConverterTask | None:
        """Create the task for the current selection and reserve its output names."""
        if self._input_path is None or not self._ready:
            return None
        ext = self.target_ext()
        sidecar = self._sidecar_format()
        output = suggest_output_path(
            self._input_path, ext, sidecar_ext=sidecar, taken=self._reserved
        )
        self._reserved.add(output)
        if sidecar:
            self._reserved.add(audio_sidecar_path(output, sidecar))
        return ConverterTask(
            self._input_path, str(output),
            extract_audio=sidecar is not None, audio_format=sidecar or "mp3",
        )

    def _submit(self) -> None:
        task = self.build_task()
        if task is None:
            return
        self.task_submitted.emit(task)
        self.status_label.setText(f"เพิ่มงานเข้าคิวแล้ว: {Path(task.output_path).name}")
        self._status_timer.start(5000)
        self._refresh()  # the preview moves on to the next free name
