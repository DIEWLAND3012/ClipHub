"""Feature 2 page: converter for video and audio files (owner: member 2).

Flow: drop or pick a file -> its details appear, with the container shown largest ->
1 convert to video or audio, 2 file format, 3 codec and quality, 4 output name -> start.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt, QThread, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QListView,
    QPushButton, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from core.errors import ClipHubError
from core.ffmpeg import available_encoders, probe_media
from core.naming import safe_filename, strip_extension
from tasks.converter import (
    AUDIO_BITRATES, AUDIO_CODECS, AUDIO_CODECS_FOR, AUDIO_TARGETS, COPY, FRAME_RATES, LOSSLESS_AUDIO,
    RESOLUTIONS, SEPARATOR_FORMATS, VIDEO_CODECS, VIDEO_CODECS_FOR, VIDEO_TARGETS,
    ConverterTask, ConvertSettings, audio_sidecar_path, copy_problem, describe_conversion, describe_settings,
    suggest_output_path,
)
from ui import theme
from ui.widgets import (
    ActionBar, Columns, DropZone, ElidedLabel, NameField, PageDrop, Segmented, card, fade_in, field_label, label,
    page_header, pop_in, primary_button,
)

_VIDEO_EXTS = "*.mp4 *.mkv *.avi *.mov *.webm *.m4v *.flv *.wmv *.ts *.mts *.3gp"
_AUDIO_EXTS = "*.mp3 *.wav *.aac *.flac *.m4a *.ogg *.opus *.wma *.aiff *.alac"
FILE_FILTERS = ";;".join([
    f"ไฟล์วิดีโอและเสียง ({_VIDEO_EXTS} {_AUDIO_EXTS})",
    f"ไฟล์วิดีโอ ({_VIDEO_EXTS})",
    f"ไฟล์เสียง ({_AUDIO_EXTS})",
    "ทุกไฟล์ (*)",
])
FORMAT_LABELS = {"mp4": "MP4", "mov": "MOV", "mkv": "MKV", "webm": "WebM", "avi": "AVI",
                 "mp3": "MP3", "m4a": "M4A", "wav": "WAV", "flac": "FLAC", "opus": "Opus", "aac": "AAC"}
FORMAT_TIPS = {
    "mp4": "เล่นได้ทุกที่", "mov": "สำหรับ Final Cut / iMovie / Apple", "mkv": "เก็บได้ทุกโค้ดเดก",
    "webm": "สำหรับเว็บ", "avi": "สำหรับโปรแกรมรุ่นเก่า", "mp3": "เล่นได้ทุกที่",
    "m4a": "AAC คุณภาพดีกว่า MP3 ที่ขนาดเท่ากัน", "wav": "ไม่บีบอัด ไฟล์ใหญ่", "flac": "Lossless ไฟล์เล็กกว่า WAV",
    "opus": "คุณภาพสูงที่บิตเรตต่ำ", "aac": "AAC แบบไฟล์ดิบ (ADTS)",
}
CODEC_TIPS = {
    "h264": "เข้ากันได้กว้างที่สุด", "h265": "ไฟล์เล็กกว่า H.264 ราวครึ่งหนึ่ง แต่แปลงช้ากว่า",
    "av1": "ไฟล์เล็กที่สุด แต่อุปกรณ์เก่าอาจเล่นไม่ได้", "vp9": "โค้ดเดกของเว็บ / YouTube",
    "prores": "สำหรับตัดต่อ ไฟล์ใหญ่มาก", COPY: "ไม่เข้ารหัสใหม่ เร็วมากและไม่เสียคุณภาพ",
}
# Friendly names for what ffprobe reports.
CODEC_NAMES = {
    "h264": "H.264", "hevc": "H.265 (HEVC)", "av1": "AV1", "vp9": "VP9", "vp8": "VP8", "prores": "ProRes",
    "mpeg4": "MPEG-4", "mjpeg": "MJPEG", "mpeg2video": "MPEG-2", "aac": "AAC", "mp3": "MP3", "opus": "Opus",
    "vorbis": "Vorbis", "flac": "FLAC", "alac": "ALAC", "ac3": "AC-3", "eac3": "E-AC-3",
    "pcm_s16le": "PCM 16-bit", "pcm_s24le": "PCM 24-bit", "pcm_f32le": "PCM 32-bit float",
}
CHANNELS = {1: "โมโน", 2: "สเตอริโอ", 6: "5.1", 8: "7.1"}
LOSSY_IN_VIDEO = ("aac", "mp3", "opus")


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


def format_bitrate(bps: int) -> str:
    if bps <= 0:
        return "—"
    return f"{bps / 1_000_000:.1f} Mbps" if bps >= 1_000_000 else f"{bps / 1000:.0f} kbps"


def codec_name(codec: str, profile: str = "") -> str:
    name = CODEC_NAMES.get(codec, codec.upper() or "—")
    if profile and profile.lower() not in ("unknown", "lc") and codec in ("h264", "hevc"):
        name += f" · {profile}"
    return name


class _ProbeThread(QThread):
    """Reads everything about the chosen file so the GUI thread never touches the disk."""

    probed = Signal(int, object)  # serial, {"info": dict | None, "error": str | None}

    def __init__(self, serial: int, path: str) -> None:
        super().__init__()
        self._serial = serial
        self._path = path

    def run(self) -> None:
        result: dict[str, object] = {"info": None, "error": None}
        try:
            info = probe_media(self._path)
            if not info["size"]:
                info["size"] = Path(self._path).stat().st_size
            if info["video"] is None and info["audio"] is None:
                raise ClipHubError("ไฟล์นี้ไม่มีภาพหรือเสียงให้แปลง")
            result["info"] = info
        except ClipHubError as exc:
            result["error"] = str(exc)
        except OSError as exc:
            result["error"] = f"อ่านไฟล์ไม่ได้: {exc.strerror or exc}"
        self.probed.emit(self._serial, result)


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
        self._animation.setDuration(220)
        self._animation.setEasingCurve(QEasingCurve.Type.OutBack)
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
        mix = max(0.0, min(self._offset, 1.0))
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
        knob = QRectF(3.0 + 18.0 * self._offset, 3.0, 22, 22)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 40))
        painter.drawEllipse(knob.translated(0, 1))
        painter.setBrush(theme.color("knob"))
        painter.drawEllipse(knob)


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


def _step(number: int, text: str) -> tuple[QHBoxLayout, QLabel]:
    badge = label(str(number), "StepNumber")
    badge.setFixedSize(20, 20)
    badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
    title = field_label(text)
    row = QHBoxLayout()
    row.setSpacing(8)
    row.addWidget(badge)
    row.addWidget(title)
    row.addStretch(1)
    return row, title


class ConverterPage(QWidget):
    task_submitted = Signal(object)  # ConverterTask

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ConverterPage")
        self._input_path: str | None = None
        self._info: dict[str, Any] | None = None
        self._serial = 0
        self._probes: list[_ProbeThread] = []
        self._reserved: set[Path] = set()  # outputs of queued jobs that are not on disk yet
        self._last_dir = str(Path.home())
        self._encoders = available_encoders()
        self._tile_columns = 4

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)
        root.addLayout(page_header("แปลงไฟล์"))
        self.dropzone = DropZone("ลากไฟล์วิดีโอหรือเสียงมาวางที่นี่", FILE_FILTERS, "เลือกไฟล์วิดีโอหรือเสียง")
        self.dropzone.setMinimumHeight(300)
        self.dropzone.file_chosen.connect(self.set_input_file)
        self.dropzone.rejected.connect(self._set_notice)
        root.addWidget(self.dropzone)
        PageDrop(self, self.set_input_file, self._set_notice)  # drop anywhere, also to replace the file
        self.notice_label = label("", "ErrorText", wrap=True)
        self.notice_label.hide()
        root.addWidget(self.notice_label)
        # The file on the left, what to turn it into on the right.
        self.columns = Columns(breakpoint=820, stretch=(5, 6))
        self.file_card = self._build_file_card()
        self.file_card.hide()
        self.columns.left.addWidget(self.file_card)
        self.columns.left.addStretch(1)
        self.options_card = self._build_options_card()
        self.options_card.hide()
        self.columns.right.addWidget(self.options_card)
        self.columns.right.addStretch(1)
        root.addWidget(self.columns)
        self._build_actions()
        root.addStretch(1)

        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._wait_probes)
        self._on_format_changed()

    # ----- file card ------------------------------------------------------------------
    def _build_file_card(self) -> QFrame:
        frame, layout = card(spacing=14, margins=(20, 18, 20, 20))
        top = QHBoxLayout()
        top.setSpacing(16)
        self.format_badge = label("—", "FormatBadge")
        self.format_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.format_badge.setMinimumSize(96, 76)
        texts = QVBoxLayout()
        texts.setSpacing(3)
        self.file_name_label = ElidedLabel(mode=Qt.TextElideMode.ElideMiddle)
        self.file_name_label.setObjectName("FileTitle")
        self.file_kind_label = ElidedLabel()
        self.file_kind_label.setObjectName("MetaRow")
        texts.addStretch(1)
        texts.addWidget(self.file_name_label)
        texts.addWidget(self.file_kind_label)
        texts.addStretch(1)
        change = QPushButton("เปลี่ยนไฟล์")
        change.setCursor(Qt.CursorShape.PointingHandCursor)
        change.clicked.connect(lambda: self.dropzone.browse())
        top.addWidget(self.format_badge)
        top.addLayout(texts, 1)
        top.addWidget(change, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(top)

        self.tiles_grid = QGridLayout()
        self.tiles_grid.setHorizontalSpacing(8)
        self.tiles_grid.setVerticalSpacing(8)
        self.tiles: dict[str, tuple[QFrame, ElidedLabel]] = {}
        for key, text in (("duration", "ความยาว"), ("size", "ขนาด"), ("bitrate", "บิตเรตรวม"),
                          ("vcodec", "วิดีโอ"), ("resolution", "ความละเอียด"), ("fps", "เฟรมเรต"),
                          ("acodec", "เสียง"), ("audio", "รายละเอียดเสียง")):
            self.tiles[key] = _info_tile(text)
        self._layout_tiles(4)
        layout.addLayout(self.tiles_grid)
        self.file_error_label = label("", "ErrorText", wrap=True)
        self.file_error_label.hide()
        layout.addWidget(self.file_error_label)
        return frame

    def _layout_tiles(self, columns: int) -> None:
        self._tile_columns = columns
        for tile, _value in self.tiles.values():
            self.tiles_grid.removeWidget(tile)
        shown = [tile for tile, _value in self.tiles.values() if not tile.isHidden()]
        for index, tile in enumerate(shown):
            row, column = divmod(index, columns)
            self.tiles_grid.addWidget(tile, row, column)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().resizeEvent(event)
        columns = 4 if self.file_card.width() >= 600 else 2
        if columns != self._tile_columns:
            self._layout_tiles(columns)

    # ----- options card ---------------------------------------------------------------
    def _build_options_card(self) -> QFrame:
        frame, layout = card(spacing=8, margins=(22, 16, 22, 16))

        row, _ = _step(1, "แปลงเป็น")
        layout.addLayout(row)
        self.kind_seg = Segmented()
        self.kind_seg.setFixedHeight(theme.CONTROL_HEIGHT)
        self.video_button = self.kind_seg.add("วิดีโอ", 0, icon="video")
        self.audio_button = self.kind_seg.add("เสียงอย่างเดียว", 1, icon="audio")
        self.video_button.setChecked(True)
        self.kind_seg.group.idToggled.connect(lambda _i, on: on and self._on_kind_changed())
        layout.addWidget(self.kind_seg)

        layout.addSpacing(6)
        row, _ = _step(2, "รูปแบบไฟล์")
        layout.addLayout(row)
        self.video_formats = self._format_seg(VIDEO_TARGETS)
        self.audio_formats = self._format_seg(AUDIO_TARGETS)
        self.format_stack = QStackedWidget()
        self.format_stack.addWidget(self.video_formats)
        self.format_stack.addWidget(self.audio_formats)
        self.format_stack.setFixedHeight(theme.CONTROL_HEIGHT)
        layout.addWidget(self.format_stack)

        layout.addSpacing(6)
        row, self.step3_title = _step(3, "โค้ดเดก")
        layout.addLayout(row)
        self.settings_stack = QStackedWidget()
        self.settings_stack.addWidget(self._build_video_settings())
        self.settings_stack.addWidget(self._build_audio_settings())
        layout.addWidget(self.settings_stack)
        self.copy_note = label("", "Note", wrap=True)
        self.copy_note.hide()
        layout.addWidget(self.copy_note)

        layout.addSpacing(6)
        row, _ = _step(4, "ชื่อไฟล์")
        layout.addLayout(row)
        self.name_field = NameField()
        self.name_field.changed.connect(self._refresh_hint)
        layout.addWidget(self.name_field)
        self.separator_row = QWidget()
        sep = QHBoxLayout(self.separator_row)
        sep.setContentsMargins(0, 4, 0, 0)
        sep.setSpacing(12)
        sep.addWidget(label("แยกไฟล์เสียงออกมาด้วย", "ToggleTitle"), 1)
        self.audio_format_combo = _Combo()
        self.audio_format_combo.setObjectName("FmtCombo")
        for ext in SEPARATOR_FORMATS:
            self.audio_format_combo.addItem(ext.upper(), ext)
        self.audio_format_combo.currentIndexChanged.connect(self._refresh)
        self.extract_switch = SwitchCheckBox("แยกไฟล์เสียงออกมาด้วย")
        self.extract_switch.toggled.connect(self._refresh)
        sep.addWidget(self.audio_format_combo)
        sep.addWidget(self.extract_switch)
        layout.addWidget(self.separator_row)
        return frame

    def _format_seg(self, formats: tuple[str, ...]) -> Segmented:
        seg = Segmented()
        for index, fmt in enumerate(formats):
            seg.add(FORMAT_LABELS[fmt], index, tooltip=FORMAT_TIPS.get(fmt, ""))
        seg.group.button(0).setChecked(True)
        seg.group.idToggled.connect(lambda _i, on: on and self._on_format_changed())
        return seg

    def _build_video_settings(self) -> QWidget:
        box = QWidget()
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(10)
        column.addWidget(field_label("โค้ดเดกวิดีโอ"))
        self.vcodec_seg = Segmented()
        self.vcodec_seg.setFixedHeight(theme.CONTROL_HEIGHT)
        self.vcodec_seg.group.idToggled.connect(lambda _i, on: on and self._refresh())
        column.addWidget(self.vcodec_seg)

        self.resolution_combo = _Combo()
        self.resolution_combo.addItem("ขนาดเดิม", None)
        for height in RESOLUTIONS:
            self.resolution_combo.addItem(f"{height}p", height)
        self.fps_combo = _Combo()
        self.fps_combo.addItem("เฟรมเรตเดิม", None)
        for fps in FRAME_RATES:
            self.fps_combo.addItem(f"{fps} fps", fps)
        for combo in (self.resolution_combo, self.fps_combo):
            combo.currentIndexChanged.connect(self._refresh)
        self.video_tuning = QWidget()
        grid = QGridLayout(self.video_tuning)
        grid.setContentsMargins(0, 4, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        grid.addWidget(field_label("ความละเอียด"), 0, 0)
        grid.addWidget(field_label("เฟรมเรต"), 0, 1)
        grid.addWidget(self.resolution_combo, 1, 0)
        grid.addWidget(self.fps_combo, 1, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        column.addWidget(self.video_tuning)

        self.acodec_combo = _Combo()
        self.acodec_combo.currentIndexChanged.connect(self._refresh)
        self.vbitrate_combo = _Combo()  # audio bitrate inside a video file
        for kbps in AUDIO_BITRATES:
            self.vbitrate_combo.addItem(f"{kbps} kbps", kbps)
        self.vbitrate_combo.setCurrentIndex(self.vbitrate_combo.findData(192))
        self.vbitrate_combo.currentIndexChanged.connect(self._refresh)
        audio_row = QGridLayout()
        audio_row.setContentsMargins(0, 4, 0, 0)
        audio_row.setHorizontalSpacing(10)
        audio_row.setVerticalSpacing(6)
        audio_row.addWidget(field_label("เสียงในวิดีโอ"), 0, 0)
        audio_row.addWidget(field_label("บิตเรตเสียง"), 0, 1)
        audio_row.addWidget(self.acodec_combo, 1, 0)
        audio_row.addWidget(self.vbitrate_combo, 1, 1)
        column.addLayout(audio_row)
        return box

    def _build_audio_settings(self) -> QWidget:
        box = QWidget()
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)
        self.abitrate_combo = _Combo()
        self.abitrate_combo.currentIndexChanged.connect(self._refresh)
        column.addWidget(self.abitrate_combo)
        self.lossless_note = label("", "Note", wrap=True)
        column.addWidget(self.lossless_note)
        column.addStretch(1)
        return box

    def _build_actions(self) -> None:
        """Summary + start button; MainWindow shows this bar in the same place on every page."""
        self.action_bar = ActionBar()
        self.start_button = self.action_bar.add_button(primary_button("เริ่มแปลงไฟล์", "convert"))
        self.start_button.clicked.connect(self._submit)

    # ----- current choices ------------------------------------------------------------
    def is_audio_target(self) -> bool:
        return self.audio_button.isChecked()

    def target_ext(self) -> str:
        if self.is_audio_target():
            return AUDIO_TARGETS[max(self.audio_formats.group.checkedId(), 0)]
        return VIDEO_TARGETS[max(self.video_formats.group.checkedId(), 0)]

    def _selected_vcodec(self) -> str:
        button = self.vcodec_seg.group.checkedButton()
        return str(button.property("codec")) if button is not None else ""

    def settings(self) -> ConvertSettings:
        ext = self.target_ext()
        if ext in VIDEO_TARGETS:
            acodec = str(self.acodec_combo.currentData() or "")
            return ConvertSettings(
                # Always the highest quality: smaller presets visibly broke up the picture.
                video_codec=self._selected_vcodec(), audio_codec=acodec, quality="high",
                height=self.resolution_combo.currentData(), fps=self.fps_combo.currentData(),
                audio_bitrate=self.vbitrate_combo.currentData() if acodec in LOSSY_IN_VIDEO else None,
            )
        choice = self.abitrate_combo.currentData()
        if choice == COPY:
            return ConvertSettings(audio_codec=COPY)
        return ConvertSettings(audio_bitrate=choice if isinstance(choice, int) else None)

    # ----- rebuilding options when the format changes ---------------------------------
    def _on_kind_changed(self) -> None:
        audio = self.is_audio_target()
        self.format_stack.setCurrentIndex(1 if audio else 0)
        self.settings_stack.setCurrentIndex(1 if audio else 0)
        # A stacked widget is as tall as its tallest page; let only the visible page count.
        for index in range(self.settings_stack.count()):
            page = self.settings_stack.widget(index)
            policy = QSizePolicy.Policy.Preferred if index == self.settings_stack.currentIndex() else QSizePolicy.Policy.Ignored
            page.setSizePolicy(QSizePolicy.Policy.Preferred, policy)
        self.settings_stack.adjustSize()
        self.step3_title.setText("คุณภาพเสียง" if audio else "โค้ดเดก")
        self._on_format_changed()

    def _on_format_changed(self) -> None:
        ext = self.target_ext()
        if ext in VIDEO_TARGETS:
            self._fill_video_codecs(ext)
            self._fill_audio_codecs(ext)
        else:
            self._fill_audio_bitrates(ext)
        self._refresh()

    def _copy_reason(self, ext: str, settings: ConvertSettings) -> str:
        return copy_problem(ext, settings, self._info) if self._info else ""

    def _fill_video_codecs(self, ext: str) -> None:
        previous = self._selected_vcodec()
        self.vcodec_seg.clear()
        first_enabled = None
        for index, key in enumerate(VIDEO_CODECS_FOR[ext]):
            label_text, encoder = VIDEO_CODECS[key]
            button = self.vcodec_seg.add(label_text, index, tooltip=CODEC_TIPS.get(key, ""))
            button.setProperty("codec", key)
            reason = ""
            if encoder and self._encoders and encoder not in self._encoders:
                reason = f"FFmpeg ในเครื่องนี้ไม่มี {encoder}"
            elif key == COPY:
                reason = self._copy_reason(ext, ConvertSettings(video_codec=COPY))
                if not reason and self._info and self._info.get("video") is None:
                    reason = "ไฟล์นี้ไม่มีภาพ"
            if reason:
                button.setEnabled(False)
                button.setToolTip(reason)
            elif first_enabled is None:
                first_enabled = button
        keep = next((b for b in self.vcodec_seg.group.buttons()
                     if b.property("codec") == previous and b.isEnabled()), None)
        (keep or first_enabled or self.vcodec_seg.group.buttons()[0]).setChecked(True)
        self.vcodec_seg.refresh_thumb()

    def _fill_audio_codecs(self, ext: str) -> None:
        previous = self.acodec_combo.currentData()
        self.acodec_combo.blockSignals(True)
        self.acodec_combo.clear()
        for key in AUDIO_CODECS_FOR[ext]:
            text = AUDIO_CODECS[key][0] if key != COPY else "คงเดิม (ไม่แปลงเสียง)"
            self.acodec_combo.addItem(text, key)
            blocked = key == COPY and (
                self._copy_reason(ext, ConvertSettings(video_codec="h264", audio_codec=COPY))
                or (self._info is not None and self._info.get("audio") is None))
            if blocked:
                self.acodec_combo.model().item(self.acodec_combo.count() - 1).setEnabled(False)
        index = self.acodec_combo.findData(previous)
        if index < 0 or not self.acodec_combo.model().item(index).isEnabled():
            index = 0
        self.acodec_combo.setCurrentIndex(index)
        self.acodec_combo.blockSignals(False)

    def _fill_audio_bitrates(self, ext: str) -> None:
        previous = self.abitrate_combo.currentData()
        self.abitrate_combo.blockSignals(True)
        self.abitrate_combo.clear()
        lossless = ext in LOSSLESS_AUDIO
        if lossless:
            self.abitrate_combo.addItem("Lossless · เต็มคุณภาพ", None)
        else:
            for kbps in AUDIO_BITRATES:
                self.abitrate_combo.addItem(f"{kbps} kbps", kbps)
            self.abitrate_combo.setCurrentIndex(self.abitrate_combo.findData(192))
        can_copy = bool(self._info and self._info.get("audio")) and not self._copy_reason(
            ext, ConvertSettings(audio_codec=COPY))
        if can_copy:
            self.abitrate_combo.addItem("คงเดิม (ไม่แปลงเสียง เร็วที่สุด)", COPY)
        index = self.abitrate_combo.findData(previous)
        if index >= 0:
            self.abitrate_combo.setCurrentIndex(index)
        self.abitrate_combo.blockSignals(False)
        self.lossless_note.setText(
            f"{FORMAT_LABELS[ext]} เก็บเสียงแบบไม่สูญเสียคุณภาพ ไม่ต้องเลือกบิตเรต" if lossless else "")
        self.lossless_note.setVisible(lossless)

    # ----- refresh --------------------------------------------------------------------
    def _refresh(self, *_args: object) -> None:
        ext = self.target_ext()
        video_target = ext in VIDEO_TARGETS
        settings = self.settings()
        copy_video = video_target and settings.video_codec == COPY
        self.video_tuning.setEnabled(not copy_video)
        self.vbitrate_combo.setEnabled(video_target and settings.audio_codec in LOSSY_IN_VIDEO)
        has_audio = self._info is None or self._info.get("audio") is not None
        self.separator_row.setVisible(video_target and has_audio)
        self.audio_format_combo.setEnabled(self.extract_switch.isChecked())
        note = "คงเดิม: คัดลอกภาพเดิมโดยไม่เข้ารหัสใหม่ เร็วมากและไม่เสียคุณภาพ แต่ปรับคุณภาพหรือขนาดไม่ได้" if copy_video else ""
        if self.copy_note.text() != note and note and self.isVisible():
            fade_in(self.copy_note, 200)
        self.copy_note.setText(note)
        self.copy_note.setVisible(bool(note))

        ready = self._input_path is not None and self._info is not None
        self.start_button.setEnabled(ready)
        self.name_field.set_extension(ext)
        if self._input_path is None:
            self.name_field.set_default("")
            self.action_bar.set_summary("")
            return
        sidecar = self._sidecar_format()
        suggested = suggest_output_path(self._input_path, ext, sidecar_ext=sidecar, taken=self._reserved)
        self.name_field.set_default(suggested.stem)
        self._refresh_hint()

    def _refresh_hint(self) -> None:
        if self._input_path is None:
            return
        ext = self.target_ext()
        sidecar = self._sidecar_format()
        self.action_bar.set_summary(
            f"{describe_conversion(Path(self._input_path).suffix, ext, sidecar)} · "
            f"{describe_settings(ext, self.settings())}")  # no file name: they can be long

    def _output_path(self) -> Path:
        """Where the result goes: the (edited) name beside the source, numbered if it is taken."""
        ext = self.target_ext()
        sidecar = self._sidecar_format()
        folder = Path(self._input_path or ".").parent
        stem = safe_filename(strip_extension(self.name_field.name(), ext)) or Path(self._input_path or "file").stem
        count = 1
        while True:
            name = stem if count == 1 else f"{stem} ({count})"
            candidate = folder / f"{name}.{ext}"
            paths = [candidate] + ([audio_sidecar_path(candidate, sidecar)] if sidecar else [])
            if not any(p.exists() or p in self._reserved for p in paths):
                return candidate
            count += 1

    def _sidecar_format(self) -> str | None:
        if self.is_audio_target() or not self.extract_switch.isChecked() or not self.separator_row.isVisibleTo(self):
            return None
        return str(self.audio_format_combo.currentData() or "mp3")

    def _set_notice(self, message: str) -> None:
        self.notice_label.setText(message)
        self.notice_label.setVisible(bool(message))

    # ----- choosing a file ------------------------------------------------------------
    def set_input_file(self, path: str) -> None:
        """Select the file to convert; its details are read in a QThread."""
        self._set_notice("")
        self._input_path = path
        self._info = None
        self.name_field.edited = False  # a new file gets a fresh suggested name
        self._last_dir = str(Path(path).parent)
        self._serial += 1
        self._reap_probes()
        self.dropzone.hide()
        self.format_badge.setText(Path(path).suffix.lstrip(".").upper() or "?")
        self.file_name_label.set_full_text(Path(path).name)
        self.file_kind_label.set_full_text("กำลังอ่านข้อมูลไฟล์…")
        for tile, value in self.tiles.values():
            value.set_full_text("…")
            tile.show()
        self._layout_tiles(self._tile_columns)
        self.file_error_label.hide()
        if self.file_card.isHidden():
            pop_in(self.file_card)
        self._layout_tiles(4 if self.file_card.width() >= 600 else 2)
        probe = _ProbeThread(self._serial, path)
        probe.probed.connect(self._on_probed)
        probe.finished.connect(self._reap_probes)
        self._probes.append(probe)
        probe.start()
        self._refresh()

    def _on_probed(self, serial: int, result: dict) -> None:
        if serial != self._serial:
            return  # the user picked another file meanwhile
        info, error = result.get("info"), result.get("error")
        if error or not info:
            self._info = None
            self.file_kind_label.set_full_text("อ่านข้อมูลไม่ได้")
            for tile, _value in self.tiles.values():
                tile.hide()
            self.file_error_label.setText(str(error))
            self.file_error_label.show()
            self.options_card.hide()
            self._refresh()
            return
        self._info = info
        self._show_info(info)
        has_video, has_audio = info["video"] is not None, info["audio"] is not None
        self.video_button.setEnabled(has_video)
        self.audio_button.setEnabled(has_audio)
        if not has_video:
            self.audio_button.setChecked(True)
        elif not has_audio:
            self.video_button.setChecked(True)
        self.kind_seg.refresh_thumb()
        self._on_kind_changed()
        if self.options_card.isHidden():
            pop_in(self.options_card, delay=140)  # settings rise in just after the file details
        else:
            fade_in(self.options_card, 200)

    def _show_info(self, info: dict[str, Any]) -> None:
        video, audio = info["video"], info["audio"]
        kind = "วิดีโอ" if video else "ไฟล์เสียง"
        self.file_kind_label.set_full_text(f"{kind} · {info['format_long'] or info['format_name']}")
        values = {
            "duration": format_duration(info["duration"]) if info["duration"] else "—",
            "size": format_size(info["size"]),
            "bitrate": format_bitrate(info["bit_rate"]),
        }
        if video:
            values["vcodec"] = codec_name(video["codec"], video["profile"])
            values["resolution"] = f"{video['width']} × {video['height']}" if video["width"] else "—"
            values["fps"] = f"{video['fps']:g} fps" if video["fps"] else "—"
        if audio:
            values["acodec"] = codec_name(audio["codec"])
            details = []
            if audio["sample_rate"]:
                details.append(f"{audio['sample_rate'] / 1000:g} kHz")
            if audio["channels"]:
                details.append(CHANNELS.get(audio["channels"], f"{audio['channels']} ช่อง"))
            if audio["bit_rate"]:
                details.append(format_bitrate(audio["bit_rate"]))
            values["audio"] = " · ".join(details) or "—"
        for key, (tile, value) in self.tiles.items():
            text = values.get(key)
            tile.setVisible(text is not None)
            if text is not None:
                value.set_full_text(text)
        self._layout_tiles(self._tile_columns)

    def _reap_probes(self) -> None:
        for probe in list(self._probes):
            if probe.wait(0):  # finished; the rest are swept on the next call
                self._probes.remove(probe)
                probe.deleteLater()

    def _wait_probes(self) -> None:
        for probe in list(self._probes):
            probe.wait(3000)

    # ----- submit ---------------------------------------------------------------------
    def build_task(self) -> ConverterTask | None:
        """Create the task for the current selection and reserve its output names."""
        if self._input_path is None or self._info is None:
            return None
        sidecar = self._sidecar_format()
        output = self._output_path()
        self._reserved.add(output)
        if sidecar:
            self._reserved.add(audio_sidecar_path(output, sidecar))
        return ConverterTask(
            self._input_path, str(output),
            extract_audio=sidecar is not None, audio_format=sidecar or "mp3",
            settings=self.settings(),
        )

    def _submit(self) -> None:
        task = self.build_task()
        if task is None:
            return
        self.task_submitted.emit(task)
        self.action_bar.flash("เพิ่มเข้าคิวแล้ว ✓")
        self._refresh()  # the preview moves on to the next free name
