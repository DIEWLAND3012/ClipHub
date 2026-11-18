"""Settings panel, top to bottom: app version (update / go back), the GPU pack for AI subtitles, the graphics
card switch, which card, the app language, and uninstall.

Jobs always run in parallel where the hardware allows; there is nothing else to set.
"""
from __future__ import annotations

from PySide6.QtCore import QSettings, Qt, QThread, Signal
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QListView, QPushButton, QScrollArea, QVBoxLayout, QWidget

from core import app_update, gpu_pack, hwaccel
from core.version import __version__
from core.hwaccel import Engine, HardwareReport
from ui import i18n, theme
from ui.widgets import GlassCard, Segmented, fade_in, label

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


class _FitScroll(QScrollArea):
    """A scroll area that asks for its content's full height; the popup caps it to the window."""

    def __init__(self, body: QWidget) -> None:
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWidget(body)
        self.viewport().setAutoFillBackground(False)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt API)
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 (Qt API)
        layout = self.widget().layout()
        layout.activate()
        inner = width - 4
        return layout.totalHeightForWidth(inner) if layout.hasHeightForWidth() else layout.totalSizeHint().height()

    def sizeHint(self):  # noqa: N802 (Qt API)
        hint = super().sizeHint()
        hint.setHeight(self.heightForWidth(self.width() or 320))
        return hint

    def minimumSizeHint(self):  # noqa: N802 (Qt API)
        hint = super().minimumSizeHint()
        hint.setHeight(80)
        return hint


class PerformancePanel(GlassCard):
    settings_changed = Signal()
    close_requested = Signal()
    language_changed = Signal(str)
    size_changed = Signal()  # the popup must grow / shrink to fit
    install_release = Signal(object)  # AppRelease: update, or go back to an older version
    gpu_pack_install = Signal()
    gpu_pack_uninstall = Signal()
    uninstall_app = Signal()

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
        head.addWidget(label("การตั้งค่า", "RailTitle"))
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

        # Everything below the title scrolls, so a short window never squeezes rows on top of each other.
        self.body = QWidget()
        self.body.setAutoFillBackground(False)
        outer = layout
        layout = QVBoxLayout(self.body)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(12)
        self.scroll = _FitScroll(self.body)
        outer.addWidget(self.scroll, 1)

        # 1. App version: a red dot and an update button when a newer version exists; any version can be chosen.
        self._releases: list[app_update.AppRelease] = []
        version_row = QHBoxLayout()
        version_texts = QVBoxLayout()
        version_texts.setSpacing(1)
        version_texts.addWidget(label("เวอร์ชันโปรแกรม", "ToggleTitle"))
        self.version_note = label(f"v{__version__}", "Note", wrap=True)
        version_texts.addWidget(self.version_note)
        version_row.addLayout(version_texts, 1)
        self.update_dot = QLabel()
        self.update_dot.setObjectName("UpdateDot")
        self.update_dot.setFixedSize(10, 10)
        self.update_dot.hide()
        version_row.addWidget(self.update_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(version_row)
        self.update_button = QPushButton("อัปเดต")
        self.update_button.setObjectName("SettingsAction")
        self.update_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_button.clicked.connect(self._update_now)
        self.update_button.hide()
        layout.addWidget(self.update_button)
        pick_row = QHBoxLayout()
        pick_row.setSpacing(8)
        self.version_combo = QComboBox()
        self.version_combo.setView(QListView())
        self.version_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.version_combo.currentIndexChanged.connect(lambda _i: self._refresh_version_pick())
        pick_row.addWidget(self.version_combo, 1)
        self.version_install = QPushButton("ติดตั้ง")
        self.version_install.setObjectName("SettingsGhost")
        self.version_install.setCursor(Qt.CursorShape.PointingHandCursor)
        self.version_install.clicked.connect(self._install_picked)
        pick_row.addWidget(self.version_install)
        self.version_pick = QWidget()
        self.version_pick.setLayout(pick_row)
        pick_row.setContentsMargins(0, 0, 0, 0)
        self.version_pick.hide()
        layout.addWidget(self.version_pick)
        layout.addWidget(self._line())

        # 2. GPU pack: NVIDIA libraries for AI subtitles, downloaded only on request.
        pack_texts = QVBoxLayout()
        pack_texts.setSpacing(1)
        pack_texts.addWidget(label("ทรัพยากรการ์ดจอเพิ่มเติม", "ToggleTitle"))
        self.pack_note = label("", "Note", wrap=True)
        pack_texts.addWidget(self.pack_note)
        layout.addLayout(pack_texts)
        self.pack_button = QPushButton()
        self.pack_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pack_button.clicked.connect(self._pack_clicked)
        layout.addWidget(self.pack_button)
        layout.addWidget(self._line())

        switch_row = QHBoxLayout()
        texts = QVBoxLayout()
        texts.setSpacing(1)
        texts.addWidget(label("ใช้การ์ดจอช่วยประมวลผล", "ToggleTitle"))
        texts.addWidget(label("แปลงและบีบอัดวิดีโอเร็วขึ้น ใช้ CPU น้อยลง", "Note", wrap=True))
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

        # Language: a two-way sliding switch, TH | EN.
        layout.addSpacing(4)
        lang_row = QHBoxLayout()
        lang_texts = QVBoxLayout()
        lang_texts.setSpacing(1)
        lang_texts.addWidget(label("ภาษาแอป", "ToggleTitle"))
        lang_texts.addWidget(label("ไทย / English", "Note"))
        self.lang_seg = Segmented()
        self.lang_seg.setFixedSize(124, 36)
        for index, code in enumerate(i18n.LANGUAGES):
            self.lang_seg.add(code.upper(), index)
        current = i18n.language()
        self.lang_seg.group.button(i18n.LANGUAGES.index(current)).setChecked(True)
        self.lang_seg.group.idToggled.connect(lambda i, on: on and self._choose_language(i18n.LANGUAGES[i]))
        lang_row.addLayout(lang_texts, 1)
        lang_row.addWidget(self.lang_seg, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(lang_row)

        # Last: remove ClipHUB and everything it downloaded (FFmpeg, the GPU pack, settings).
        layout.addWidget(self._line())
        self.uninstall_button = QPushButton("ถอนการติดตั้ง ClipHUB")
        self.uninstall_button.setObjectName("SettingsDanger")
        self.uninstall_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.uninstall_button.clicked.connect(self.uninstall_app)
        layout.addWidget(self.uninstall_button)
        self.refresh_pack()
        self.set_report(self._report)
        self.hide()

    @staticmethod
    def _line() -> QFrame:
        line = QFrame()
        line.setObjectName("SettingsLine")
        line.setFixedHeight(1)
        return line

    # --- app version --------------------------------------------------------------------
    def set_releases(self, releases: list) -> None:
        self._releases = releases
        newer = app_update.newest(releases)
        self.update_dot.setVisible(newer is not None)
        if newer is not None:
            self.version_note.setText(f"v{__version__} · มีเวอร์ชันใหม่ {newer.label}")
            self.update_button.setText(f"อัปเดตเป็น {newer.label}")
        elif releases:
            self.version_note.setText(f"v{__version__} · เป็นเวอร์ชันล่าสุด")
        else:
            self.version_note.setText(f"v{__version__}")
        self.update_button.setVisible(newer is not None)
        self.version_combo.blockSignals(True)
        self.version_combo.clear()
        for release in releases:
            current = release.version == __version__
            self.version_combo.addItem(release.label + (" (ปัจจุบัน)" if current else ""), release.version)
            if current:
                self.version_combo.setCurrentIndex(self.version_combo.count() - 1)
        self.version_combo.blockSignals(False)
        self.version_pick.setVisible(len(releases) > 1 or (releases and newer is None
                                                          and releases[0].version != __version__))
        self._refresh_version_pick()
        self.layout().activate()
        self.size_changed.emit()

    def has_update(self) -> bool:
        return app_update.newest(self._releases) is not None

    def _refresh_version_pick(self) -> None:
        version = self.version_combo.currentData()
        self.version_install.setEnabled(bool(version) and version != __version__)

    def _update_now(self) -> None:
        newer = app_update.newest(self._releases)
        if newer is not None:
            self.install_release.emit(newer)

    def _install_picked(self) -> None:
        version = self.version_combo.currentData()
        for release in self._releases:
            if release.version == version:
                self.install_release.emit(release)

    # --- GPU pack -------------------------------------------------------------------------
    def refresh_pack(self) -> None:
        installed = gpu_pack.installed()
        if not gpu_pack.platform_supported():
            note, button = "ใช้ได้เฉพาะ Windows ที่มีการ์ดจอ NVIDIA", None
        elif installed:
            note = f"ติดตั้งแล้ว ({gpu_pack.size_on_disk() / 1024**3:.1f} GB) ซับไตเติล AI ใช้การ์ดจอ NVIDIA"
            button = "ถอนการติดตั้ง"
        elif not gpu_pack.has_nvidia():
            note, button = "ต้องมีการ์ดจอ NVIDIA จึงจะใช้ได้", None
        else:
            note = "ให้ซับไตเติล AI ทำงานบนการ์ดจอ NVIDIA เร็วขึ้นราว 2.5 เท่า ขนาดดาวน์โหลดประมาณ 1.3 GB"
            button = "ดาวน์โหลด"
        self.pack_note.setText(note)
        self.pack_button.setVisible(button is not None)
        if button:
            self.pack_button.setText(button)
            self.pack_button.setObjectName("SettingsGhost" if installed else "SettingsAction")
            self.pack_button.style().unpolish(self.pack_button)
            self.pack_button.style().polish(self.pack_button)

    def _pack_clicked(self) -> None:
        if gpu_pack.installed():
            self.gpu_pack_uninstall.emit()
        else:
            self.gpu_pack_install.emit()

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

    def _choose_language(self, lang: str) -> None:
        self._settings.setValue("ui/language", lang)
        self.language_changed.emit(lang)

    def _on_switch(self, on: bool) -> None:
        self.gpu_box.setVisible(on)
        if on:
            fade_in(self.gpu_box, 220)
        self.layout().activate()
        self.size_changed.emit()  # MainWindow animates the popup to its new height
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
        self.layout().activate()
        self.size_changed.emit()
