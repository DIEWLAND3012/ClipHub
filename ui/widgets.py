"""Shared building blocks for the liquid-glass UI: backdrop, glass surfaces and page chrome."""
from __future__ import annotations

import math

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, QSize, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui import theme


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
              "toast": (6, 3, 6, 14), "pill": (6, 3, 6, 14)}

    def __init__(self, level: str = "card", radius: float = theme.RADIUS_LG, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("GlassCard")
        self.level = level
        self.radius = radius
        self.setContentsMargins(*self.SHADOW[level])
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

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
        else:
            top, bottom = theme.color("glass_top"), theme.color("glass_bottom")
        fill = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        fill.setColorAt(0.0, top)
        fill.setColorAt(1.0, bottom)
        painter.fillPath(shape, fill)

        # Specular sheen across the upper part: the "liquid" highlight of the glass.
        sheen = QLinearGradient(rect.topLeft(), QPointF(rect.left(), rect.top() + min(rect.height(), 140)))
        sheen.setColorAt(0.0, theme.color("sheen"))
        clear = theme.color("sheen", 0.0)
        sheen.setColorAt(1.0, clear)
        painter.fillPath(shape, sheen)

        # Outer hairline gives the edge definition, the inner rim catches the light from above.
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(theme.color("edge"), 1.0))
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        # Liquid-glass rim: bright where light enters at the top, faint at the sides, and a
        # softer refracted glow along the bottom edge.
        top_light = theme.color("rim_top")
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


def primary_button(text: str, icon: str = "") -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("PrimaryButton")
    button.setCursor(Qt.CursorShape.PointingHandCursor)
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
        self._anim.setDuration(240)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
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


class ElidedLabel(QLabel):
    """Single-line label that shows an ellipsis instead of widening its parent."""

    def __init__(self, text: str = "", mode: Qt.TextElideMode = Qt.TextElideMode.ElideRight,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mode = mode
        self._full = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.set_full_text(text)

    def set_full_text(self, text: str) -> None:
        self._full = text
        self.setToolTip(text)
        self._elide()

    def full_text(self) -> str:
        return self._full

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        width = max(self.width() - 2, 10)
        super().setText(self.fontMetrics().elidedText(self._full, self._mode, width))
