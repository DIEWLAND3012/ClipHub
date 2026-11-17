"""Feature 5 page: FastCut (ตัดคลิป), cut part of a video or song on a timeline.

Flow: drop or pick a file -> the editor pops in across the whole width: the preview, the play
button and the timeline (sound bars under a trim box). Drag the box to move the part, drag its
edges to resize it, or type the length. Below: length, preview quality, folder and file name.
Export (action bar) sends a FastCutTask to the queue.

Keys: Space plays / pauses; Left / Right or A / D move the trim box by 1 s (Shift: 5 s).
Preview quality Full, 1/2, 1/4, 1/8 plays a smaller proxy copy (made in the background) so slower
computers do not stutter, as in Premiere Pro.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import QRectF, QSettings, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDoubleValidator, QImage, QKeySequence, QPainter, QPainterPath, QPen, QShortcut
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QVBoxLayout,
    QWidget,
)

from core.errors import ClipHubError
from core.ffmpeg import _spawn_kwargs, find_tool, probe_media, run_ffmpeg
from core.naming import safe_filename, strip_extension, unique_path
from tasks.fastcut import MIN_LENGTH, FastCutTask, format_time, is_audio, output_ext, waveform_peaks
from tasks.fastcut import suggest_output_path as suggest_cut_output
from ui import theme
from ui.widgets import (
    ActionBar, DropZone, ElidedLabel, NameField, PageDrop, Segmented, card, field_label, label,
    page_header, pop_in, primary_button,
)

FILE_FILTER = ("วิดีโอและเสียง (*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.flv *.wmv *.ts *.mts *.3gp *.mpg "
               "*.mp3 *.m4a *.aac *.wav *.flac *.ogg *.opus *.wma *.aif *.aiff);;ทุกไฟล์ (*)")
DEFAULT_LENGTH = 20.0  # seconds
PREVIEW_SCALES = {0: 1, 1: 2, 2: 4, 3: 8}  # segment id -> divide the picture by
PREVIEW_LABELS = {0: "Full", 1: "1/2", 2: "1/4", 3: "1/8"}
PROXY_DIR = Path(tempfile.gettempdir()) / "cliphub-preview"
SETTINGS_KEY = "fastcut/preview_scale"
SIDE_BY_SIDE = 860  # editor width from which the settings sit beside the picture


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
    """One still frame via FFmpeg: a paused player does not always show where it seeks."""

    grabbed = Signal(str, float, QImage)

    def __init__(self, path: str, seconds: float, width: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.path, self.seconds, self.width = path, seconds, width

    def run(self) -> None:
        import subprocess
        try:
            cmd = [find_tool("ffmpeg"), "-v", "error", "-ss", f"{self.seconds:.3f}", "-i", self.path,
                   "-frames:v", "1", "-vf", f"scale='min({self.width},iw)':-2", "-f", "image2pipe",
                   "-vcodec", "bmp", "-"]
            data = subprocess.run(cmd, capture_output=True, timeout=15, check=False, **_spawn_kwargs()).stdout
        except Exception:
            return
        image = QImage.fromData(data)
        if not image.isNull():
            self.grabbed.emit(self.path, self.seconds, image)


def proxy_path(source: str, scale: int) -> Path:
    stat = os.stat(source)
    key = hashlib.sha1(f"{source}|{stat.st_size}|{stat.st_mtime}|{scale}".encode()).hexdigest()[:16]
    return PROXY_DIR / f"{key}.mp4"


class _ProxyMaker(QThread):
    """A small, easy-to-play copy of the video for the preview (cached until the app closes)."""

    progress = Signal(str, int)  # source, percent
    ready = Signal(str, int, str)  # source, scale, proxy path

    def __init__(self, source: str, scale: int, duration: float, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.source, self.scale, self.duration = source, scale, duration
        self.cancelled = False

    def run(self) -> None:
        target = proxy_path(self.source, self.scale)
        if not target.is_file():
            PROXY_DIR.mkdir(parents=True, exist_ok=True)
            partial = target.with_suffix(".part")
            args = ["-i", self.source, "-map", "0:v:0", "-map", "0:a:0?",
                    "-vf", f"scale=trunc(iw/{self.scale}/2)*2:-2", "-c:v", "libx264", "-preset", "ultrafast",
                    "-crf", "28", "-g", "15", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k",
                    "-f", "mp4", str(partial)]
            try:
                run_ffmpeg(args, duration=self.duration,
                           on_progress=lambda f: self.progress.emit(self.source, int(f * 100)),
                           is_cancelled=lambda: self.cancelled)
                partial.replace(target)
            except Exception:
                partial.unlink(missing_ok=True)
                return
        self.ready.emit(self.source, self.scale, str(target))


# --- preview -------------------------------------------------------------------------------------------
class VideoView(QWidget):
    """The current video frame, letterboxed in a rounded black panel (a music mark for audio)."""

    MAX_H = 720

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.limit = self.MAX_H  # set by the page so the timeline always fits on screen
        self.setMinimumHeight(140)
        self.setMaximumHeight(self.MAX_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._image = QImage()
        self.audio_only = False
        self.time_text = ""

    def set_time(self, text: str) -> None:
        self.time_text = text
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt API)
        return QSize(640, self.limit)

    def set_limit(self, height: int) -> None:
        self.limit = max(140, min(self.MAX_H, height))
        self.setMaximumHeight(self.limit)
        self.updateGeometry()

    def heightForWidth(self, width: int) -> int:  # noqa: N802 (Qt API)
        return min(self.limit, max(140, width * 9 // 16))

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
        if self.audio_only:
            painter.fillPath(clip, theme.color("accent", 0.12))
            icon = theme.icon_pixmap("music", theme.hex_color("accent"), 56, 1.8)
            painter.drawPixmap(int(rect.center().x() - 28), int(rect.center().y() - 28), icon)
        else:
            painter.fillRect(rect, QColor("#000000"))
            if not self._image.isNull():
                size = self._image.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
                target = QRectF((self.width() - size.width()) / 2, (self.height() - size.height()) / 2,
                                size.width(), size.height())
                painter.drawImage(target, self._image)
        if self.time_text:
            font = painter.font()
            font.setPixelSize(12)
            font.setFamilies([name.strip().strip('"') for name in theme.MONO.split(",")])
            painter.setFont(font)
            width = painter.fontMetrics().horizontalAdvance(self.time_text) + 16
            pill = QRectF(rect.right() - width - 10, rect.bottom() - 32, width, 22)
            path = QPainterPath()
            path.addRoundedRect(pill, 11, 11)
            painter.fillPath(path, QColor(0, 0, 0, 150))
            painter.setPen(QColor("#ffffff"))
            painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, self.time_text)


class PlayButton(QPushButton):
    """Round accent play / pause button, like the app's primary buttons."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(40, 40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # Space belongs to the page shortcut
        self._playing = False

    def set_playing(self, playing: bool) -> None:
        self._playing = playing
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt API)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        accent = theme.color("accent_hover" if self.underMouse() else "accent")
        if not self.isEnabled():
            accent.setAlphaF(0.4)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent)
        painter.drawEllipse(rect)
        icon = theme.icon_pixmap("pause" if self._playing else "play", "#ffffff", 16, 2.2)
        nudge = 0 if self._playing else 1  # a play triangle looks centred a pixel to the right
        painter.drawPixmap(int(rect.center().x() - 8 + nudge), int(rect.center().y() - 8), icon)


# --- timeline --------------------------------------------------------------------------------------------
class TrimTimeline(QWidget):
    """Sound bars with a trim box (move it, drag either handle) and a playhead, in the app's accent."""

    selection_changed = Signal(float, float)  # start, end (seconds)
    seek_requested = Signal(float)
    drag_finished = Signal()

    BAR_W = 3.0
    GAP = 4.0
    HANDLE = 12.0  # width of a handle
    BOX_H = 72.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(int(self.BOX_H) + 12)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.duration = 0.0
        self.peaks: list[float] = []
        self.start = 0.0
        self.end = 0.0
        self.playhead = 0.0
        self._drag = ""      # "move" | "left" | "right"
        self._grab = 0.0     # where in the box the move started (seconds)
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
        self.update()
        if emit:
            self.selection_changed.emit(self.start, self.end)

    def move_by(self, seconds: float) -> None:
        self.set_selection(self.start + seconds, self.end + seconds)

    def set_playhead(self, seconds: float) -> None:
        self.playhead = seconds
        self.update()

    # --- geometry ------------------------------------------------------------------------------
    def _track(self) -> QRectF:
        return QRectF(self.HANDLE, (self.height() - self.BOX_H) / 2, self.width() - 2 * self.HANDLE, self.BOX_H)

    def _x(self, seconds: float) -> float:
        track = self._track()
        return track.left() + (seconds / self.duration) * track.width() if self.duration else track.left()

    def _t(self, x: float) -> float:
        track = self._track()
        if not self.duration or track.width() <= 0:
            return 0.0
        return max(0.0, min(self.duration, (x - track.left()) / track.width() * self.duration))

    def _box(self) -> QRectF:
        """The chosen part, with the handles outside it (like an iOS trim bar)."""
        track = self._track()
        left, right = self._x(self.start), self._x(self.end)
        if right - left < 8:  # a short part of a long file stays easy to see and grab
            middle = (left + right) / 2
            left, right = middle - 4, middle + 4
        return QRectF(left - self.HANDLE, track.top(), right - left + 2 * self.HANDLE, track.height())

    # --- painting ---------------------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if self.duration <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = self._track()
        box = self._box()
        inner = box.adjusted(self.HANDLE, 3, -self.HANDLE, -3)
        accent = theme.color("accent")
        dim = theme.color("text3")

        # the chosen part: a soft accent wash, a little stronger where it has already played
        painter.fillRect(inner, theme.color("accent", 0.14))
        if self.start <= self.playhead <= self.end:
            painter.fillRect(QRectF(inner.left(), inner.top(), self._x(self.playhead) - inner.left(),
                                    inner.height()), theme.color("accent", 0.22))

        count = max(1, int(track.width() // (self.BAR_W + self.GAP)))
        for index in range(count):
            x = track.left() + index * (self.BAR_W + self.GAP) + self.GAP / 2
            level = self._level(index / max(count - 1, 1))
            bar_h = 8 + level * (track.height() * 0.6 - 8)
            rect = QRectF(x, track.center().y() - bar_h / 2, self.BAR_W, bar_h)
            painter.fillPath(self._pill(rect), accent if inner.left() <= x <= inner.right() else dim)

        # frame: accent border top and bottom, solid handles at both ends with a white grip
        frame = QPainterPath()
        frame.addRoundedRect(box, 10, 10)
        hole = QPainterPath()
        hole.addRect(inner)
        painter.fillPath(frame.subtracted(hole), accent)
        for grip_x in (box.left() + self.HANDLE / 2, box.right() - self.HANDLE / 2):
            painter.fillPath(self._pill(QRectF(grip_x - 1.25, box.center().y() - 11, 2.5, 22)), QColor("#ffffff"))

        if self.start <= self.playhead <= self.end:
            x = self._x(self.playhead)
            pen = QPen(theme.color("text"))
            pen.setWidthF(2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(int(x), int(inner.top() + 4), int(x), int(inner.bottom() - 4))

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
        if box.left() - 4 <= x <= box.left() + self.HANDLE:
            return "left"
        if box.right() - self.HANDLE <= x <= box.right() + 4:
            return "right"
        if box.left() < x < box.right():
            return "move"
        return "outside"

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt API)
        x = event.position().x()
        if not self._drag:
            shape = {"left": Qt.CursorShape.SizeHorCursor, "right": Qt.CursorShape.SizeHorCursor,
                     "move": Qt.CursorShape.OpenHandCursor}.get(self._hit(x), Qt.CursorShape.PointingHandCursor)
            self.setCursor(shape)
            return
        self._moved = True
        t = self._t(x)
        if self._drag == "move":
            length = self.end - self.start
            self.set_selection(t - self._grab, t - self._grab + length)
            self.seek_requested.emit(self.start)
        elif self._drag == "left":
            self.set_selection(max(0.0, min(t, self.end - MIN_LENGTH)), self.end)
            self.seek_requested.emit(self.start)
        elif self._drag == "right":
            end = min(self.duration, max(t, self.start + MIN_LENGTH))
            self.set_selection(self.start, end)
            self.seek_requested.emit(max(self.start, end - 1.5))  # show where it ends

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
        clicked_inside = self._drag == "move" and not self._moved
        if self._drag and not clicked_inside:
            self.drag_finished.emit()
        if clicked_inside:  # a click inside the box moves the playhead there
            self.seek_requested.emit(min(max(self._t(event.position().x()), self.start), self.end))
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
        self._player = None      # QMediaPlayer, made on first use
        self._source = ""        # what the player plays: the file or its proxy
        self._proxy: _ProxyMaker | None = None
        self._grab_at = -1.0
        self._grabber: _FrameGrab | None = None
        self._grab_timer = QTimer(self)
        self._grab_timer.setSingleShot(True)
        self._grab_timer.setInterval(90)  # while dragging, at most ~10 still frames a second
        self._grab_timer.timeout.connect(self._grab_frame)
        self._build()
        self._bind_keys()
        self._refresh()
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._shutdown)

    # --- layout ---------------------------------------------------------------------------------
    def _build(self) -> None:
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(16)
        col.addLayout(page_header("ตัดคลิป"))
        self.dropzone = DropZone("ลากไฟล์วิดีโอหรือเสียงมาวางที่นี่", FILE_FILTER, "เลือกไฟล์วิดีโอหรือเสียง")
        self.dropzone.setMinimumHeight(300)
        self.dropzone.file_chosen.connect(self.set_input_file)
        self.dropzone.rejected.connect(self._set_notice)
        col.addWidget(self.dropzone)
        PageDrop(self, self.set_input_file, self._set_notice)
        self.notice = label("", "ErrorText", wrap=True)
        self.notice.hide()
        col.addWidget(self.notice)

        self.editor_card = self._build_editor()     # full width at every window size
        col.addWidget(self.editor_card)
        col.addStretch(1)
        self.editor_card.hide()

        self.action_bar = ActionBar()
        self.export_btn = self.action_bar.add_button(primary_button("Export", "fastcut"))
        self.export_btn.clicked.connect(self._submit)

    def _build_editor(self) -> QFrame:
        frame, layout = card(spacing=8, margins=(14, 14, 14, 12))
        # Wide: the picture fills its column (no black bars) and the settings use the room beside it.
        # Narrow: picture, then the timeline, then the settings, so the timeline is never pushed down.
        top = QHBoxLayout()
        top.setSpacing(18)
        self.view = VideoView()
        top.addWidget(self.view, 3)
        self.side = QWidget()
        self.side_layout = QVBoxLayout(self.side)
        self.side_layout.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self.side, 2)
        layout.addLayout(top)
        self.settings = self._build_settings()
        self.side_layout.addWidget(self.settings)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.play_btn = PlayButton()
        self.play_btn.setToolTip("เล่น / หยุด (Space)")
        self.play_btn.clicked.connect(self.toggle_play)
        row.addWidget(self.play_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self.timeline = TrimTimeline()
        self.timeline.selection_changed.connect(self._on_selection)
        self.timeline.seek_requested.connect(self._seek)
        self.timeline.drag_finished.connect(lambda: self._seek(self.timeline.start))  # play from the start
        row.addWidget(self.timeline, 1)
        layout.addLayout(row)
        self.below = QVBoxLayout()
        self.below.setContentsMargins(0, 8, 0, 0)
        layout.addLayout(self.below)
        return frame

    def _place_settings(self) -> None:
        wide = self.editor_card.width() >= SIDE_BY_SIDE
        target = self.side_layout if wide else self.below
        if self.settings.parentWidget() is not (self.side if wide else self.editor_card):
            target.addWidget(self.settings)
        self.side.setVisible(wide)

    def _build_settings(self) -> QWidget:
        frame = QWidget()
        left = QVBoxLayout(frame)
        left.setContentsMargins(4, 0, 2, 0)
        left.setSpacing(6)
        file_row = QHBoxLayout()
        file_row.setSpacing(10)
        self.file_name = ElidedLabel(mode=Qt.TextElideMode.ElideMiddle)
        self.file_name.setObjectName("FileTitle")
        change = QPushButton("เปลี่ยนไฟล์")
        change.setCursor(Qt.CursorShape.PointingHandCursor)
        change.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        change.clicked.connect(lambda: self.dropzone.browse())
        file_row.addWidget(self.file_name, 1)
        file_row.addWidget(change)
        left.addLayout(file_row)
        left.addSpacing(4)
        left.addLayout(_step(1, "ความยาวที่ตัด"))
        length_box = QFrame()
        length_box.setObjectName("Control")
        length_box.setFixedHeight(theme.CONTROL_HEIGHT)
        length_row = QHBoxLayout(length_box)
        length_row.setContentsMargins(6, 0, 14, 0)
        length_row.setSpacing(6)
        self.minus_btn = QPushButton()
        self.plus_btn = QPushButton()
        for button, icon, step in ((self.minus_btn, "minus", -1), (self.plus_btn, "plus", 1)):
            button.setObjectName("StepButton")
            button.setFixedSize(34, 34)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setAutoRepeat(True)
            button.setProperty("icon_key", icon)
            button.clicked.connect(lambda _c=False, s=step: self._nudge_length(s))
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
        left.addWidget(length_box)
        left.addLayout(_step(2, "คุณภาพพรีวิว"))
        left.addWidget(self._build_preview_seg())
        left.addLayout(_step(3, "บันทึกที่"))
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("เลือกโฟลเดอร์ปลายทาง")
        self._folder_action = self.folder_edit.addAction(theme.themed_icon("folder"),
                                                         QLineEdit.ActionPosition.LeadingPosition)
        self.folder_edit.textChanged.connect(lambda _t: self._refresh())
        folder_btn = QPushButton("เลือก…")
        folder_btn.setObjectName("FieldButton")
        folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        folder_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        folder_btn.clicked.connect(self.choose_folder)
        folder_row = QHBoxLayout()
        folder_row.setSpacing(10)
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(folder_btn)
        left.addLayout(folder_row)
        left.addLayout(_step(4, "ชื่อไฟล์"))
        self.name_field = NameField()
        self.name_field.changed.connect(self._refresh_summary)
        left.addWidget(self.name_field)
        left.addStretch(1)
        theme.on_change(self._refresh_icons)
        return frame

    def _build_preview_seg(self) -> Segmented:
        self.preview_seg = Segmented()
        self.preview_seg.setFixedHeight(theme.CONTROL_HEIGHT)
        for seg_id, text in PREVIEW_LABELS.items():
            self.preview_seg.add(text, seg_id).setFocusPolicy(Qt.FocusPolicy.NoFocus)
        saved = int(QSettings("ClipHUB", "ClipHUB").value(SETTINGS_KEY, 0) or 0)
        self.preview_seg.group.button(saved if saved in PREVIEW_SCALES else 0).setChecked(True)
        self.preview_seg.group.idToggled.connect(lambda seg_id, on: on and self._on_preview_scale(seg_id))
        return self.preview_seg

    def _refresh_icons(self) -> None:
        for button in (self.minus_btn, self.plus_btn):
            button.setIcon(theme.themed_icon(button.property("icon_key"), "text", 14, 2.4))
        self._folder_action.setIcon(theme.themed_icon("folder"))
        for name in ("play_btn", "timeline", "view"):  # may run before they exist
            widget = getattr(self, name, None)
            if widget is not None:
                widget.update()

    def _bind_keys(self) -> None:
        """Shortcuts work while this page shows; a text field keeps its own keys while typing."""
        bindings = [
            ("Space", self.toggle_play),
            ("Left", lambda: self._move_box(-1)), ("A", lambda: self._move_box(-1)),
            ("Right", lambda: self._move_box(1)), ("D", lambda: self._move_box(1)),
            ("Shift+Left", lambda: self._move_box(-5)), ("Shift+A", lambda: self._move_box(-5)),
            ("Shift+Right", lambda: self._move_box(5)), ("Shift+D", lambda: self._move_box(5)),
        ]
        for keys, handler in bindings:
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(handler)

    # --- state ------------------------------------------------------------------------------------------
    def _ext(self) -> str:
        return output_ext(self._path or "a.mp4", precise=True)

    def _scale(self) -> int:
        return PREVIEW_SCALES.get(self.preview_seg.group.checkedId(), 1)

    def _set_notice(self, message: str) -> None:
        self.notice.setText(message)
        self.notice.setVisible(bool(message))

    def _refresh(self) -> None:
        ready = self._path is not None and self._duration > 0
        self.preview_seg.setEnabled(self._has_video)
        self.name_field.set_extension(self._ext())
        self.export_btn.setEnabled(ready and bool(self.folder_edit.text().strip()))
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        if not self._path or self._duration <= 0:
            self.action_bar.set_summary("")
            return
        self.action_bar.set_summary(f"{format_time(self.timeline.start)} – {format_time(self.timeline.end)}")

    def _on_selection(self, start: float, end: float) -> None:
        if not self.length_edit.hasFocus():
            self.length_edit.setText(f"{end - start:.1f}".rstrip("0").rstrip("."))
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

    def _move_box(self, seconds: float) -> None:
        if self._duration <= 0 or self.editor_card.isHidden():
            return
        self.timeline.move_by(seconds)
        self._seek(self.timeline.start)

    def choose_folder(self) -> None:
        start = self.folder_edit.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "เลือกโฟลเดอร์ปลายทาง", start)
        if chosen:
            self.folder_edit.setText(chosen)
            self.name_field.set_default(self._suggested_stem())

    def _suggested_stem(self) -> str:
        return suggest_cut_output(self._path, self.folder_edit.text().strip(), True, self._reserved).stem

    # --- file -----------------------------------------------------------------------------------------------
    def set_input_file(self, path: str) -> None:
        self._set_notice("")
        self._stop()
        self._cancel_proxy()
        self._path = path
        self._duration = 0.0
        self._has_video = not is_audio(path)
        self.dropzone.start_dir = os.path.dirname(path) or self.dropzone.start_dir
        self.dropzone.hide()
        self.file_name.set_full_text(os.path.basename(path))
        self.view.set_time("")
        self.view.audio_only = not self._has_video
        self.view.clear()
        self.timeline.set_media(0.0, [])
        if not self.folder_edit.text().strip():
            self.folder_edit.setText(os.path.dirname(path))
        self.name_field.edited = False
        self.name_field.set_default(self._suggested_stem())
        if self.editor_card.isHidden():
            pop_in(self.editor_card)
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
        self._play_source(path)
        self._seek(0.0)
        if has_video and self._scale() > 1:
            self._make_proxy()
        self._refresh()

    def _on_failed(self, path: str, message: str) -> None:
        if path != self._path:
            return
        self.view.set_time("")
        self._set_notice(message)
        self._refresh()

    # --- preview quality (proxy) -----------------------------------------------------------------------------
    def _on_preview_scale(self, seg_id: int) -> None:
        QSettings("ClipHUB", "ClipHUB").setValue(SETTINGS_KEY, seg_id)
        if not self._path or not self._has_video or self._duration <= 0:
            return
        self._cancel_proxy()
        if self._scale() == 1:
            self._play_source(self._path, keep_position=True)
        else:
            self._make_proxy()
        self._seek(self.timeline.playhead)

    def _make_proxy(self) -> None:
        maker = _ProxyMaker(self._path, self._scale(), self._duration, self)
        maker.progress.connect(self._on_proxy_progress)
        maker.ready.connect(self._on_proxy_ready)
        maker.finished.connect(maker.deleteLater)
        self._proxy = maker
        maker.start()

    def _cancel_proxy(self) -> None:
        if self._proxy is not None:
            try:
                self._proxy.cancelled = True
            except RuntimeError:
                pass
            self._proxy = None

    def _on_proxy_progress(self, source: str, percent: int) -> None:
        if source == self._path and not self._playing():
            self.view.set_time(f"{PREVIEW_LABELS[self.preview_seg.group.checkedId()]} {percent}%")

    def _on_proxy_ready(self, source: str, scale: int, proxy: str) -> None:
        if source != self._path or scale != self._scale():
            return
        self._play_source(proxy, keep_position=True)
        self._show_time(self.timeline.playhead)

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
            self._player.playbackStateChanged.connect(lambda _s: self.play_btn.set_playing(self._playing()))
        return self._player

    def _play_source(self, path: str, keep_position: bool = False) -> None:
        try:
            player = self._ensure_player()
        except ImportError:
            return
        if path == self._source:
            return
        playing = self._playing()
        position = player.position()
        self._source = path
        player.setSource(QUrl.fromLocalFile(path))
        if keep_position:
            player.setPosition(position)
            if playing:
                player.play()

    def _playing(self) -> bool:
        if self._player is None:
            return False
        from PySide6.QtMultimedia import QMediaPlayer
        return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def toggle_play(self) -> None:
        if self._player is None or self._duration <= 0 or self.editor_card.isHidden():
            return
        if self._playing():
            self._player.pause()
            return
        if not (self.timeline.start <= self.timeline.playhead < self.timeline.end - 0.05):
            self._seek(self.timeline.start)
        self._player.play()

    def _stop(self) -> None:
        if self._player is not None:
            self._player.stop()
            self._source = ""

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
        if self._grabber is not None:
            self._grab_timer.start()  # one at a time; try again shortly with the newest position
            return
        self._grabber = _FrameGrab(self._source or self._path, self._grab_at, 1920 // self._scale(), self)
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
        if path in (self._path, self._source) and not self._playing():
            self.view.set_image(image)

    def _on_position(self, ms: int) -> None:
        seconds = ms / 1000
        if self._playing() and seconds >= self.timeline.end:  # loop the chosen part
            self._player.setPosition(int(self.timeline.start * 1000))
            seconds = self.timeline.start
        if self._playing():
            self.timeline.set_playhead(seconds)
            self._show_time(seconds)

    def _show_time(self, seconds: float) -> None:
        if self._duration > 0:
            self.view.set_time(f"{format_time(seconds)} / {format_time(self._duration)}")

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().resizeEvent(event)
        self._fit_preview()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().showEvent(event)
        self._fit_preview()

    def _fit_preview(self) -> None:
        """Preview as tall as the window allows while the whole timeline stays on screen."""
        self._place_settings()
        window = self.window()
        if window is not None:
            self.view.set_limit(window.height() - 300)

    def hideEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if self._playing():
            self._player.pause()  # leaving the page stops the sound
        super().hideEvent(event)

    def _shutdown(self) -> None:
        self._cancel_proxy()
        self._stop()
        for worker in list(self._loaders):
            worker.wait(2000)
        shutil.rmtree(PROXY_DIR, ignore_errors=True)

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
        # Always frame-accurate: a short clip re-encodes in seconds (on the GPU when there is one).
        task = FastCutTask(self._path, str(output), self.timeline.start, self.timeline.end, precise=True)
        self.task_submitted.emit(task)
        self.action_bar.flash("เพิ่มเข้าคิวแล้ว ✓")
        self.name_field.edited = False
        self.name_field.set_default(self._suggested_stem())
