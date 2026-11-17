"""Feature 1 page: downloader UI (owner: member 1).

The page follows the order the user decides things in:
1. destination folder  2. link  3. video or audio  4. file format  5. resolution / bitrate  -> download
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QSettings, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.errors import ClipHubError
from tasks.downloader import (
    AUDIO_BITRATES,
    AUDIO_FORMATS,
    DEFAULT_AUDIO_BITRATE,
    DEFAULT_QUALITY,
    H264_MAX_HEIGHT,
    INVALID_URL_MESSAGE,
    LOSSLESS_AUDIO,
    QUALITIES,
    QUALITY_LABELS,
    SOURCE,
    VIDEO_FORMATS,
    DownloaderTask,
    describe_choice,
    suggest_file_name,
    fetch_metadata,
    format_duration,
    is_valid_youtube_url,
)
from ui import overlays, theme
from ui.theme import repolish
from ui.widgets import (
    ActionBar, Columns, ElidedLabel, NameField, Segmented, bind_icon, card, fade_in, field_label, label, page_header, primary_button,
    reveal,
)

DEFAULT_OUTPUT_DIR = Path.home() / "Movies" / "ClipHUB Downloads"
CHIPS_PER_ROW = 6
FORMAT_LABELS = {"mp4": "MP4", "mov": "MOV", "mkv": "MKV", "webm": "WebM",
                 "mp3": "MP3", "m4a": "M4A", "wav": "WAV", "flac": "FLAC", "opus": "Opus"}
FORMAT_TIPS = {
    "mp4": "เล่นได้ทุกที่", "mov": "สำหรับ Final Cut / iMovie", "mkv": "เก็บได้ทุกโค้ดเดก", "webm": "สำหรับเว็บ",
    "mp3": "เล่นได้ทุกที่", "m4a": "AAC คุณภาพดีกว่า MP3 ที่ขนาดเท่ากัน", "wav": "ไม่บีบอัด ไฟล์ใหญ่",
    "flac": "Lossless ไฟล์เล็กกว่า WAV", "opus": "คุณภาพสูงที่บิตเรตต่ำ",
}
SETTINGS_KEYS = ("download/folder", "download/kind", "download/video_format", "download/audio_format",
                 "download/quality", "download/bitrate")


class MetadataWorker(QThread):
    """Fetches video info off the GUI thread (SRS 4.1.1)."""

    fetched = Signal(str, object)  # url, metadata dict
    failed = Signal(str, str)      # url, message for the user

    def __init__(self, url: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.url = url

    def run(self) -> None:
        try:
            metadata = fetch_metadata(self.url)
        except ClipHubError as exc:
            self.failed.emit(self.url, str(exc))
        except Exception as exc:  # never let a network quirk kill the app
            self.failed.emit(self.url, f"เกิดข้อผิดพลาดไม่คาดคิด: {exc}")
        else:
            self.fetched.emit(self.url, metadata)


def _step(number: int, text: str) -> QHBoxLayout:
    """Numbered step heading: a small accent circle and a field label."""
    badge = label(str(number), "StepNumber")
    badge.setFixedSize(20, 20)
    badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
    row = QHBoxLayout()
    row.setSpacing(8)
    row.addWidget(badge)
    row.addWidget(field_label(text))
    row.addStretch(1)
    return row


def _quality_text(key: str) -> str:
    return QUALITY_LABELS.get(key, key)


class DownloaderPage(QWidget):
    task_submitted = Signal(object)  # DownloaderTask

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._metadata: dict[str, Any] | None = None
        self._worker: MetadataWorker | None = None
        self._settings = QSettings("ClipHUB", "ClipHUB")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addLayout(page_header("ดาวน์โหลด"))
        # Numbered steps in the order the choices are made: where + what on the left,
        # how on the right, so the whole page fits without scrolling.
        columns = Columns(breakpoint=760)
        where, left = card(spacing=10, margins=(22, 20, 22, 22))
        self._build_folder(left)
        left.addSpacing(12)
        self._build_url(left)
        left.addStretch(1)
        how, right = card(spacing=10, margins=(22, 20, 22, 22))
        self._build_options(right)
        right.addStretch(1)
        columns.left.addWidget(where)
        columns.right.addWidget(how)
        layout.addWidget(columns)
        layout.addStretch(1)
        self._build_actions()

        self.url_edit.textChanged.connect(self._on_url_changed)
        self.url_edit.returnPressed.connect(self.start_fetch)
        # The clip's details load by themselves shortly after a valid link is typed or pasted.
        self._fetch_timer = QTimer(self)
        self._fetch_timer.setSingleShot(True)
        self._fetch_timer.setInterval(350)
        self._fetch_timer.timeout.connect(self.start_fetch)
        self.paste_button.clicked.connect(self.paste_from_clipboard)
        self.folder_button.clicked.connect(self.choose_folder)
        self.folder_edit.textChanged.connect(self._refresh)
        self.kind_seg.group.idToggled.connect(self._on_kind_toggled)
        self.video_formats.group.idToggled.connect(lambda _id, on: on and self._refresh())
        self.audio_formats.group.idToggled.connect(lambda _id, on: on and self._refresh())
        self.quality_combo.currentIndexChanged.connect(self._refresh)
        self.bitrate_combo.currentIndexChanged.connect(self._refresh)
        self.download_button.clicked.connect(self.start_download)
        self._restore_choices()
        # What the user picked, kept even when a clip lacks it and the combo shows a lower one.
        self._preferred_quality = self.selected_quality()
        self.quality_combo.activated.connect(lambda _i: setattr(self, "_preferred_quality", self.selected_quality()))
        theme.on_change(self._refresh_icons)
        self._refresh()

    # ----- layout ---------------------------------------------------------------
    def _build_folder(self, layout: QVBoxLayout) -> None:
        layout.addLayout(_step(1, "บันทึกที่"))
        self.folder_edit = QLineEdit(str(DEFAULT_OUTPUT_DIR))
        self.folder_edit.setPlaceholderText("เลือกโฟลเดอร์ปลายทาง")
        self._folder_action = self.folder_edit.addAction(theme.themed_icon("folder"), QLineEdit.ActionPosition.LeadingPosition)
        self.folder_button = QPushButton("เลือกโฟลเดอร์…")
        self.folder_button.setObjectName("FieldButton")
        self.folder_button.setCursor(Qt.CursorShape.PointingHandCursor)
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(self.folder_edit, 1)
        row.addWidget(self.folder_button)
        layout.addLayout(row)

    def _build_url(self, layout: QVBoxLayout) -> None:
        layout.addLayout(_step(2, "ลิงก์"))
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("วางลิงก์ YouTube")
        self.url_edit.setClearButtonEnabled(True)
        self._link_action = self.url_edit.addAction(theme.themed_icon("link"), QLineEdit.ActionPosition.LeadingPosition)
        self.paste_button = QPushButton("วาง")
        bind_icon(self.paste_button, "paste", "text2", 16)
        self.paste_button.setObjectName("FieldButton")
        self.paste_button.setCursor(Qt.CursorShape.PointingHandCursor)
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(self.url_edit, 1)
        row.addWidget(self.paste_button)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.hide()
        layout.addLayout(row)
        layout.addWidget(self.status_label)
        self.meta_box = self._build_meta()
        self.meta_box.hide()
        layout.addWidget(self.meta_box)

    def _build_meta(self) -> QFrame:
        box = QFrame()
        box.setObjectName("FileRow")
        row = QHBoxLayout(box)
        row.setContentsMargins(12, 12, 14, 12)
        row.setSpacing(14)
        poster = QFrame()
        poster.setObjectName("Poster")
        poster.setFixedSize(128, 72)
        self.duration_badge = QLabel("00:00")
        self.duration_badge.setObjectName("PosterDuration")
        poster_layout = QGridLayout(poster)
        poster_layout.setContentsMargins(6, 6, 6, 6)
        poster_layout.addWidget(self.duration_badge, 0, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
        self.title_label = ElidedLabel()
        self.title_label.setObjectName("MetaTitle")
        self.info_label = ElidedLabel()
        self.info_label.setObjectName("MetaRow")
        # Chips may clip on narrow windows rather than forcing the page wider.
        chips_box = QWidget()
        chips_box.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.chips_layout = QHBoxLayout(chips_box)
        self.chips_layout.setContentsMargins(0, 0, 0, 0)
        self.chips_layout.setSpacing(6)
        texts = QVBoxLayout()
        texts.setSpacing(4)
        texts.addWidget(self.title_label)
        texts.addWidget(self.info_label)
        texts.addWidget(chips_box)
        texts.addStretch(1)
        row.addWidget(poster, 0, Qt.AlignmentFlag.AlignTop)
        row.addLayout(texts, 1)
        return box

    def _build_options(self, layout: QVBoxLayout) -> None:
        layout.addLayout(_step(3, "ประเภท"))
        self.kind_seg = Segmented()
        self.kind_seg.setFixedHeight(theme.CONTROL_HEIGHT)
        self.video_button = self.kind_seg.add("วิดีโอ", 0, icon="video")
        self.audio_button = self.kind_seg.add("เสียง", 1, icon="audio")
        self.video_button.setChecked(True)
        layout.addWidget(self.kind_seg)

        layout.addSpacing(12)
        layout.addLayout(_step(4, "รูปแบบไฟล์"))
        self.video_formats = self._format_seg(VIDEO_FORMATS)
        self.audio_formats = self._format_seg(AUDIO_FORMATS)
        self.format_stack = QStackedWidget()
        self.format_stack.addWidget(self.video_formats)
        self.format_stack.addWidget(self.audio_formats)
        self.format_stack.setFixedHeight(theme.CONTROL_HEIGHT)
        layout.addWidget(self.format_stack)

        layout.addSpacing(12)
        self.quality_step = _step(5, "ความละเอียด")
        self.quality_title = self.quality_step.itemAt(1).widget()
        layout.addLayout(self.quality_step)
        self.quality_combo = QComboBox()
        self._fill_video_qualities()
        self.bitrate_combo = QComboBox()
        for kbps in AUDIO_BITRATES:
            self.bitrate_combo.addItem(f"{kbps} kbps", kbps)
        self.bitrate_combo.addItem("ต้นฉบับ", SOURCE)
        self.quality_stack = QStackedWidget()
        self.quality_stack.addWidget(self.quality_combo)
        self.quality_stack.addWidget(self.bitrate_combo)
        self.quality_stack.setFixedHeight(theme.CONTROL_HEIGHT + 2)
        layout.addWidget(self.quality_stack)
        self.option_note = label("", "Note", wrap=True)
        self.option_note.hide()
        layout.addWidget(self.option_note)

        layout.addSpacing(12)
        layout.addLayout(_step(6, "ชื่อไฟล์"))
        self.name_field = NameField()
        self.name_field.edit.setPlaceholderText("ชื่อคลิปจะขึ้นที่นี่เมื่อวางลิงก์")
        layout.addWidget(self.name_field)

    def _format_seg(self, formats: tuple[str, ...]) -> Segmented:
        seg = Segmented()
        for index, fmt in enumerate(formats):
            seg.add(FORMAT_LABELS[fmt], index, tooltip=FORMAT_TIPS.get(fmt, ""))
        seg.group.button(0).setChecked(True)
        return seg

    def _fill_video_qualities(self, available: list[int] | None = None) -> None:
        """Largest first. With metadata, resolutions the clip does not have are greyed out."""
        current = getattr(self, "_preferred_quality", None) or self.quality_combo.currentData() or DEFAULT_QUALITY
        model = QStandardItemModel(self.quality_combo)
        best = max(available) if available else None
        for key in reversed(QUALITIES):  # source, 2160p ... 144p
            text = _quality_text(key)
            item = QStandardItem(text)
            item.setData(key, Qt.ItemDataRole.UserRole)
            if key == SOURCE and best:
                item.setText(f"ต้นฉบับ · {best}p")
            elif best is not None and key != SOURCE and int(key[:-1]) > best:
                item.setText(f"{text}  —  ไม่มีในคลิปนี้")
                item.setEnabled(False)
            model.appendRow(item)
        self.quality_combo.blockSignals(True)
        self.quality_combo.setModel(model)
        index = self.quality_combo.findData(current)
        if index < 0 or not model.item(index).isEnabled():  # fall back to the best the clip has
            index = next(i for i in range(model.rowCount()) if model.item(i).isEnabled() and i > 0)
        self.quality_combo.setCurrentIndex(index)
        self.quality_combo.blockSignals(False)

    def _build_actions(self) -> None:
        """Summary + download button; MainWindow shows this bar in the same place on every page."""
        self.action_bar = ActionBar()
        self.download_button = self.action_bar.add_button(primary_button("ดาวน์โหลด", "download"))

    def _refresh_icons(self) -> None:
        self._link_action.setIcon(theme.themed_icon("link", "text2", 18))
        self._folder_action.setIcon(theme.themed_icon("folder", "text2", 18))

    # ----- state helpers ----------------------------------------------------------
    def is_audio_only(self) -> bool:
        return self.audio_button.isChecked()

    def selected_format(self) -> str:
        if self.is_audio_only():
            return AUDIO_FORMATS[max(self.audio_formats.group.checkedId(), 0)]
        return VIDEO_FORMATS[max(self.video_formats.group.checkedId(), 0)]

    def selected_quality(self) -> str:
        return str(self.quality_combo.currentData() or DEFAULT_QUALITY)

    def selected_bitrate(self) -> str:
        return str(self.bitrate_combo.currentData() or DEFAULT_AUDIO_BITRATE)

    def _on_kind_toggled(self, _id: int, checked: bool) -> None:
        if checked:
            self.format_stack.setCurrentIndex(1 if self.is_audio_only() else 0)
            self.quality_stack.setCurrentIndex(1 if self.is_audio_only() else 0)
            self._refresh()

    def _refresh(self, *_args: object) -> None:
        audio = self.is_audio_only()
        fmt = self.selected_format()
        lossless = audio and fmt in LOSSLESS_AUDIO
        self.quality_title.setText("คุณภาพเสียง" if audio else "ความละเอียด")
        self.quality_stack.setVisible(not lossless)
        if lossless:
            self._set_note(f"Lossless · {FORMAT_LABELS[fmt]} เก็บเสียงเต็มคุณภาพ ไม่ต้องเลือกบิตเรต")
        elif not audio and fmt == "mov" and self._mov_needs_encode():
            self._set_note("MOV เกิน 1080p ต้องแปลงเป็น H.264 หลังดาวน์โหลด ใช้เวลาเพิ่ม")
        else:
            self._set_note("")
        self.action_bar.set_summary(
            describe_choice(audio, fmt, self.selected_quality(), self.selected_bitrate()))
        self._refresh_chips()
        self._refresh_name()
        ready = bool(self.folder_edit.text().strip()) and is_valid_youtube_url(self.url_edit.text())
        self.download_button.setEnabled(ready)

    def _mov_needs_encode(self) -> bool:
        key = self.selected_quality()
        if key == SOURCE:
            heights = (self._metadata or {}).get("heights") or []
            return not heights or max(heights) > H264_MAX_HEIGHT
        return int(key[:-1]) > H264_MAX_HEIGHT

    def _set_note(self, text: str) -> None:
        changed = text != self.option_note.text()
        self.option_note.setText(text)
        self.option_note.setVisible(bool(text))
        if text and changed and self.isVisible():
            fade_in(self.option_note, 220)

    def _set_status(self, text: str, state: str = "") -> None:
        self.status_label.setText(text)
        self.status_label.setProperty("state", state)
        self.status_label.setVisible(bool(text))
        repolish(self.status_label)

    def _refresh_name(self) -> None:
        """Keep the suggested name in step with the clip, type, format and resolution."""
        self.name_field.set_extension(self.selected_format())
        if self._metadata is None:
            self.name_field.set_default("")
            return
        self.name_field.set_default(suggest_file_name(
            self._metadata.get("title", ""), self.is_audio_only(), self.selected_quality(),
            self._metadata.get("heights") or None))

    def _on_url_changed(self) -> None:
        self._metadata = None  # the preview belongs to the previous link
        self.name_field.edited = False  # a new clip gets a fresh suggested name
        self.meta_box.hide()
        self._fill_video_qualities()
        text = self.url_edit.text().strip()
        if is_valid_youtube_url(text):
            self._fetch_timer.start()
        else:
            self._fetch_timer.stop()
            self._set_status(INVALID_URL_MESSAGE if text else "", "err" if text else "")
        self._refresh()

    def _refresh_chips(self) -> None:
        selected = self.selected_quality()
        for index in range(self.chips_layout.count()):
            chip = self.chips_layout.itemAt(index).widget()
            if chip is None:
                continue
            chip.setProperty("on", chip.text() == selected and not self.is_audio_only())
            repolish(chip)

    def _show_metadata(self, metadata: dict[str, Any]) -> None:
        self.title_label.set_full_text(metadata.get("title") or "(ไม่มีชื่อ)")
        duration = format_duration(metadata.get("duration", 0))
        self.duration_badge.setText(duration)
        self.info_label.set_full_text(" · ".join(p for p in (duration, metadata.get("uploader") or "") if p))
        while self.chips_layout.count():
            item = self.chips_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        for height in list(reversed(metadata.get("heights") or []))[:CHIPS_PER_ROW]:
            chip = QLabel(f"{height}p")
            chip.setObjectName("Chip")
            self.chips_layout.addWidget(chip)
        self.chips_layout.addStretch(1)
        self._fill_video_qualities(metadata.get("heights") or None)
        reveal(self.meta_box)
        self._refresh()

    # ----- remembered choices -------------------------------------------------------
    def _restore_choices(self) -> None:
        get = self._settings.value
        folder = str(get("download/folder", "") or "")
        if folder:
            self.folder_edit.setText(folder)
        if str(get("download/kind", "video")) == "audio":
            self.audio_button.setChecked(True)
        for seg, formats, key in ((self.video_formats, VIDEO_FORMATS, "download/video_format"),
                                  (self.audio_formats, AUDIO_FORMATS, "download/audio_format")):
            saved = str(get(key, formats[0]))
            if saved in formats:
                seg.group.button(formats.index(saved)).setChecked(True)
        for combo, key in ((self.quality_combo, "download/quality"), (self.bitrate_combo, "download/bitrate")):
            index = combo.findData(str(get(key, "")))
            if index >= 0:
                combo.setCurrentIndex(index)

    def _save_choices(self) -> None:
        values = (self.folder_edit.text().strip(), "audio" if self.is_audio_only() else "video",
                  VIDEO_FORMATS[max(self.video_formats.group.checkedId(), 0)],
                  AUDIO_FORMATS[max(self.audio_formats.group.checkedId(), 0)],
                  self.selected_quality(), self.selected_bitrate())
        for key, value in zip(SETTINGS_KEYS, values):
            self._settings.setValue(key, value)

    # ----- metadata fetch ---------------------------------------------------------
    def start_fetch(self) -> None:
        self._fetch_timer.stop()
        url = self.url_edit.text().strip()
        if not is_valid_youtube_url(url):
            self._set_status(INVALID_URL_MESSAGE if url else "", "err" if url else "")
            return
        if self._metadata is not None:
            return  # already showing this clip
        if self._worker is not None:
            return  # _on_worker_finished fetches the newest link once this one returns
        self._set_status("กำลังโหลดคลิป…")
        worker = MetadataWorker(url, self)
        worker.fetched.connect(self._on_fetched)
        worker.failed.connect(self._on_fetch_failed)
        worker.finished.connect(self._on_worker_finished)
        self._worker = worker
        worker.start()

    def _on_fetched(self, url: str, metadata: dict[str, Any]) -> None:
        if url != self.url_edit.text().strip():
            self._set_status("")  # the user changed the link while we were fetching
            return
        self._metadata = metadata
        self._set_status("")
        self._show_metadata(metadata)

    def _on_fetch_failed(self, url: str, message: str) -> None:
        stale = url != self.url_edit.text().strip()
        self._set_status("" if stale else message, "" if stale else "err")

    def _on_worker_finished(self) -> None:
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.deleteLater()
            if worker.url != self.url_edit.text().strip():
                self.start_fetch()  # the link changed while loading: load the new one

    def shutdown(self) -> bool:
        """Called by MainWindow on close. Returns True once no metadata fetch is running.

        The fetch cannot be cancelled mid-request, so it is waited for and never terminated:
        terminating a QThread that is inside Python code can hang the process.
        """
        worker = self._worker
        if worker is None or not worker.isRunning():
            return True
        return worker.wait(1500)

    # ----- actions --------------------------------------------------------------
    def paste_from_clipboard(self) -> None:
        text = QApplication.clipboard().text().strip()
        if text:
            self.url_edit.setText(text)
            self.start_fetch()  # no need to wait for the typing delay

    def choose_folder(self) -> None:
        start = self.folder_edit.text().strip() or str(DEFAULT_OUTPUT_DIR)
        chosen = QFileDialog.getExistingDirectory(self, "เลือกโฟลเดอร์ปลายทาง", start)
        if chosen:
            self.folder_edit.setText(chosen)

    def start_download(self) -> None:
        url = self.url_edit.text().strip()
        if not is_valid_youtube_url(url):
            self._warn("ลิงก์ไม่ถูกต้อง", f"{INVALID_URL_MESSAGE}\nตัวอย่าง: https://www.youtube.com/watch?v=xxxxxxxxxxx")
            return
        folder_text = self.folder_edit.text().strip()
        if not folder_text:
            self._warn("ยังไม่ได้เลือกโฟลเดอร์", "เลือกโฟลเดอร์ปลายทางก่อนดาวน์โหลด")
            return
        folder = Path(folder_text).expanduser()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._warn("สร้างโฟลเดอร์ไม่ได้", f"สร้างโฟลเดอร์ปลายทางไม่ได้: {exc}")
            return
        task = DownloaderTask(
            url,
            str(folder),
            quality=self.selected_quality(),
            audio_only=self.is_audio_only(),
            video_title=(self._metadata or {}).get("title", ""),
            file_format=self.selected_format(),
            audio_bitrate=self.selected_bitrate(),
            file_name=self.name_field.name(),  # "" before the clip loads: the title-based name is used
        )
        self._save_choices()
        self.task_submitted.emit(task)
        self.action_bar.flash("เพิ่มเข้าคิวแล้ว ✓")

    def _warn(self, title: str, text: str) -> None:
        if overlays.notify("error", title, text):
            return
        box = QMessageBox(QMessageBox.Icon.Warning, title, text, QMessageBox.StandardButton.Ok, self)
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        box.open()  # non-blocking; the page stays responsive
