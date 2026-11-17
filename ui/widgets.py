"""Shared building blocks for the liquid-glass UI: backdrop, glass surfaces and page chrome."""
from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve, QEvent, QObject, QPoint, QPointF, QPropertyAnimation, QRectF, QSize, Qt, QTimer,
    QVariantAnimation, Signal,
)
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient
from PySide6.QtWidgets import (
    QBoxLayout,
    QButtonGroup,
    QFileDialog,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QProgressBar,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui import i18n, theme


class Backdrop(QWidget):
    """Window wallpaper: a soft base colour with blurred colour fields behind the glass."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Backdrop")
        self._cache: QPixmap | None = None
        theme.on_change(self._invalidate)

    def _invalidate(self) -> None:
        self._cache = None
        self.update()

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        self._cache = None
        super().resizeEvent(event)

    def _render(self) -> QPixmap:
        ratio = self.devicePixelRatioF()
        pixmap = QPixmap(QSize(max(1, int(self.width() * ratio)), max(1, int(self.height() * ratio))))
        pixmap.setDevicePixelRatio(ratio)
        t = theme.tokens()
        pixmap.fill(QColor(t["bg"]))  # type: ignore[arg-type]
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        diagonal = math.hypot(w, h)
        for x, y, radius, tint in t["blobs"]:  # type: ignore[misc]
            center = QPointF(w * x, h * y)
            gradient = QRadialGradient(center, diagonal * radius)
            gradient.setColorAt(0.0, tint)
            faded = QColor(tint)
            faded.setAlphaF(tint.alphaF() * 0.35)
            gradient.setColorAt(0.45, faded)
            faded = QColor(tint)
            faded.setAlpha(0)
            gradient.setColorAt(1.0, faded)
            painter.fillRect(self.rect(), gradient)
        painter.end()
        return pixmap

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if self._cache is None or self._cache.deviceIndependentSize().toSize() != self.size():
            self._cache = self._render()
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._cache)


class GlassCard(QWidget):
    """A frosted, rim-lit rounded surface with a soft drop shadow.

    level "card" is a content card, "panel" the sidebar / queue rail, "inner" a nested
    surface (no shadow). The shadow lives in the widget's own contents margins, so the
    layout inside is unaffected.
    """

    SHADOW = {"card": (3, 2, 3, 8), "panel": (4, 3, 4, 10), "inner": (0, 0, 0, 0),
              "toast": (6, 3, 6, 14), "pill": (6, 3, 6, 14), "drawer": (8, 4, 4, 16)}

    def __init__(self, level: str = "card", radius: float = theme.RADIUS_LG, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("GlassCard")
        self.level = level
        self.radius = radius
        self.setContentsMargins(*self.SHADOW[level])
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        # Hover: the rim and sheen brighten smoothly, as if the glass catches more light.
        self._hover = 0.0
        self._hover_anim = QVariantAnimation(self)
        self._hover_anim.setDuration(220)
        self._hover_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._hover_anim.valueChanged.connect(self._set_hover)
        self.hover_glow = level in ("card", "toast")

    def _set_hover(self, value: float) -> None:
        self._hover = float(value)
        self.update()

    def _animate_hover(self, target: float) -> None:
        if not self.hover_glow:
            return
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover)
        self._hover_anim.setEndValue(target)
        self._hover_anim.start()

    def enterEvent(self, event) -> None:  # noqa: N802 (Qt API)
        self._animate_hover(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 (Qt API)
        self._animate_hover(0.0)
        super().leaveEvent(event)

    def set_level(self, level: str) -> None:
        """Switch material, e.g. the queue rail becomes a denser floating drawer on small windows."""
        self.level = level
        self.setContentsMargins(*self.SHADOW[level])
        self.update()

    def glass_rect(self) -> QRectF:
        left, top, right, bottom = self.SHADOW[self.level]
        return QRectF(self.rect()).adjusted(left, top, -right, -bottom)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt API)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.glass_rect()
        radius = self.radius
        shape = QPainterPath()
        shape.addRoundedRect(rect, radius, radius)

        if self.level != "inner":
            self._paint_shadow(painter, rect, shape)

        if self.level == "panel":
            top, bottom = theme.color("panel_top"), theme.color("panel_bottom")
        elif self.level == "inner":
            top = bottom = theme.color("inner")
        elif self.level in ("toast", "pill"):  # floats over content, so it needs a denser material
            top, bottom = theme.color("float_top"), theme.color("float_bottom")
        elif self.level == "drawer":  # popups hold lists and settings: almost opaque so text never clashes
            top, bottom = theme.color("float_top", 0.97), theme.color("float_bottom", 0.97)
        else:
            top, bottom = theme.color("glass_top"), theme.color("glass_bottom")
        fill = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        fill.setColorAt(0.0, top)
        fill.setColorAt(1.0, bottom)
        painter.fillPath(shape, fill)

        # Specular sheen across the upper part: the "liquid" highlight of the glass.
        sheen = QLinearGradient(rect.topLeft(), QPointF(rect.left(), rect.top() + min(rect.height(), 140)))
        glow = 1.0 + 0.7 * self._hover
        sheen.setColorAt(0.0, theme.color("sheen", min(theme.color("sheen").alphaF() * glow, 1.0)))
        clear = theme.color("sheen", 0.0)
        sheen.setColorAt(1.0, clear)
        painter.fillPath(shape, sheen)

        # Outer hairline gives the edge definition, the inner rim catches the light from above.
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(theme.color("edge"), 1.0))
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        # Liquid-glass rim: bright where light enters at the top, faint at the sides, and a
        # softer refracted glow along the bottom edge.
        top_light = theme.color("rim_top", min(theme.color("rim_top").alphaF() * glow, 1.0))
        rim = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        rim.setColorAt(0.0, top_light)
        rim.setColorAt(min(0.35, 40 / max(rect.height(), 1)), theme.color("rim_bottom"))
        rim.setColorAt(max(0.65, 1 - 40 / max(rect.height(), 1)), theme.color("rim_bottom"))
        rim.setColorAt(1.0, theme.color("rim_top", top_light.alphaF() * 0.55))
        painter.setPen(QPen(rim, 1.2))
        painter.drawRoundedRect(rect.adjusted(1.1, 1.1, -1.1, -1.1), radius - 1, radius - 1)

    def _paint_shadow(self, painter: QPainter, rect: QRectF, shape: QPainterPath) -> None:
        outside = QPainterPath()
        outside.addRect(QRectF(self.rect()))
        painter.save()
        painter.setClipPath(outside.subtracted(shape))  # like CSS box-shadow: never under the glass
        painter.setPen(Qt.PenStyle.NoPen)
        base = theme.color("shadow")
        left, top, right, bottom = self.SHADOW[self.level]
        steps = max(bottom, 1)
        for step in range(steps, 0, -1):
            spread = step * (left / steps)
            drop = step * 0.45
            tone = QColor(base)
            tone.setAlphaF(base.alphaF() * (1 - step / (steps + 1)) / steps * 2.2)
            painter.setBrush(tone)
            painter.drawRoundedRect(
                rect.adjusted(-spread, -spread * 0.4 + drop, spread, spread + drop),
                self.radius + spread, self.radius + spread,
            )
        painter.restore()


def card(spacing: int = 12, margins: tuple[int, int, int, int] = (20, 18, 20, 20)) -> tuple[GlassCard, QVBoxLayout]:
    frame = GlassCard("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    return frame, layout


def label(text: str = "", name: str = "", wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def field_label(text: str) -> QLabel:
    return label(text, "FieldLabel")


def page_header(title: str) -> QHBoxLayout:
    """Large title only: the controls below explain themselves."""
    row = QHBoxLayout()
    row.setContentsMargins(4, 0, 4, 6)
    row.addWidget(label(title, "PageTitle"))
    row.addStretch(1)
    return row


def animate_value(bar: QProgressBar, value: int, duration: int = 320) -> None:
    """Glide a progress bar to its new value instead of jumping."""
    anim = getattr(bar, "_value_anim", None)
    if anim is None:
        anim = QPropertyAnimation(bar, b"value", bar)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        bar._value_anim = anim  # type: ignore[attr-defined]
    if value <= bar.value():  # resets and finished bars snap
        anim.stop()
        bar.setValue(value)
        return
    anim.stop()
    anim.setDuration(duration)
    anim.setStartValue(bar.value())
    anim.setEndValue(value)
    anim.start()


def fade_in(widget: QWidget, duration: int = 260) -> None:
    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(duration)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    anim.finished.connect(lambda: widget.setGraphicsEffect(None))  # crisp text once settled
    anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)


def reveal(widget: QWidget, duration: int = 340) -> None:
    """Show a hidden widget by growing its height from zero while it fades in."""
    if widget.isVisible():
        return
    widget.setMaximumHeight(0)
    widget.show()
    target = widget.sizeHint().height()
    grow = QPropertyAnimation(widget, b"maximumHeight", widget)
    grow.setDuration(duration)
    grow.setStartValue(0)
    grow.setEndValue(target)
    grow.setEasingCurve(QEasingCurve.Type.OutCubic)
    grow.finished.connect(lambda: widget.setMaximumHeight(16777215))
    grow.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
    fade_in(widget, duration)


def pulse(button: QWidget) -> None:
    """A soft accent glow that blooms and fades around a button, for a confirmed action.

    One glow per button: a new pulse while one is running just restarts it.
    """
    anim = getattr(button, "_pulse_anim", None)
    if anim is not None:
        anim.stop()
        anim.start()
        return
    glow = QGraphicsDropShadowEffect(button)
    glow.setOffset(0, 0)
    glow.setColor(theme.color("accent", 0.75))
    glow.setBlurRadius(0)
    button.setGraphicsEffect(glow)
    anim = QVariantAnimation(button)
    anim.setDuration(620)
    anim.setKeyValueAt(0.0, 0.0)
    anim.setKeyValueAt(0.3, 34.0)
    anim.setKeyValueAt(1.0, 0.0)
    anim.setEasingCurve(QEasingCurve.Type.OutQuad)
    anim.valueChanged.connect(lambda v: glow.setBlurRadius(float(v)))

    def done() -> None:
        button._pulse_anim = None  # type: ignore[attr-defined]
        button.setGraphicsEffect(None)
        anim.deleteLater()

    anim.finished.connect(done)
    button._pulse_anim = anim  # type: ignore[attr-defined]
    anim.start()


def primary_button(text: str, icon: str = "") -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("PrimaryButton")
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.clicked.connect(lambda: pulse(button))
    if icon:
        button.setIconSize(QSize(18, 18))

        def refresh() -> None:
            button.setIcon(theme.themed_icon(icon, "on_accent", 18, 2.0))

        theme.on_change(refresh)
    return button


def bind_icon(button: QPushButton, icon: str, role: str = "text2", size: int = 18,
              checked_role: str | None = None, stroke: float = 1.8) -> None:
    """Keep a button's icon in the theme colour (and an accent colour while checked)."""
    button.setIconSize(QSize(size, size))

    def refresh(*_args: object) -> None:
        tone = checked_role if checked_role and button.isChecked() else role
        button.setIcon(theme.themed_icon(icon, tone, size, stroke))

    theme.on_change(refresh)
    if checked_role:
        button.toggled.connect(refresh)


class Segmented(QFrame):
    """Apple-style segmented control: checkable buttons over a thumb that slides between them."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Segmented")
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(3, 3, 3, 3)
        self._row.setSpacing(2)
        self._thumb = QRectF()
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(380)
        self._anim.setEasingCurve(QEasingCurve.Type.OutQuint)  # fast start, long gentle settle
        self._anim.valueChanged.connect(self._set_thumb)
        self.group.buttonToggled.connect(self._on_toggled)

    def add(self, text: str, button_id: int = -1, icon: str = "", tooltip: str = "") -> QPushButton:
        button = QPushButton(text)
        button.setProperty("seg", True)
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        if tooltip:
            button.setToolTip(tooltip)
        if icon:
            bind_icon(button, icon, "text2", 16, checked_role="accent")
        self.group.addButton(button, button_id)
        self._row.addWidget(button, 1)
        return button

    def clear(self) -> None:
        """Remove every option (used when the choices depend on another selection)."""
        for button in list(self.group.buttons()):
            self.group.removeButton(button)
            self._row.removeWidget(button)
            button.hide()
            button.deleteLater()
        self._anim.stop()
        self._thumb = QRectF()
        self.update()

    def refresh_thumb(self) -> None:
        """Place the thumb once the layout has given the buttons their geometry."""
        QTimer.singleShot(0, lambda: self._set_thumb(self._target()))

    def _target(self) -> QRectF:
        button = self.group.checkedButton()
        if button is None or button.isHidden():
            return QRectF()
        return QRectF(button.geometry())

    def _set_thumb(self, rect: QRectF) -> None:
        self._thumb = QRectF(rect)
        self.update()

    def _on_toggled(self, _button: QPushButton, checked: bool) -> None:
        if not checked:
            return
        target = self._target()
        if not self.isVisible() or self._thumb.isNull() or target.isNull():
            self._anim.stop()
            self._set_thumb(target)
            return
        self._anim.stop()
        self._anim.setStartValue(QRectF(self._thumb))
        self._anim.setEndValue(target)
        self._anim.start()

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().resizeEvent(event)
        QTimer.singleShot(0, lambda: self._set_thumb(self._target()))

    def showEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().showEvent(event)
        QTimer.singleShot(0, lambda: self._set_thumb(self._target()))

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().paintEvent(event)  # the track comes from the stylesheet
        if self._thumb.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self._thumb.adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.color("shadow", theme.color("shadow").alphaF() * 0.9))
        painter.drawRoundedRect(rect.translated(0, 1), 8, 8)
        painter.setBrush(theme.color("seg_thumb"))
        painter.setPen(QPen(theme.color("seg_thumb_border"), 1))
        painter.drawRoundedRect(rect, 8, 8)


class NameField(QFrame):
    """Editable output name with its extension after it.

    It starts with the app's suggested name; once the user types, suggestions no longer
    overwrite it, and "ใช้ชื่อเดิม" brings the suggestion back. An emptied field falls back too.
    """

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Control")
        self.setFixedHeight(theme.CONTROL_HEIGHT)
        self._default = ""
        self.edited = False
        self.edit = QLineEdit()
        self.edit.setObjectName("NameEdit")
        self.edit.setPlaceholderText("ชื่อไฟล์")
        self.edit.installEventFilter(self)
        self.ext_label = QLabel()
        self.ext_label.setObjectName("Unit")
        self.reset_button = QPushButton("ใช้ชื่อเดิม")
        self.reset_button.setObjectName("RailClear")
        self.reset_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reset_button.hide()
        self.reset_button.clicked.connect(self.reset)
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 0, 10, 0)
        row.setSpacing(6)
        row.addWidget(self.edit, 1)
        row.addWidget(self.ext_label)
        row.addWidget(self.reset_button)
        self.edit.textEdited.connect(self._on_edited)
        self.edit.textChanged.connect(lambda _t: self.changed.emit())
        self.edit.editingFinished.connect(self._on_finished)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt API) - focus ring on the whole field
        if obj is self.edit and event.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            self.setProperty("active", event.type() == QEvent.Type.FocusIn)
            theme.repolish(self)
        return False

    def _on_edited(self, _text: str) -> None:
        self.edited = True
        self.reset_button.setVisible(self.edit.text().strip() != self._default)

    def _on_finished(self) -> None:
        if not self.edit.text().strip():
            self.reset()

    def set_default(self, name: str) -> None:
        """The app's suggestion. Replaces the text only while the user has not edited it."""
        self._default = name
        if not self.edited or not self.edit.text().strip():
            self.edited = False
            self.edit.setText(name)
            self.edit.setCursorPosition(0)
            self.reset_button.hide()

    def reset(self) -> None:
        self.edited = False
        self.edit.setText(self._default)
        self.edit.setCursorPosition(0)
        self.reset_button.hide()

    def set_extension(self, ext: str) -> None:
        self.ext_label.setText(f".{ext.lstrip('.')}" if ext else "")

    def name(self) -> str:
        return self.edit.text().strip() or self._default


class ElidedLabel(QLabel):
    """Single-line label that shows an ellipsis instead of widening its parent."""

    def __init__(self, text: str = "", mode: Qt.TextElideMode = Qt.TextElideMode.ElideRight,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mode = mode
        self._full = ""
        self._source = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.set_full_text(text)

    def set_full_text(self, text: str) -> None:
        self._source = text  # the Thai source, kept for live language switches
        self._full = i18n.tr(text)
        self.setToolTip(text)
        self._elide()

    def retranslate(self) -> None:
        self.set_full_text(self._source)

    def full_text(self) -> str:
        return self._full

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        width = max(self.width() - 2, 10)
        elided = self.fontMetrics().elidedText(self._full, self._mode, width)
        if i18n.installed():
            i18n.raw_label_text(self, elided)  # display text only: not a new source to translate
        else:
            super().setText(elided)


def pop_in(widget: QWidget, delay: int = 0, rise: int = 18, duration: int = 420) -> None:
    """Show a widget with a soft rise and fade, like a card popping into place (staggered with delay)."""
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(0.0)
    widget.setGraphicsEffect(effect)
    widget.show()

    def start() -> None:
        if widget.graphicsEffect() is not effect:
            return
        end = widget.pos()
        move = QPropertyAnimation(widget, b"pos", widget)
        move.setDuration(duration)
        move.setStartValue(end + QPoint(0, rise))
        move.setEndValue(end)
        move.setEasingCurve(QEasingCurve.Type.OutBack)
        fade = QPropertyAnimation(effect, b"opacity", widget)
        fade.setDuration(duration - 80)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade.finished.connect(lambda: widget.setGraphicsEffect(None))
        move.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        fade.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    QTimer.singleShot(max(delay, 0) + 16, start)  # after the layout has placed the widget


class Columns(QWidget):
    """Two columns side by side that stack into one on narrow content areas."""

    def __init__(self, breakpoint: int = 820, stretch: tuple[int, int] = (1, 1), parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.breakpoint = breakpoint
        self._box = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self._box.setContentsMargins(0, 0, 0, 0)
        self._box.setSpacing(16)
        self.left = QVBoxLayout()
        self.left.setSpacing(16)
        self.right = QVBoxLayout()
        self.right.setSpacing(16)
        self._box.addLayout(self.left, stretch[0])
        self._box.addLayout(self.right, stretch[1])

    def _side_by_side_width(self) -> int:
        """Narrowest width at which both columns still fit next to each other."""
        return self.left.minimumSize().width() + self.right.minimumSize().width() + self._box.spacing()

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt API)
        # Never demand room for both columns: below that width they stack instead of being clipped.
        width = max(self.left.minimumSize().width(), self.right.minimumSize().width())
        return QSize(width, super().minimumSizeHint().height())

    def _update_direction(self) -> None:
        wide = self.width() >= max(self.breakpoint, self._side_by_side_width())
        direction = QBoxLayout.Direction.LeftToRight if wide else QBoxLayout.Direction.TopToBottom
        if self._box.direction() != direction:
            self._box.setDirection(direction)
            self.updateGeometry()

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().resizeEvent(event)
        self._update_direction()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().showEvent(event)
        self._update_direction()


class ActionBar(QWidget):
    """A page's summary text and action buttons. MainWindow shows it in the same place on every page."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(12)
        self.hint = ElidedLabel()
        self.hint.setObjectName("Hint")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._row.addWidget(self.hint, 1)
        self._summary = ""
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(lambda: self.hint.set_full_text(self._summary))

    def add_button(self, button: QPushButton) -> QPushButton:
        self._row.addWidget(button)
        return button

    def set_summary(self, text: str) -> None:
        self._summary = text
        if not self._flash_timer.isActive():
            self.hint.set_full_text(text)

    def flash(self, text: str, ms: int = 3500) -> None:
        """Show a short confirmation (e.g. 'added to the queue') before going back to the summary."""
        self.hint.set_full_text(text)
        fade_in(self.hint, 200)
        self._flash_timer.start(ms)


def local_paths(event) -> list[str]:
    return [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]


def handle_drop(event, on_file, on_error) -> None:
    """Accept one dropped local file; explain folders, remote files and multiple files."""
    paths = local_paths(event)
    if not paths:
        on_error("วางได้เฉพาะไฟล์จากเครื่องนี้")
        return
    if Path(paths[0]).is_dir():
        on_error("ไม่รองรับโฟลเดอร์ กรุณาเลือกไฟล์เดียว")
        return
    event.acceptProposedAction()
    on_file(paths[0])
    if len(paths) > 1:
        on_error(f"รับครั้งละ 1 ไฟล์ — ใช้ไฟล์แรก ({Path(paths[0]).name})")


class DropZone(QFrame):
    """Dashed area to drop a file on or click to pick one (the app's single file-picking style)."""

    file_chosen = Signal(str)
    rejected = Signal(str)

    def __init__(self, title: str, file_filter: str, dialog_title: str = "เลือกไฟล์",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DropZone")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAcceptDrops(True)
        self.file_filter = file_filter
        self.dialog_title = dialog_title
        self.start_dir = str(Path.home())
        self._icon = QLabel()
        self._icon.setObjectName("DzIcon")
        self._icon.setFixedSize(60, 60)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 34, 24, 34)
        layout.setSpacing(8)
        layout.addStretch(1)
        layout.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(label(title, "DzTitle"), 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(label("หรือคลิกเพื่อเลือกไฟล์", "DzSub"), 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)
        theme.on_change(self._refresh_icon)

    def _refresh_icon(self) -> None:
        self._icon.setPixmap(theme.icon_pixmap("upload", theme.hex_color("accent"), 28, 2.0))

    def browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self.window(), self.dialog_title, self.start_dir, self.file_filter)
        if path:
            self.start_dir = str(Path(path).parent)
            self.file_chosen.emit(path)

    def set_drag(self, active: bool) -> None:
        self.setProperty("drag", active)
        theme.repolish(self)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if event.button() == Qt.MouseButton.LeftButton:
            self.browse()
        super().mousePressEvent(event)

    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if local_paths(event):
            event.acceptProposedAction()
            self.set_drag(True)
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802 (Qt API)
        self.set_drag(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802 (Qt API)
        self.set_drag(False)
        handle_drop(event, self.file_chosen.emit, self.rejected.emit)


class PageDrop(QObject):
    """Lets a whole page take a dropped file (e.g. to replace the current one)."""

    def __init__(self, page: QWidget, on_file, on_error) -> None:
        super().__init__(page)
        self._on_file, self._on_error = on_file, on_error
        page.setAcceptDrops(True)
        page.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt API)
        kind = event.type()
        if kind in (QEvent.Type.DragEnter, QEvent.Type.DragMove) and local_paths(event):
            event.acceptProposedAction()
            return True
        if kind == QEvent.Type.Drop:
            handle_drop(event, self._on_file, self._on_error)
            return True
        return False
