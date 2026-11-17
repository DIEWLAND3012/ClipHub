"""Feature 5 page: FastCut, pick a part of a video or song the way Instagram picks a music clip.

Flow: drop or pick a file -> the preview (with play and a moving playhead) and the timeline pop in.
The timeline shows the sound as bars under a gradient box: drag the box to move the part, drag
its edges to make it longer or shorter, or type the length. Then choose the folder and the name,
and Export (from the action bar) sends a FastCutTask to the queue.
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDoubleValidator, QImage, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from core.errors import ClipHubError
from core.ffmpeg import probe_media
from core.naming import safe_filename, strip_extension, unique_path
from tasks.fastcut import MIN_LENGTH, FastCutTask, format_time, is_audio, output_ext, waveform_peaks
from tasks.fastcut import suggest_output_path as suggest_cut_output
from ui import theme
from ui.widgets import (
    ActionBar, Columns, DropZone, ElidedLabel, NameField, PageDrop, Segmented, card, field_label, label,
    page_header, pop_in, primary_button,
)

FILE_FILTER = ("วิดีโอและเสียง (*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.flv *.wmv *.ts *.mts *.3gp *.mpg "
               "*.mp3 *.m4a *.aac *.wav *.flac *.ogg *.opus *.wma *.aif *.aiff);;ทุกไฟล์ (*)")
DEFAULT_LENGTH = 20.0  # seconds, like a story clip
GRADIENT = ("#fccc2e", "#f7772d", "#e8336b", "#9b35f5")  # the selection box, yellow to purple


def _gradient(rect: QRectF, alpha: int = 255) -> QLinearGradient:
    grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
    for index, hex_color in enumerate(GRADIENT):
        color = QColor(hex_color)
        color.setAlpha(alpha)
        grad.setColorAt(index / (len(GRADIENT) - 1), color)
    return grad


def _step(number: int, text: str) -> QHBoxLayout:
    badge = label(str(number), "StepNumber")
    badge.setFixedSize(20, 20)
    badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
    row = QHBoxLayout()
    row.setSpacing(8)
    row.addWidget(badge)
    row.addWidget(field_label(text))
    row.addStretch(1)
    return row


# --- background work ---------------------------------------------------------------------------------
class _Loader(QThread):
    """Reads the duration and the sound bars off the GUI thread."""

    loaded = Signal(str, float, bool, object)  # path, duration, has video, peaks
    failed = Signal(str, str)

    def __init__(self, path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.path = path

    def run(self) -> None:
        try:
            info = probe_media(self.path)
            if info["duration"] <= 0:
                raise ClipHubError("ไม่พบความยาวของไฟล์")
            if info["video"] is None and info.get("audio") is None and not is_audio(self.path):
                raise ClipHubError("ไฟล์นี้ไม่มีภาพหรือเสียงให้ตัด")
            peaks = waveform_peaks(self.path)
        except ClipHubError as exc:
            self.failed.emit(self.path, str(exc))
        except OSError as exc:
            self.failed.emit(self.path, f"อ่านไฟล์ไม่ได้: {exc}")
        else:
            has_video = info["video"] is not None and not is_audio(self.path)
            self.loaded.emit(self.path, float(info["duration"]), has_video, peaks)


class _FrameGrab(QThread):
    """One still frame at a time, via FFmpeg: a paused player does not always show where it seeks."""

    grabbed = Signal(str, float, QImage)

    def __init__(self, path: str, seconds: float, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.path, self.seconds = path, seconds

    def run(self) -> None:
        import subprocess
        from core.ffmpeg import _spawn_kwargs, find_tool
        try:
            cmd = [find_tool("ffmpeg"), "-v", "error", "-ss", f"{self.seconds:.3f}", "-i", self.path,
                   "-frames:v", "1", "-vf", "scale=960:-2", "-f", "image2pipe", "-vcodec", "bmp", "-"]
            data = subprocess.run(cmd, capture_output=True, timeout=15, check=False, **_spawn_kwargs()).stdout
        except Exception:
            return
        image = QImage.fromData(data)
        if not image.isNull():
            self.grabbed.emit(self.path, self.seconds, image)


# --- preview -------------------------------------------------------------------------------------------
class VideoView(QWidget):
    """The current video frame, letterboxed in a rounded black panel (or a music mark for audio)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(150)
        self.setMaximumHeight(300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._image = QImage()
        self.audio_only = False

    def sizeHint(self):  # noqa: N802 (Qt API)
        from PySide6.QtCore import QSize
        return QSize(480, 270)

    def heightForWidth(self, width: int) -> int:  # noqa: N802 (Qt API)
        return min(300, max(150, width * 9 // 16))

    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt API)
        return True

    def set_frame(self, frame) -> None:
        image = frame.toImage()
        if not image.isNull():
            self.set_image(image)

    def set_image(self, image: QImage) -> None:
        self._image = image
        self.update()

    def clear(self) -> None:
        self._image = QImage()
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt API)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(rect, 14, 14)
        painter.setClipPath(clip)
        painter.fillRect(rect, QColor("#000000") if not self.audio_only else QColor(0, 0, 0, 0))
        if self.audio_only:
            painter.fillPath(clip, _gradient(rect, 235))
            icon = theme.icon_pixmap("music", "#ffffff", 64, 1.8)
            painter.drawPixmap(int(rect.center().x() - 32), int(rect.center().y() - 32), icon)
            return
        if self._image.isNull():
            return
        size = self._image.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        target = QRectF((self.width() - size.width()) / 2, (self.height() - size.height()) / 2,
                        size.width(), size.height())
        painter.drawImage(target, self._image)


class PlayButton(QPushButton):
    """Round play / pause button filled with the selection gradient."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(40, 40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._playing = False

    def set_playing(self, playing: bool) -> None:
        self._playing = playing
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt API)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_gradient(rect, 255 if self.isEnabled() else 120))
        painter.drawEllipse(rect)
        if self.underMouse():
            painter.setBrush(QColor(255, 255, 255, 40))
            painter.drawEllipse(rect)
        icon = theme.icon_pixmap("pause" if self._playing else "play", "#ffffff", 16, 2.2)
        nudge = 0 if self._playing else 1  # a play triangle looks centred a pixel to the right
        painter.drawPixmap(int(rect.center().x() - 8 + nudge), int(rect.center().y() - 8), icon)


# --- timeline --------------------------------------------------------------------------------------------
class TrimTimeline(QWidget):
    """Sound bars with a gradient selection box (move it, stretch either edge) and a playhead."""

    selection_changed = Signal(float, float)  # start, end (seconds)
    seek_requested = Signal(float)
    drag_finished = Signal()

    BAR_W = 3.0
    GAP = 4.0
    EDGE = 12.0  # grab width of a box edge
    BOX_H = 92.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(int(self.BOX_H) + 22)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.duration = 0.0
        self.peaks: list[float] = []
        self.start = 0.0
        self.end = 0.0
        self.playhead = 0.0
        self._drag: str = ""     # "move" | "left" | "right" | "seek"
        self._grab = 0.0         # where in the box the move started (seconds)
        self._moved = False

    # --- data -----------------------------------------------------------------------------
    def set_media(self, duration: float, peaks: list[float]) -> None:
        self.duration = duration
        self.peaks = peaks
        self.update()

    def set_selection(self, start: float, end: float, emit: bool = True) -> None:
        if self.duration <= 0:
            return
        length = max(MIN_LENGTH, min(end - start, self.duration))
        start = max(0.0, min(start, self.duration - length))
        self.start, self.end = start, start + length
        self.playhead = min(max(self.playhead, self.start), self.end)
        self.update()
        if emit:
            self.selection_changed.emit(self.start, self.end)

    def set_playhead(self, seconds: float) -> None:
        self.playhead = seconds
        self.update()

    # --- geometry ------------------------------------------------------------------------------
    def _track(self) -> QRectF:
        return QRectF(self.EDGE, (self.height() - self.BOX_H) / 2, self.width() - 2 * self.EDGE, self.BOX_H)

    def _x(self, seconds: float) -> float:
        track = self._track()
        return track.left() + (seconds / self.duration) * track.width() if self.duration else track.left()

    def _t(self, x: float) -> float:
        track = self._track()
        if not self.duration or track.width() <= 0:
            return 0.0
        return max(0.0, min(self.duration, (x - track.left()) / track.width() * self.duration))

    def _box(self) -> QRectF:
        track = self._track()
        left, right = self._x(self.start), self._x(self.end)
        if right - left < 2 * self.EDGE:  # a short part of a long file stays easy to grab
            middle = (left + right) / 2
            left, right = middle - self.EDGE, middle + self.EDGE
        return QRectF(left, track.top(), right - left, track.height())

    # --- painting ---------------------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 (Qt API)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = self._track()
        box = self._box()
        if self.duration <= 0:
            return
        dim = theme.color("text3")
        count = max(1, int(track.width() // (self.BAR_W + self.GAP)))
        played = QRectF(box.left(), box.top(), max(0.0, self._x(self.playhead) - box.left()), box.height())

        # box: soft gradient inside, solid gradient for the part already played
        inner = QPainterPath()
        inner.addRoundedRect(box, 12, 12)
        painter.fillPath(inner, _gradient(box, 70))
        painter.save()
        painter.setClipPath(inner)
        painter.fillRect(played, _gradient(box, 255))
        painter.restore()

        for index in range(count):
            x = track.left() + index * (self.BAR_W + self.GAP) + self.GAP / 2
            level = self._level(index / max(count - 1, 1))
            bar_h = 10 + level * (track.height() * 0.62 - 10)
            rect = QRectF(x, track.center().y() - bar_h / 2, self.BAR_W, bar_h)
            inside = box.left() <= x <= box.right()
            painter.fillPath(self._pill(rect), QColor("#ffffff") if inside else dim)

        pen = QPen()
        pen.setBrush(_gradient(box))
        pen.setWidthF(4)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(box.adjusted(2, 2, -2, -2), 10, 10)
        for edge_x in (box.left() + 6, box.right() - 6):  # grab marks on the edges
            painter.fillPath(self._pill(QRectF(edge_x - 1.5, box.center().y() - 12, 3, 24)), QColor("#ffffff"))

        if self.start <= self.playhead <= self.end:
            x = self._x(self.playhead)
            painter.fillRect(QRectF(x - 1, box.top() + 6, 2, box.height() - 12), QColor("#ffffff"))

    def _level(self, fraction: float) -> float:
        if not self.peaks:
            return 0.35
        return self.peaks[min(len(self.peaks) - 1, int(fraction * len(self.peaks)))]

    @staticmethod
    def _pill(rect: QRectF) -> QPainterPath:
        path = QPainterPath()
        path.addRoundedRect(rect, rect.width() / 2, rect.width() / 2)
        return path

    # --- mouse ------------------------------------------------------------------------------------------
    def _hit(self, x: float) -> str:
        box = self._box()
        if abs(x - box.left()) <= self.EDGE:
            return "left"
        if abs(x - box.right()) <= self.EDGE:
            return "right"
        if box.left() < x < box.right():
            return "move"
        return "outside"

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt API)
        x = event.position().x()
        if not self._drag:
            hit = self._hit(x)
            shape = {"left": Qt.CursorShape.SizeHorCursor, "right": Qt.CursorShape.SizeHorCursor,
                     "move": Qt.CursorShape.OpenHandCursor}.get(hit, Qt.CursorShape.PointingHandCursor)
            self.setCursor(shape)
            return
        self._moved = True
        t = self._t(x)
        if self._drag == "move":
            length = self.end - self.start
            self.set_selection(t - self._grab, t - self._grab + length)
            self.seek_requested.emit(self.start)
        elif self._drag == "left":
            start = max(0.0, min(t, self.end - MIN_LENGTH))
            self.set_selection(start, self.end)
            self.seek_requested.emit(self.start)
        elif self._drag == "right":
            end = min(self.duration, max(t, self.start + MIN_LENGTH))
            self.set_selection(self.start, end)
            self.seek_requested.emit(max(self.start, end - 1.5))  # show what the end sounds like

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if event.button() != Qt.MouseButton.LeftButton or self.duration <= 0:
            return
        x = event.position().x()
        hit = self._hit(x)
        self._moved = False
        if hit == "outside":  # jump the box to where the user clicked, keeping its length
            length = self.end - self.start
            t = self._t(x)
            self.set_selection(t - length / 2, t + length / 2)
            self.seek_requested.emit(self.start)
            self._drag, self._grab = "move", t - self.start
        else:
            self._drag = hit
            self._grab = self._t(x) - self.start
        if self._drag == "move":
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if self._drag == "move" and not self._moved:
            self.seek_requested.emit(min(max(self._t(event.position().x()), self.start), self.end))
        if self._drag:
            self.drag_finished.emit()
        self._drag = ""
        self.mouseMoveEvent(event)


# --- page ------------------------------------------------------------------------------------------------
class FastCutPage(QWidget):
    task_submitted = Signal(object)  # FastCutTask

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._path: str | None = None
        self._duration = 0.0
        self._has_video = True
        self._loaders: set[_Loader] = set()
        self._reserved: set[str] = set()
        self._player = None  # QMediaPlayer, made on first use
        self._grab_at = -1.0     # still frame wanted at (seconds)
        self._grabber: _FrameGrab | None = None
        self._grab_timer = QTimer(self)
        self._grab_timer.setSingleShot(True)
        self._grab_timer.setInterval(90)  # while dragging, fetch at most ~10 frames a second
        self._grab_timer.timeout.connect(self._grab_frame)
        self._build()
        self._refresh()

    # --- layout ---------------------------------------------------------------------------------
    def _build(self) -> None:
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(16)
        col.addLayout(page_header("FastCut"))
        self.dropzone = DropZone("ลากไฟล์วิดีโอหรือเสียงมาวางที่นี่", FILE_FILTER, "เลือกไฟล์วิดีโอหรือเสียง")
        self.dropzone.setMinimumHeight(300)
        self.dropzone.file_chosen.connect(self.set_input_file)
        self.dropzone.rejected.connect(self._set_notice)
        col.addWidget(self.dropzone)
        PageDrop(self, self.set_input_file, self._set_notice)
        self.notice = label("", "ErrorText", wrap=True)
        self.notice.hide()
        col.addWidget(self.notice)

        self.columns = Columns(breakpoint=820, stretch=(7, 5))
        self.preview_card = self._build_preview()
        self.settings_card = self._build_settings()
        self.columns.left.addWidget(self.preview_card)
        self.columns.right.addWidget(self.settings_card)
        self.columns.right.addStretch(1)
        col.addWidget(self.columns)
        col.addStretch(1)
        for widget in (self.preview_card, self.settings_card):
            widget.hide()

        self.action_bar = ActionBar()
        self.export_btn = self.action_bar.add_button(primary_button("Export", "fastcut"))
        self.export_btn.clicked.connect(self._submit)

    def _build_preview(self) -> QFrame:
        frame, layout = card(spacing=12, margins=(16, 16, 16, 14))
        self.view = VideoView()
        layout.addWidget(self.view, 1)
        row = QHBoxLayout()
        row.setSpacing(12)
        self.play_btn = PlayButton()
        self.play_btn.setToolTip("เล่นช่วงที่เลือก (Space)")
        self.play_btn.clicked.connect(self.toggle_play)
        self.file_name = ElidedLabel(mode=Qt.TextElideMode.ElideMiddle)
        self.file_name.setObjectName("FileTitle")
        self.time_label = QLabel("0:00.0")
        self.time_label.setObjectName("TimeLabel")
        change = QPushButton("เปลี่ยนไฟล์")
        change.setCursor(Qt.CursorShape.PointingHandCursor)
        change.clicked.connect(lambda: self.dropzone.browse())
        row.addWidget(self.play_btn)
        row.addWidget(self.file_name, 1)
        row.addWidget(self.time_label)
        row.addWidget(change)
        layout.addLayout(row)
        layout.addWidget(self._build_timeline())
        return frame

    def _build_settings(self) -> QFrame:
        frame, layout = card(spacing=10, margins=(22, 20, 22, 22))
        layout.addLayout(_step(1, "ความยาวที่ตัด"))
        self.length_box = QFrame()
        self.length_box.setObjectName("Control")
        self.length_box.setFixedHeight(theme.CONTROL_HEIGHT)
        length_row = QHBoxLayout(self.length_box)
        length_row.setContentsMargins(6, 0, 14, 0)
        length_row.setSpacing(6)
        self.minus_btn = QPushButton("−")
        self.plus_btn = QPushButton("+")
        for button in (self.minus_btn, self.plus_btn):
            button.setObjectName("StepButton")
            button.setFixedSize(34, 34)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setAutoRepeat(True)
        self.minus_btn.clicked.connect(lambda: self._nudge_length(-1))
        self.plus_btn.clicked.connect(lambda: self._nudge_length(1))
        self.length_edit = QLineEdit(f"{DEFAULT_LENGTH:g}")
        self.length_edit.setObjectName("KbpsEdit")
        self.length_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        validator = QDoubleValidator(0.5, 99999, 1)
        validator.setNotation(QDoubleValidator.Notation.StandardNotation)
        self.length_edit.setValidator(validator)
        self.length_edit.editingFinished.connect(self._on_length_typed)
        unit = QLabel("วินาที")
        unit.setObjectName("Unit")
        length_row.addWidget(self.minus_btn)
        length_row.addWidget(self.length_edit, 1)
        length_row.addWidget(self.plus_btn)
        length_row.addWidget(unit)
        layout.addWidget(self.length_box)
        self.range_label = label("", "Note")
        layout.addWidget(self.range_label)

        layout.addSpacing(6)
        layout.addLayout(_step(2, "วิธีตัด"))
        self.mode_seg = Segmented()
        self.mode_seg.setFixedHeight(theme.CONTROL_HEIGHT)
        self.mode_seg.add("เร็ว", 0)
        self.mode_seg.add("แม่นยำ", 1)
        self.mode_seg.group.button(0).setChecked(True)
        self.mode_seg.group.idToggled.connect(lambda _id, on: on and self._refresh())
        layout.addWidget(self.mode_seg)
        self.mode_note = label("", "Note", wrap=True)
        layout.addWidget(self.mode_note)

        layout.addSpacing(6)
        layout.addLayout(_step(3, "บันทึกที่"))
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("เลือกโฟลเดอร์ปลายทาง")
        self._folder_action = self.folder_edit.addAction(theme.themed_icon("folder"),
                                                         QLineEdit.ActionPosition.LeadingPosition)
        self.folder_edit.textChanged.connect(lambda _t: self._refresh())
        folder_btn = QPushButton("เลือก…")
        folder_btn.setObjectName("FieldButton")
        folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        folder_btn.clicked.connect(self.choose_folder)
        folder_row = QHBoxLayout()
        folder_row.setSpacing(10)
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(folder_btn)
        layout.addLayout(folder_row)

        layout.addSpacing(6)
        layout.addLayout(_step(4, "ชื่อไฟล์"))
        self.name_field = NameField()
        self.name_field.changed.connect(self._refresh_summary)
        layout.addWidget(self.name_field)
        return frame

    def _build_timeline(self) -> QWidget:
        frame = QWidget()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(2)
        self.timeline = TrimTimeline()
        self.timeline.selection_changed.connect(self._on_selection)
        self.timeline.seek_requested.connect(self._seek)
        self.timeline.drag_finished.connect(lambda: self._seek(self.timeline.start))  # play from the start
        layout.addWidget(self.timeline)
        ends = QHBoxLayout()
        self.zero_label = label("0:00", "Note")
        self.total_label = label("", "Note")
        ends.addWidget(self.zero_label)
        ends.addStretch(1)
        ends.addWidget(self.total_label)
        layout.addLayout(ends)
        return frame

    # --- state ------------------------------------------------------------------------------------------
    def _precise(self) -> bool:
        return self._has_video and self.mode_seg.group.checkedId() == 1

    def _ext(self) -> str:
        return output_ext(self._path or "a.mp4", self._precise())

    def _set_notice(self, message: str) -> None:
        self.notice.setText(message)
        self.notice.setVisible(bool(message))

    def _refresh(self) -> None:
        ready = self._path is not None and self._duration > 0
        self.mode_seg.setEnabled(self._has_video)
        if not self._has_video:
            self.mode_note.setText("ไฟล์เสียงตัดได้ตรงจุดและเร็วเสมอ")
        elif self._precise():
            self.mode_note.setText("เริ่มตรงเฟรมที่เลือก · เข้ารหัสภาพใหม่ ใช้เวลานานขึ้น")
        else:
            self.mode_note.setText("เสร็จในไม่กี่วินาที ไม่เข้ารหัสใหม่ · จุดเริ่มอาจเร็วกว่าที่เลือกเล็กน้อย")
        self.name_field.set_extension(self._ext())
        self.export_btn.setEnabled(ready and bool(self.folder_edit.text().strip()))
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        if not self._path or self._duration <= 0:
            self.action_bar.set_summary("")
            return
        length = self.timeline.end - self.timeline.start
        self.action_bar.set_summary(f"{format_time(length)} · {self._ext().upper()}")

    def _on_selection(self, start: float, end: float) -> None:
        length = end - start
        if not self.length_edit.hasFocus():
            self.length_edit.setText(f"{length:.1f}".rstrip("0").rstrip("."))
        self.range_label.setText(f"{format_time(start)} – {format_time(end)} · {length:.1f} วินาที")
        self._refresh_summary()

    def _on_length_typed(self) -> None:
        try:
            length = float(self.length_edit.text().replace(",", "."))
        except ValueError:
            return
        self.timeline.set_selection(self.timeline.start, self.timeline.start + length)
        self.length_edit.setText(f"{self.timeline.end - self.timeline.start:.1f}".rstrip("0").rstrip("."))

    def _nudge_length(self, step: int) -> None:
        self.timeline.set_selection(self.timeline.start, self.timeline.end + step)

    def choose_folder(self) -> None:
        start = self.folder_edit.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "เลือกโฟลเดอร์ปลายทาง", start)
        if chosen:
            self.folder_edit.setText(chosen)
            self.name_field.set_default(self._suggested_stem())

    def _suggested_stem(self) -> str:
        return suggest_cut_output(self._path, self.folder_edit.text().strip(), self._precise(), self._reserved).stem

    # --- file -----------------------------------------------------------------------------------------------
    def set_input_file(self, path: str) -> None:
        self._set_notice("")
        self._stop()
        self._path = path
        self._duration = 0.0
        self._has_video = not is_audio(path)
        self.dropzone.start_dir = os.path.dirname(path) or self.dropzone.start_dir
        self.dropzone.hide()
        self.file_name.set_full_text(os.path.basename(path))
        self.time_label.setText("กำลังอ่านไฟล์…")
        self.view.audio_only = not self._has_video
        self.view.clear()
        self.timeline.set_media(0.0, [])
        if not self.folder_edit.text().strip():
            self.folder_edit.setText(os.path.dirname(path))
        self.name_field.edited = False
        self.name_field.set_default(self._suggested_stem())
        if self.preview_card.isHidden():
            pop_in(self.preview_card)
            pop_in(self.settings_card, delay=110)
        self._refresh()
        loader = _Loader(path, self)
        loader.loaded.connect(self._on_loaded)
        loader.failed.connect(self._on_failed)
        loader.finished.connect(lambda: (self._loaders.discard(loader), loader.deleteLater()))
        self._loaders.add(loader)
        loader.start()

    def _on_loaded(self, path: str, duration: float, has_video: bool, peaks: list) -> None:
        if path != self._path:
            return
        self._duration = duration
        self._has_video = has_video
        self.view.audio_only = not has_video
        self.timeline.set_media(duration, peaks)
        self.timeline.playhead = 0.0
        self.timeline.set_selection(0.0, min(DEFAULT_LENGTH, duration))
        self.total_label.setText(format_time(duration, tenths=False))
        self.time_label.setText(f"0:00.0 / {format_time(duration)}")
        self._load_player(path)
        self._refresh()

    def _on_failed(self, path: str, message: str) -> None:
        if path != self._path:
            return
        self.time_label.setText("")
        self._set_notice(message)
        self._refresh()

    # --- playback ----------------------------------------------------------------------------------------
    def _ensure_player(self):
        if self._player is None:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
            self._player = QMediaPlayer(self)
            self._audio = QAudioOutput(self)
            self._sink = QVideoSink(self)
            self._player.setAudioOutput(self._audio)
            self._player.setVideoSink(self._sink)
            self._sink.videoFrameChanged.connect(self.view.set_frame)
            self._player.positionChanged.connect(self._on_position)
            self._player.playbackStateChanged.connect(lambda _s: self._refresh_play_icon())
        return self._player

    def _load_player(self, path: str) -> None:
        try:
            player = self._ensure_player()
        except ImportError:
            return
        player.setSource(QUrl.fromLocalFile(path))
        self._seek(0.0)  # shows the first frame

    def _playing(self) -> bool:
        if self._player is None:
            return False
        from PySide6.QtMultimedia import QMediaPlayer
        return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def toggle_play(self) -> None:
        if self._player is None or self._duration <= 0:
            return
        if self._playing():
            self._player.pause()
            return
        head = self.timeline.playhead
        if not (self.timeline.start <= head < self.timeline.end - 0.05):
            self._seek(self.timeline.start)
        self._player.play()

    def _stop(self) -> None:
        if self._player is not None:
            self._player.stop()

    def _seek(self, seconds: float) -> None:
        self.timeline.set_playhead(seconds)
        self._show_time(seconds)
        if self._player is not None:
            self._player.setPosition(int(seconds * 1000))
        if self._has_video and not self._playing():
            self._grab_at = seconds
            if not self._grab_timer.isActive():
                self._grab_timer.start()

    def _grab_frame(self) -> None:
        if self._path is None or self._grab_at < 0:
            return
        if self._grabber is not None and self._grabber.isRunning():
            self._grab_timer.start()  # one at a time; try again shortly with the newest position
            return
        self._grabber = _FrameGrab(self._path, self._grab_at, self)
        self._grabber.grabbed.connect(self._on_grabbed)
        self._grabber.finished.connect(self._on_grab_finished)
        self._grab_at = -1.0
        self._grabber.start()

    def _on_grab_finished(self) -> None:
        worker = self.sender()
        if worker is self._grabber:
            self._grabber = None
        if worker is not None:
            worker.deleteLater()

    def _on_grabbed(self, path: str, _seconds: float, image: QImage) -> None:
        if path == self._path and not self._playing():
            self.view.set_image(image)

    def _on_position(self, ms: int) -> None:
        seconds = ms / 1000
        if self._playing() and seconds >= self.timeline.end:  # loop the chosen part, like a story clip
            self._player.setPosition(int(self.timeline.start * 1000))
            seconds = self.timeline.start
        self.timeline.set_playhead(seconds)
        self._show_time(seconds)

    def _show_time(self, seconds: float) -> None:
        if self._duration > 0:
            self.time_label.setText(f"{format_time(seconds)} / {format_time(self._duration)}")

    def _refresh_play_icon(self) -> None:
        self.play_btn.set_playing(self._playing())

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if event.key() == Qt.Key.Key_Space and not self.length_edit.hasFocus():
            self.toggle_play()
            return
        super().keyPressEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if self._playing():
            self._player.pause()  # leaving the page stops the sound
        super().hideEvent(event)

    # --- submit -------------------------------------------------------------------------------------------------
    def _output_path(self) -> Path:
        ext = self._ext()
        folder = Path(self.folder_edit.text().strip() or os.path.dirname(self._path or "."))
        stem = safe_filename(strip_extension(self.name_field.name(), ext)) or Path(self._path or "clip").stem + "_cut"
        return unique_path(folder, stem, ext, taken=self._reserved)

    def _submit(self) -> None:
        if self._path is None or not self.export_btn.isEnabled():
            return
        output = self._output_path()
        self._reserved.add(str(output))
        task = FastCutTask(self._path, str(output), self.timeline.start, self.timeline.end, self._precise())
        self.task_submitted.emit(task)
        self.action_bar.flash("เพิ่มเข้าคิวแล้ว ✓")
        self.name_field.edited = False
        self.name_field.set_default(self._suggested_stem())


