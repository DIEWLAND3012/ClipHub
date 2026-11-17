"""Glass sidebar: brand, tool navigation with a sliding highlight, and the appearance switch.

It collapses to an icon rail on narrow windows (set_compact), animating its width.
"""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ui import theme
from ui.app_icon import IconView
from ui.theme import repolish
from ui.widgets import GlassCard, Segmented, bind_icon, label

APPEARANCE_ICONS = {"system": "auto", "light": "sun", "dark": "moon"}


class Sidebar(GlassCard):
    page_selected = Signal(int)
    appearance_selected = Signal(str)
    performance_clicked = Signal()
    queue_clicked = Signal()

    FULL_WIDTH = 240
    COMPACT_WIDTH = 78

    def __init__(self, pages: list[tuple[str, str]], version: str, parent: QWidget | None = None) -> None:
        super().__init__("panel", radius=24, parent=parent)
        self.setObjectName("Sidebar")
        self.compact = False
        self._labels = [text for text, _icon in pages]
        self.setFixedWidth(self.FULL_WIDTH)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 18, 12, 14)
        layout.setSpacing(3)

        mark = IconView(42)  # the ClipHUB app icon
        self.brand_texts = QWidget()
        texts = QVBoxLayout(self.brand_texts)
        texts.setContentsMargins(0, 0, 0, 0)
        texts.setSpacing(0)
        texts.addWidget(label("ClipHUB", "BrandName"))
        texts.addWidget(label(version, "BrandVersion"))
        self.brand = QHBoxLayout()
        self.brand.setContentsMargins(7, 0, 6, 18)
        self.brand.setSpacing(11)
        self.brand.addStretch(0)  # grows in compact mode so the icon sits in the middle
        self.brand.addWidget(mark)
        self.brand.addWidget(self.brand_texts)
        self.brand.addStretch(1)
        layout.addLayout(self.brand)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: list[QPushButton] = []
        for index, (text, icon_key) in enumerate(pages):
            button = QPushButton(text)
            button.setProperty("nav", True)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(f"{text}  (Ctrl+{index + 1})")
            bind_icon(button, icon_key, "accent", 19)
            self.group.addButton(button, index)
            self.buttons.append(button)
            layout.addWidget(button)
        self.group.idClicked.connect(self.page_selected)
        self.group.buttonToggled.connect(self._on_toggled)
        layout.addStretch(1)

        # Queue: opens as a popup; the badge counts unfinished jobs.
        self.queue_button = QPushButton("คิวงาน")
        self.queue_button.setProperty("nav", True)
        self.queue_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.queue_button.setToolTip("คิวงาน")
        bind_icon(self.queue_button, "queue", "text2", 19)
        self.queue_button.clicked.connect(self.queue_clicked)
        badge_row = QHBoxLayout(self.queue_button)
        badge_row.setContentsMargins(0, 0, 10, 0)
        badge_row.addStretch(1)
        self.queue_badge = QLabel()
        self.queue_badge.setObjectName("NavBadge")
        self.queue_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.queue_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.queue_badge.hide()
        badge_row.addWidget(self.queue_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.queue_button)

        self.perf_button = QPushButton("การตั้งค่า")
        self.perf_button.setProperty("nav", True)
        self.perf_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.perf_button.setToolTip("การตั้งค่า")
        bind_icon(self.perf_button, "gear", "text2", 19)
        self.perf_button.clicked.connect(self.performance_clicked)
        layout.addWidget(self.perf_button)
        layout.addSpacing(6)

        manager = theme.manager()
        self._appearance = manager.appearance if manager is not None else "system"
        self.appearance = Segmented()
        self.appearance.setFixedHeight(40)  # never stretched by a tall (full-screen) sidebar
        for index, key in enumerate(theme.APPEARANCES):
            button = self.appearance.add("", index, APPEARANCE_ICONS[key], theme.APPEARANCE_LABELS[key])
            button.setIconSize(QSize(16, 16))
            button.setChecked(key == self._appearance)
        self.appearance.group.idClicked.connect(lambda i: self._choose_appearance(theme.APPEARANCES[i]))
        layout.addWidget(self.appearance)
        # Compact mode has no room for three segments: one button cycles system -> light -> dark.
        self.cycle_button = QPushButton()
        self.cycle_button.setProperty("nav", True)
        self.cycle_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cycle_button.clicked.connect(self._cycle_appearance)
        self.cycle_button.hide()
        layout.addWidget(self.cycle_button)
        theme.on_change(self._refresh_cycle_icon)

        # Selection highlight that glides between items (painted under the transparent buttons).
        self._highlight = QRectF()
        self._move = QVariantAnimation(self)
        self._move.setDuration(320)
        self._move.setEasingCurve(QEasingCurve.Type.OutBack)  # a little overshoot, like a spring
        self._move.valueChanged.connect(self._set_highlight)
        self._width_anim = QVariantAnimation(self)
        self._width_anim.setDuration(280)
        self._width_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._width_anim.valueChanged.connect(lambda w: self.setFixedWidth(int(w)))
        self._width_anim.finished.connect(self._snap_highlight)

    # --- navigation highlight ---------------------------------------------------
    def select(self, index: int) -> None:
        self.buttons[index].setChecked(True)

    def _target(self) -> QRectF:
        button = self.group.checkedButton()
        return QRectF(button.geometry()) if button is not None else QRectF()

    def _set_highlight(self, rect: QRectF) -> None:
        self._highlight = QRectF(rect)
        self.update()

    def _snap_highlight(self) -> None:
        self._move.stop()
        self._set_highlight(self._target())

    def _on_toggled(self, _button: QPushButton, checked: bool) -> None:
        if not checked:
            return
        target = self._target()
        if self._highlight.isNull() or not self.isVisible():
            self._set_highlight(target)
            return
        self._move.stop()
        self._move.setStartValue(QRectF(self._highlight))
        self._move.setEndValue(target)
        self._move.start()

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().resizeEvent(event)
        QTimer.singleShot(0, self._snap_highlight)

    def showEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().showEvent(event)
        QTimer.singleShot(0, self._snap_highlight)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().paintEvent(event)  # the glass panel
        if self._highlight.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.color("accent_soft"))
        painter.drawRoundedRect(self._highlight, 11, 11)

    # --- compact mode -------------------------------------------------------------
    def set_compact(self, compact: bool, animate: bool = True) -> None:
        if compact == self.compact:
            return
        self.compact = compact
        self.brand_texts.setVisible(not compact)
        self.brand.setStretch(0, 1 if compact else 0)  # centre the icon when only icons show
        self.brand.setContentsMargins(0 if compact else 7, 0, 0 if compact else 6, 18)
        self.appearance.setVisible(not compact)
        self.cycle_button.setVisible(compact)
        for button, text in zip(self.buttons + [self.cycle_button, self.queue_button, self.perf_button],
                                self._labels + ["", "คิวงาน", "การตั้งค่า"]):
            button.setText("" if compact else text)
            button.setProperty("compact", compact)
            repolish(button)
        target = self.COMPACT_WIDTH if compact else self.FULL_WIDTH
        if not animate:
            self.setFixedWidth(target)
            self._snap_highlight()
            return
        self._width_anim.stop()
        self._width_anim.setStartValue(self.width())
        self._width_anim.setEndValue(target)
        self._width_anim.start()

    def set_queue_count(self, count: int) -> None:
        """Unfinished jobs on the queue button; a small dot-sized badge in compact mode."""
        self.queue_badge.setText(str(count))
        self.queue_badge.setVisible(count > 0)
        self.queue_badge.setProperty("compact", self.compact)
        size = 14 if self.compact else 18  # radius in the stylesheet is half of this: a true capsule
        self.queue_badge.setFixedHeight(size)
        self.queue_badge.setMinimumWidth(size)
        repolish(self.queue_badge)

    # --- appearance ------------------------------------------------------------
    def _choose_appearance(self, key: str) -> None:
        self._appearance = key
        self.appearance.group.button(theme.APPEARANCES.index(key)).setChecked(True)
        self._refresh_cycle_icon()
        self.appearance_selected.emit(key)

    def _cycle_appearance(self) -> None:
        order = theme.APPEARANCES
        self._choose_appearance(order[(order.index(self._appearance) + 1) % len(order)])

    def _refresh_cycle_icon(self) -> None:
        self.cycle_button.setIcon(theme.themed_icon(APPEARANCE_ICONS[self._appearance], "accent", 18))
        self.cycle_button.setIconSize(QSize(18, 18))
        self.cycle_button.setToolTip(f"ธีม · {theme.APPEARANCE_LABELS[self._appearance]} (คลิกเพื่อเปลี่ยน)")
