"""Feature 1 page: downloader UI (owner: member 1)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QThread, Signal
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
    QVBoxLayout,
    QWidget,
)

from core.errors import ClipHubError
from tasks.downloader import (
    DEFAULT_QUALITY,
    INVALID_URL_MESSAGE,
    DownloaderTask,
    fetch_metadata,
    format_duration,
    is_valid_youtube_url,
)
from ui import overlays, theme
from ui.theme import repolish
from ui.widgets import Segmented, bind_icon, card as _glass_card, field_label as _field_label, page_header, primary_button

# (quality key, label) in the order of the mockup dropdown
QUALITY_LABELS = [
    ("best", "สูงสุด · Best Quality"),
    ("2160p", "2160p · 4K"),
    ("1440p", "1440p · QHD"),
    ("1080p", "1080p · Full HD"),
    ("720p", "720p · HD"),
    ("360p", "360p"),
]
DEFAULT_OUTPUT_DIR = Path.home() / "Movies" / "ClipHUB Downloads"
CHIPS_PER_ROW = 5


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


def _card():
    return _glass_card(spacing=10)


class DownloaderPage(QWidget):
    task_submitted = Signal(object)  # DownloaderTask

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._metadata: dict[str, Any] | None = None
        self._worker: MetadataWorker | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addLayout(self._build_header())
        layout.addWidget(self._build_url_card())
        self.meta_card = self._build_meta_card()
        self.meta_card.hide()
        layout.addWidget(self.meta_card)
        layout.addWidget(self._build_options_card())
        layout.addWidget(self._build_folder_card())
        layout.addLayout(self._build_footer())
        layout.addStretch(1)

        self.url_edit.textChanged.connect(self._on_url_changed)
        self.url_edit.returnPressed.connect(self.start_fetch)
        self.paste_button.clicked.connect(self.paste_from_clipboard)
        self.fetch_button.clicked.connect(self.start_fetch)
        self.quality_combo.currentIndexChanged.connect(self._on_options_changed)
        self.mode_group.buttonToggled.connect(self._on_options_changed)
        self.folder_button.clicked.connect(self.choose_folder)
        self.download_button.clicked.connect(self.start_download)
        self._on_options_changed()
        theme.on_change(self._refresh_icons)

    def _refresh_icons(self) -> None:
        self._link_action.setIcon(theme.themed_icon("link", "text2", 18))
        self._folder_action.setIcon(theme.themed_icon("folder", "text2", 18))

    # ----- layout -----
    def _build_header(self):
        return page_header("ดาวน์โหลด")

    def _build_url_card(self) -> QFrame:
        card, layout = _card()
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("วางลิงก์ YouTube")
        self.url_edit.setClearButtonEnabled(True)
        self._link_action = self.url_edit.addAction(theme.themed_icon("link"), QLineEdit.ActionPosition.LeadingPosition)
        self.paste_button = QPushButton("วาง")
        bind_icon(self.paste_button, "paste", "text2", 16)
        self.fetch_button = QPushButton("ดึงข้อมูล")
        for button in (self.paste_button, self.fetch_button):
            button.setObjectName("FieldButton")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(self.url_edit, 1)
        row.addWidget(self.paste_button)
        row.addWidget(self.fetch_button)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.hide()
        layout.addLayout(row)
        layout.addWidget(self.status_label)
        return card

    def _build_meta_card(self) -> QFrame:
        card, layout = _card()
        layout.setSpacing(14)

        poster = QFrame()
        poster.setObjectName("Poster")
        poster.setFixedSize(208, 118)  # placeholder: thumbnails are not downloaded
        play = QLabel()
        play.setObjectName("PosterPlay")
        play.setFixedSize(46, 46)
        play.setAlignment(Qt.AlignmentFlag.AlignCenter)
        play.setPixmap(theme.icon_pixmap("play", "#1d1d1f", 20))
        self.duration_badge = QLabel("00:00")
        self.duration_badge.setObjectName("PosterDuration")
        poster_layout = QGridLayout(poster)
        poster_layout.setContentsMargins(8, 8, 8, 8)
        poster_layout.addWidget(play, 0, 0, Qt.AlignmentFlag.AlignCenter)
        poster_layout.addWidget(self.duration_badge, 0, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)

        self.title_label = QLabel()
        self.title_label.setObjectName("MetaTitle")
        self.title_label.setWordWrap(True)
        self.info_label = QLabel()
        self.info_label.setObjectName("MetaRow")
        self.info_label.setWordWrap(True)
        info = QVBoxLayout()
        info.setSpacing(9)
        info.addWidget(self.title_label)
        info.addWidget(self.info_label)
        info.addStretch(1)
        top = QHBoxLayout()
        top.setSpacing(18)
        top.addWidget(poster, 0, Qt.AlignmentFlag.AlignTop)
        top.addLayout(info, 1)

        self.chips_layout = QGridLayout()
        self.chips_layout.setHorizontalSpacing(8)
        self.chips_layout.setVerticalSpacing(8)
        layout.addLayout(top)
        layout.addLayout(self.chips_layout)
        return card

    def _build_options_card(self) -> QFrame:
        card, layout = _card()
        self.quality_combo = QComboBox()
        for key, label in QUALITY_LABELS:
            self.quality_combo.addItem(label, key)
        self.quality_combo.setCurrentIndex(self.quality_combo.findData(DEFAULT_QUALITY))

        segmented = Segmented()
        segmented.setFixedHeight(theme.CONTROL_HEIGHT)
        self.video_button = segmented.add("วิดีโอ · MP4", icon="video")
        self.audio_button = segmented.add("เสียง · MP3", icon="audio")
        self.mode_group = segmented.group
        self.video_button.setChecked(True)

        left = QVBoxLayout()
        left.setSpacing(9)
        left.addWidget(_field_label("คุณภาพ"))
        left.addWidget(self.quality_combo)
        right = QVBoxLayout()
        right.setSpacing(9)
        right.addWidget(_field_label("รูปแบบ"))
        right.addWidget(segmented)
        right.addStretch(1)
        columns = QHBoxLayout()
        columns.setSpacing(14)
        columns.addLayout(left, 1)
        columns.addLayout(right, 1)
        layout.addLayout(columns)
        return card

    def _build_folder_card(self) -> QFrame:
        card, layout = _card()
        self.folder_edit = QLineEdit(str(DEFAULT_OUTPUT_DIR))
        self._folder_action = self.folder_edit.addAction(theme.themed_icon("folder"), QLineEdit.ActionPosition.LeadingPosition)
        self.folder_button = QPushButton("เลือก…")
        self.folder_button.setObjectName("FieldButton")
        self.folder_button.setCursor(Qt.CursorShape.PointingHandCursor)
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(self.folder_edit, 1)
        row.addWidget(self.folder_button)
        layout.addWidget(_field_label("บันทึกที่"))
        layout.addLayout(row)
        return card

    def _build_footer(self) -> QHBoxLayout:
        self.download_button = primary_button("ดาวน์โหลด", "download")
        self.hint_label = QLabel()
        self.hint_label.setObjectName("Hint")
        self.hint_label.hide()  # the selected options are already visible above
        row = QHBoxLayout()
        row.setContentsMargins(0, 4, 0, 0)
        row.setSpacing(18)
        row.addWidget(self.download_button)
        row.addWidget(self.hint_label)
        row.addStretch(1)
        return row

    # ----- state helpers -----
    def selected_quality(self) -> str:
        return str(self.quality_combo.currentData() or DEFAULT_QUALITY)

    def is_audio_only(self) -> bool:
        return self.audio_button.isChecked()

    def _set_status(self, text: str, state: str = "") -> None:
        self.status_label.setText(text)
        self.status_label.setProperty("state", state)
        self.status_label.setVisible(bool(text))
        repolish(self.status_label)

    def _on_url_changed(self) -> None:
        self._metadata = None  # the preview belongs to the previous link
        self.meta_card.hide()
        if self._worker is None:
            self._set_status("")

    def _on_options_changed(self, *_args: object) -> None:
        audio = self.is_audio_only()
        self.quality_combo.setEnabled(not audio)
        if audio:
            self.hint_label.setText("เสียง · MP3")
        else:
            self.hint_label.setText(f"{self.quality_combo.currentText().split(' · ')[0]} · MP4")
        self._refresh_chips()

    def _refresh_chips(self) -> None:
        selected = self.selected_quality()
        for index in range(self.chips_layout.count()):
            chip = self.chips_layout.itemAt(index).widget()
            chip.setProperty("on", chip.text() == selected and not self.is_audio_only())
            repolish(chip)

    def _show_metadata(self, metadata: dict[str, Any]) -> None:
        self.title_label.setText(metadata.get("title") or "(ไม่มีชื่อ)")
        duration = format_duration(metadata.get("duration", 0))
        self.duration_badge.setText(duration)
        self.info_label.setText(" · ".join(part for part in (duration, metadata.get("uploader") or "") if part))
        while self.chips_layout.count():
            old = self.chips_layout.takeAt(0).widget()
            old.hide()
            old.deleteLater()
        for index, height in enumerate(reversed(metadata.get("heights") or [])):
            chip = QLabel(f"{height}p")
            chip.setObjectName("Chip")
            row, column = divmod(index, CHIPS_PER_ROW)
            self.chips_layout.addWidget(chip, row, column)
        self.chips_layout.setColumnStretch(CHIPS_PER_ROW, 1)
        self._refresh_chips()
        self.meta_card.show()

    # ----- metadata fetch -----
    def start_fetch(self) -> None:
        if self._worker is not None:
            return
        url = self.url_edit.text().strip()
        if not is_valid_youtube_url(url):
            self._set_status(INVALID_URL_MESSAGE, "err")
            return
        self._set_status("กำลังดึงข้อมูลวิดีโอ…")
        self.fetch_button.setEnabled(False)
        self.fetch_button.setText("กำลังดึง…")
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
        self._show_metadata(metadata)
        self._set_status("ดึงข้อมูลสำเร็จ", "ok")

    def _on_fetch_failed(self, url: str, message: str) -> None:
        stale = url != self.url_edit.text().strip()
        self._set_status("" if stale else message, "" if stale else "err")

    def _on_worker_finished(self) -> None:
        worker, self._worker = self._worker, None
        self.fetch_button.setEnabled(True)
        self.fetch_button.setText("ดึงข้อมูล")
        if worker is not None:
            worker.deleteLater()

    def shutdown(self) -> bool:
        """Called by MainWindow on close. Returns True once no metadata fetch is running.

        The fetch cannot be cancelled mid-request, so it is waited for and never terminated:
        terminating a QThread that is inside Python code can hang the process.
        """
        worker = self._worker
        if worker is None or not worker.isRunning():
            return True
        return worker.wait(1500)

    # ----- actions -----
    def paste_from_clipboard(self) -> None:
        text = QApplication.clipboard().text().strip()
        if text:
            self.url_edit.setText(text)

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
        folder = Path(self.folder_edit.text().strip() or str(DEFAULT_OUTPUT_DIR)).expanduser()
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
        )
        self.task_submitted.emit(task)
        self._set_status("เพิ่มงานเข้าคิวแล้ว", "ok")

    def _warn(self, title: str, text: str) -> None:
        if overlays.notify("error", title, text):
            return
        box = QMessageBox(QMessageBox.Icon.Warning, title, text, QMessageBox.StandardButton.Ok, self)
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        box.open()  # non-blocking; the page stays responsive
