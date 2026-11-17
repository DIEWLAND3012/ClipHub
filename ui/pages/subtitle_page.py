"""Feature 4 page: subtitle UI with transcript preview (owner: member 4)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.errors import ClipHubError
from tasks.subtitle import Cue, SubtitleTask, format_srt_time, write_subtitles
from ui import overlays, theme
from ui.theme import repolish
from ui.widgets import ActionBar, Columns, DropZone, ElidedLabel, GlassCard, PageDrop, Segmented, page_header, pop_in, primary_button

AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma"}
VIDEO_SUFFIXES = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".flv", ".wmv"}
MEDIA_FILTER = (
    "ไฟล์เสียงและวิดีโอ ("
    + " ".join(f"*{s}" for s in sorted(AUDIO_SUFFIXES | VIDEO_SUFFIXES))
    + ");;ทุกไฟล์ (*)"
)

LANGUAGES = (("th", "ไทย"), ("en", "English"))
MODELS = (("tiny", "เร็วสุด"), ("base", "สมดุล"), ("small", "แม่นยำ"))
DEFAULT_MODEL = "base"

def _format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _label(text: str, name: str, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setObjectName(name)
    label.setWordWrap(wrap)
    return label


class SubtitlePage(QWidget):
    task_submitted = Signal(object)  # SubtitleTask

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SubtitlePage")
        self._input_path: str | None = None
        self._task: SubtitleTask | None = None          # last task submitted from this page
        self._preview_task: SubtitleTask | None = None  # task whose cues are in the table
        self._cues: tuple[Cue, ...] = ()
        self._last_dir = str(Path.home())
        self._build_ui()
        theme.on_change(self._on_theme)

    def _on_theme(self) -> None:
        self._refresh_thumb()
        if self._cues:
            self._fill_table()  # item colours are set per item, not by the stylesheet

    # ---- UI construction ------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)

        root.addLayout(page_header("ซับไตเติล"))
        self.dropzone = DropZone("ลากไฟล์เสียงหรือวิดีโอมาวางที่นี่", MEDIA_FILTER, "เลือกไฟล์เสียงหรือวิดีโอ")
        self.dropzone.setMinimumHeight(300)
        self.dropzone.file_chosen.connect(self.set_input_file)
        self.dropzone.rejected.connect(self._set_notice)
        root.addWidget(self.dropzone)
        PageDrop(self, self.set_input_file, self._set_notice)
        self._notice = _label("", "ErrorText", wrap=True)
        self._notice.hide()
        root.addWidget(self._notice)

        # The file and settings on the left, the transcript filling the right.
        self.columns = Columns(breakpoint=820, stretch=(4, 6))
        self.file_card = self._build_file_card()
        self.options_card = self._build_options_card()
        self.preview_card = self._build_preview_card()
        for widget in (self.file_card, self.options_card, self.preview_card):
            widget.hide()
        self.columns.left.addWidget(self.file_card)
        self.columns.left.addWidget(self.options_card)
        self.columns.left.addStretch(1)
        self.columns.right.addWidget(self.preview_card, 1)
        root.addWidget(self.columns, 1)

        self.action_bar = ActionBar()
        self._submit_btn = primary_button("ถอดเสียงด้วย AI", "sparkles")
        self._submit_btn.setEnabled(False)
        self._submit_btn.clicked.connect(self._submit)
        self._export_srt_btn = QPushButton("ส่งออก .srt")
        self._export_vtt_btn = QPushButton("ส่งออก .vtt")
        for button, suffix in ((self._export_srt_btn, ".srt"), (self._export_vtt_btn, ".vtt")):
            button.setObjectName("FieldButton")
            button.setEnabled(False)
            button.clicked.connect(lambda _checked=False, s=suffix: self._export(s))
        for button in (self._export_srt_btn, self._export_vtt_btn, self._submit_btn):  # main action last
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.action_bar.add_button(button)
        self._refresh_preview_state()

    def _build_file_card(self) -> QFrame:
        card = GlassCard("card")
        outer = QVBoxLayout(card)
        outer.setContentsMargins(14, 14, 14, 14)

        row = QFrame()
        row.setObjectName("FileRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(14)

        self._thumb = _label("", "Thumb")
        self._thumb_icon = "music"
        self._thumb.setFixedSize(56, 44)
        self._thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        meta = QVBoxLayout()
        meta.setSpacing(2)
        self._file_name = ElidedLabel("ยังไม่ได้เลือกไฟล์", Qt.TextElideMode.ElideMiddle)  # long names shorten
        self._file_name.setObjectName("FileName")
        self._file_meta = _label("", "FileMeta")
        meta.addWidget(self._file_name)
        meta.addWidget(self._file_meta)
        self._file_meta.hide()  # shown once a file is chosen
        self._file_size = _label("", "FileSize")
        pick = QPushButton("เปลี่ยนไฟล์")
        pick.setCursor(Qt.CursorShape.PointingHandCursor)
        pick.clicked.connect(lambda: self.dropzone.browse())

        layout.addWidget(self._thumb)
        layout.addLayout(meta, 1)
        layout.addWidget(self._file_size)
        layout.addWidget(pick)
        outer.addWidget(row)
        return card

    def _build_options_card(self) -> QFrame:
        card = GlassCard("card")
        grid = QVBoxLayout(card)
        grid.setContentsMargins(20, 18, 20, 20)
        grid.setSpacing(9)
        grid.addWidget(_label("ภาษา", "FieldLabel"))

        seg = Segmented()
        seg.setFixedHeight(theme.CONTROL_HEIGHT)
        self._lang_group = seg.group
        self._lang_buttons: dict[str, QPushButton] = {}
        for code, text in LANGUAGES:
            self._lang_buttons[code] = seg.add(text)
        self._lang_buttons["th"].setChecked(True)
        self._lang_group.buttonToggled.connect(lambda _b, on: on and self._refresh_summary())
        grid.addWidget(seg)
        grid.addSpacing(6)
        grid.addWidget(_label("โมเดล AI", "FieldLabel"))

        self._model_combo = QComboBox()
        for size, note in MODELS:
            self._model_combo.addItem(f"{size} · {note}", size)
        self._model_combo.setCurrentIndex(self._model_combo.findData(DEFAULT_MODEL))
        self._model_combo.currentIndexChanged.connect(lambda _i: self._refresh_summary())
        grid.addWidget(self._model_combo)
        self._model_combo.setToolTip("ครั้งแรกที่ใช้โมเดลแต่ละขนาด จะดาวน์โหลดโมเดลจากอินเทอร์เน็ต")
        return card

    def _build_preview_card(self) -> QFrame:
        card = GlassCard("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 14)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.addWidget(_label("ผลถอดความ", "FieldLabel"))
        header.addStretch(1)
        self._chip = _label("", "Chip")
        header.addWidget(self._chip)
        layout.addLayout(header)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["#", "เวลา", "ข้อความ"])
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setShowGrid(False)
        self._table.setWordWrap(True)
        self._table.setMinimumHeight(160)
        head = self._table.horizontalHeader()
        head.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        head.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        head.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        head.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._table, 1)

        self._empty = _label(
            "ผลถอดความจะแสดงที่นี่",
            "Empty", wrap=True,
        )
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._empty, 1)
        return card

    # ---- file selection and submit --------------------------------------

    def set_input_file(self, path: str) -> None:
        source = Path(path)
        self._input_path = str(source)
        self._last_dir = str(source.parent)
        kind = "วิดีโอ" if source.suffix.lower() in VIDEO_SUFFIXES else "เสียง"
        try:
            size_text = _format_size(source.stat().st_size)
        except OSError:
            size_text = ""
        self._file_name.set_full_text(source.name)
        self._file_name.setToolTip(str(source))
        self._file_meta.show()
        self._file_meta.setText(f"{kind} · {source.suffix.lstrip('.').upper() or 'ไม่ทราบชนิด'}")
        self._file_size.setText(size_text)
        self._thumb_icon = "film" if kind == "วิดีโอ" else "music"
        self._refresh_thumb()
        self._submit_btn.setEnabled(True)
        self._set_notice("")
        self.dropzone.hide()
        if self.file_card.isHidden():  # first file: the cards rise in one after another
            pop_in(self.file_card)
            pop_in(self.options_card, delay=110)
            pop_in(self.preview_card, delay=220)
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        """Language and model only: file names can be very long."""
        if self._input_path:
            self.action_bar.set_summary(f"ภาษา{dict(LANGUAGES)[self.selected_language()]} · {self.selected_model()}")

    def selected_language(self) -> str:
        return next(code for code, button in self._lang_buttons.items() if button.isChecked())

    def selected_model(self) -> str:
        return self._model_combo.currentData()

    def _submit(self) -> None:
        if self._input_path is None:
            return
        source = Path(self._input_path)
        task = SubtitleTask(
            str(source),
            str(source.with_suffix(".srt")),
            language=self.selected_language(),
            model_size=self.selected_model(),
            write_file=False,  # the preview is shown first; the user exports from here
        )
        self._task = task
        self.action_bar.flash("เพิ่มเข้าคิวแล้ว ✓ ผลถอดความจะขึ้นทางขวาเมื่อเสร็จ", 5000)
        self.task_submitted.emit(task)

    # ---- preview and export ---------------------------------------------

    def show_preview(self, task: SubtitleTask) -> None:
        """Hook for MainWindow: call when a SubtitleTask has finished. Lists task.cues and enables export."""
        self._preview_task = task
        self._cues = tuple(cue for cue in task.cues if cue.text.strip())
        self._fill_table()
        self._refresh_preview_state()
        self._set_status("" if self._cues else "ไม่พบเสียงพูดในไฟล์นี้")

    def _fill_table(self) -> None:
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        self._table.setRowCount(len(self._cues))
        for row, cue in enumerate(self._cues):
            number = QTableWidgetItem(str(row + 1))
            number.setForeground(theme.color("text3"))
            number.setFont(mono)
            when = QTableWidgetItem(f"{format_srt_time(cue.start)} → {format_srt_time(cue.end)}")
            when.setForeground(theme.color("accent"))
            when.setFont(mono)
            text = QTableWidgetItem(cue.text)
            text.setFont(QApplication.font())  # item views otherwise drop the Inter + Noto Sans Thai family list
            for column, item in enumerate((number, when, text)):
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._table.setItem(row, column, item)

    def _refresh_thumb(self) -> None:
        self._thumb.setPixmap(theme.icon_pixmap(self._thumb_icon, theme.hex_color("purple"), 22, 1.7))

    def _refresh_preview_state(self) -> None:
        has_cues = bool(self._cues)
        if has_cues:
            self._set_chip(f"{len(self._cues)} ประโยค · ซิงก์แล้ว", on=True)
        elif self._preview_task is not None:
            self._set_chip("0 ประโยค", on=False)
        else:
            self._set_chip("", on=False)
        self._table.setVisible(has_cues)
        self._empty.setVisible(not has_cues)
        if self._preview_task is not None and not has_cues:
            self._empty.setText("ไม่พบเสียงพูดในไฟล์นี้ ลองเลือกภาษาหรือไฟล์อื่น")
        self._export_srt_btn.setEnabled(has_cues)
        self._export_vtt_btn.setEnabled(has_cues)

    def _set_chip(self, text: str, on: bool) -> None:
        self._chip.setText(text)
        self._chip.setVisible(bool(text))
        self._chip.setProperty("on", "true" if on else "false")
        repolish(self._chip)

    def _suggest_path(self, suffix: str) -> str:
        if self._preview_task is not None:
            return str(Path(self._preview_task.output_path).with_suffix(suffix))
        return str(Path(self._last_dir) / f"subtitle{suffix}")

    def _export(self, suffix: str) -> None:
        if not self._cues:
            return
        file_filter = "SubRip (*.srt)" if suffix == ".srt" else "WebVTT (*.vtt)"
        chosen, _ = QFileDialog.getSaveFileName(
            self, f"ส่งออก {suffix}", self._suggest_path(suffix), file_filter
        )
        if not chosen:
            return
        if not chosen.lower().endswith(suffix):
            chosen += suffix
        try:
            saved = write_subtitles(self._cues, chosen)
        except ClipHubError as exc:
            if not overlays.notify("error", "ส่งออกไม่สำเร็จ", str(exc)):
                QMessageBox.warning(self, "ส่งออกไม่สำเร็จ", str(exc))
            return
        if not overlays.notify("success", f"บันทึก {saved.name} แล้ว", "",
                               [("แสดงในโฟลเดอร์", lambda: overlays.reveal_in_folder(saved))]):
            self._set_status(f"บันทึกไฟล์แล้ว: {saved}")

    def _set_status(self, text: str) -> None:
        if text:
            self.action_bar.flash(text, 5000)

    def _set_notice(self, text: str) -> None:
        self._notice.setText(text)
        self._notice.setVisible(bool(text))

