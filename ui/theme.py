"""Light / dark "liquid glass" theme in the style of Apple's apps (owner: member 1).

Colours live in two token sets (LIGHT, DARK). ThemeManager picks one from the saved
appearance ("system", "light" or "dark"), rebuilds the application stylesheet and emits
`changed`, so widgets that paint or render icons themselves can refresh through on_change().
"""
from __future__ import annotations

import tempfile
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from string import Template

from PySide6.QtCore import QObject, QRectF, QSettings, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QApplication, QComboBox, QStyledItemDelegate, QWidget

APPEARANCES = ("system", "light", "dark")
APPEARANCE_LABELS = {"system": "ตามระบบ", "light": "สว่าง", "dark": "มืด"}

# Apple's own faces on macOS (SF Pro + Sukhumvit Set). They may not be redistributed, so other
# platforms use the bundled open look-alikes from assets/fonts: Inter (Latin) + Noto Sans Thai.
BUNDLED_FAMILIES = ["Inter", "Noto Sans Thai"]
SANS = '"Inter", "Noto Sans Thai", sans-serif'  # replaced by load_fonts() with the platform list
MONO = '"SF Mono", "Cascadia Mono", "Menlo", "Consolas", monospace'

RADIUS_SM = 9
RADIUS = 12
RADIUS_LG = 22
CONTROL_HEIGHT = 44


def rgba(hex_color: str, alpha: float = 1.0) -> QColor:
    color = QColor(hex_color)
    color.setAlphaF(alpha)
    return color


def css(color: QColor) -> str:
    """QSS colour with 0-255 alpha (the form Qt's stylesheet parser reads reliably)."""
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {color.alpha()})"


# Apple system colours (light / dark variants) plus glass materials.
LIGHT: dict[str, object] = {
    "dark": False,
    "bg": rgba("#eef0f6"),
    "blobs": (  # (x, y, radius as share of the window diagonal, colour)
        (0.12, 0.02, 0.60, rgba("#5aa2ff", 0.80)),
        (0.98, 0.18, 0.55, rgba("#c58cff", 0.70)),
        (0.55, 1.05, 0.60, rgba("#ffb46b", 0.62)),
        (0.02, 0.92, 0.50, rgba("#5fd6cf", 0.55)),
    ),
    "text": rgba("#1d1d1f"),
    "text2": rgba("#6e6e73"),
    "text3": rgba("#a1a1a6"),
    "accent": rgba("#007aff"),
    "accent_hover": rgba("#1a88ff"),
    "accent_press": rgba("#0062cc"),
    "on_accent": rgba("#ffffff"),
    "accent_soft": rgba("#007aff", 0.12),
    "accent_line": rgba("#007aff", 0.45),
    "glass_top": rgba("#ffffff", 0.58),
    "glass_bottom": rgba("#ffffff", 0.30),
    "panel_top": rgba("#ffffff", 0.50),
    "panel_bottom": rgba("#ffffff", 0.26),
    "inner": rgba("#ffffff", 0.50),
    "float_top": rgba("#ffffff", 0.90),
    "float_bottom": rgba("#f7f7fa", 0.82),
    "rim_top": rgba("#ffffff", 1.0),
    "rim_bottom": rgba("#ffffff", 0.25),
    "edge": rgba("#000000", 0.07),
    "sheen": rgba("#ffffff", 0.60),
    "shadow": rgba("#1c2440", 0.10),
    "fill": rgba("#767680", 0.10),
    "fill_hover": rgba("#767680", 0.16),
    "field_border": rgba("#000000", 0.07),
    "ghost": rgba("#ffffff", 0.75),
    "ghost_hover": rgba("#ffffff", 0.95),
    "ghost_border": rgba("#000000", 0.09),
    "seg_track": rgba("#767680", 0.12),
    "seg_thumb": rgba("#ffffff"),
    "seg_thumb_border": rgba("#000000", 0.06),
    "nav_hover": rgba("#000000", 0.05),
    "separator": rgba("#000000", 0.08),
    "popup": rgba("#fbfbfd"),
    "scroll": rgba("#000000", 0.14),
    "scroll_hover": rgba("#000000", 0.38),
    "ok": rgba("#28a745"),
    "err": rgba("#ff3b30"),
    "warn": rgba("#ff9500"),
    "blue": rgba("#007aff"),
    "orange": rgba("#ff9500"),
    "pink": rgba("#ff2d55"),
    "purple": rgba("#af52de"),
    "switch_on": rgba("#34c759"),
    "knob": rgba("#ffffff"),
}

DARK: dict[str, object] = {
    "dark": True,
    "bg": rgba("#0b0b10"),
    "blobs": (
        (0.10, 0.02, 0.60, rgba("#1665ff", 0.62)),
        (0.98, 0.15, 0.55, rgba("#9b3be0", 0.52)),
        (0.55, 1.05, 0.60, rgba("#ff7a1a", 0.32)),
        (0.02, 0.95, 0.48, rgba("#0fb3a8", 0.34)),
    ),
    "text": rgba("#f5f5f7"),
    "text2": rgba("#a1a1aa"),
    "text3": rgba("#86868e"),
    "accent": rgba("#0a84ff"),
    "accent_hover": rgba("#3a9bff"),
    "accent_press": rgba("#0070dd"),
    "on_accent": rgba("#ffffff"),
    "accent_soft": rgba("#0a84ff", 0.20),
    "accent_line": rgba("#0a84ff", 0.55),
    "glass_top": rgba("#ffffff", 0.10),
    "glass_bottom": rgba("#ffffff", 0.035),
    "panel_top": rgba("#ffffff", 0.08),
    "panel_bottom": rgba("#ffffff", 0.025),
    "inner": rgba("#ffffff", 0.06),
    "float_top": rgba("#3a3a42", 0.90),
    "float_bottom": rgba("#26262c", 0.88),
    "rim_top": rgba("#ffffff", 0.42),
    "rim_bottom": rgba("#ffffff", 0.07),
    "edge": rgba("#000000", 0.35),
    "sheen": rgba("#ffffff", 0.11),
    "shadow": rgba("#000000", 0.28),
    "fill": rgba("#767680", 0.22),
    "fill_hover": rgba("#767680", 0.30),
    "field_border": rgba("#ffffff", 0.08),
    "ghost": rgba("#ffffff", 0.10),
    "ghost_hover": rgba("#ffffff", 0.16),
    "ghost_border": rgba("#ffffff", 0.14),
    "seg_track": rgba("#767680", 0.24),
    "seg_thumb": rgba("#636366", 0.95),
    "seg_thumb_border": rgba("#ffffff", 0.10),
    "nav_hover": rgba("#ffffff", 0.06),
    "separator": rgba("#ffffff", 0.09),
    "popup": rgba("#2c2c30"),
    "scroll": rgba("#ffffff", 0.14),
    "scroll_hover": rgba("#ffffff", 0.38),
    "ok": rgba("#30d158"),
    "err": rgba("#ff453a"),
    "warn": rgba("#ff9f0a"),
    "blue": rgba("#0a84ff"),
    "orange": rgba("#ff9f0a"),
    "pink": rgba("#ff375f"),
    "purple": rgba("#bf5af2"),
    "switch_on": rgba("#30d158"),
    "knob": rgba("#ffffff"),
}

KIND_COLOR = {"download": "blue", "convert": "orange", "compress": "pink", "subtitle": "purple"}

_current: dict[str, object] = LIGHT


def tokens() -> dict[str, object]:
    return _current


def color(name: str, alpha: float | None = None) -> QColor:
    """A token as a QColor (copy), optionally with a new alpha."""
    value = QColor(_current[name])  # type: ignore[arg-type]
    if alpha is not None:
        value.setAlphaF(alpha)
    return value


def hex_color(name: str) -> str:
    """Opaque #rrggbb of a token, for SVG icons (QtSvg ignores rgba())."""
    return QColor(_current[name]).name()  # type: ignore[arg-type]


def kind_color(kind: str) -> QColor:
    return color(KIND_COLOR.get(kind, "accent"))


def is_dark() -> bool:
    return bool(_current["dark"])


_STYLE_TEMPLATE = Template("""
QWidget { color: $text; font-size: 14px; }
QMainWindow, QDialog, QMessageBox { background: $popup; }
QLabel { background: transparent; }
QToolTip { background: $popup; color: $text; border: 1px solid $separator; border-radius: 6px; padding: 5px 9px; }

/* sidebar */
QLabel#BrandMark { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #5ac8fa, stop:0.5 #007aff, stop:1 #5e5ce6);
  border-radius: 11px; }
QLabel#BrandName { font-size: 17px; font-weight: 700; }
QLabel#BrandVersion { color: $text3; font-size: 11px; }
QLabel#NavLabel { color: $text3; font-size: 11px; font-weight: 600; padding: 4px 12px 4px 12px; }
QPushButton[nav="true"] { text-align: left; padding: 9px 12px; border: none; border-radius: 10px;
  background: transparent; color: $text; font-size: 14px; font-weight: 500; }
QPushButton[nav="true"]:hover { background: $nav_hover; }
QPushButton[nav="true"]:checked { background: transparent; color: $text; font-weight: 600; }
QPushButton[nav="true"]:checked:hover { background: transparent; }
QPushButton[nav="true"][compact="true"] { text-align: center; padding: 10px 0; }

QLabel#NavBadge { background: $accent; color: #ffffff; border-radius: 9px;
  padding: 0 6px; font-size: 11px; font-weight: 700; }
QLabel#NavBadge[compact="true"] { border-radius: 7px; padding: 0 4px; font-size: 9px; }
/* responsive queue drawer */
QWidget#Scrim { background: $scrim; }
QPushButton#QueueToggle { border-radius: 19px; min-height: 38px; max-height: 38px; padding: 0 16px 0 12px;
  font-size: 13px; font-weight: 600; background: $float_top; border: 1px solid $ghost_border; border-top-color: $btn_rim; }
QPushButton#QueueToggle:hover { background: $ghost_hover; }
QPushButton#QueueToggle[busy="true"] { color: $accent; }

/* page chrome */
QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: none; }
QLabel#PageTitle { font-size: 32px; font-weight: 700; }
QLabel#PageSubtitle { color: $text2; font-size: 14px; }
QLabel#Badge { color: $text2; font-size: 12px; font-weight: 500; padding: 6px 13px; border-radius: 14px;
  background: $ghost; border: 1px solid $ghost_border; }
QLabel#FieldLabel { color: $text2; font-size: 12px; font-weight: 600; }
QLabel#Hint { color: $text2; font-size: 13px; }
QLabel#Note { color: $text3; font-size: 12px; }
QLabel#Status { color: $text2; font-size: 13px; }
QLabel[state="ok"], QLabel#OkText { color: $ok; }
QLabel[state="err"], QLabel#ErrorText { color: $err; }
QLabel#NoticeText { color: $text2; font-size: 13px; }
QLabel#Chip { padding: 4px 12px; border-radius: 12px; color: $text2; font-size: 12px; font-weight: 500;
  background: $fill; border: 1px solid transparent; }
QLabel#Chip[on="true"] { background: $accent_soft; border: 1px solid $accent_line; color: $accent; }
QLabel#Empty { color: $text3; font-size: 13px; padding: 28px 12px; }

/* file rows shared by converter, compressor and subtitle */
QFrame#FileRow { background: $inner; border: 1px solid $separator; border-radius: 14px; }
QLabel#Thumb { border-radius: 10px; background: $fill; color: $text3; font-size: 18px; }
QLabel#FileName { font-size: 14px; font-weight: 600; }
QLabel#FileMeta { color: $text3; font-size: 12px; }
QLabel#FileMeta[error="true"] { color: $err; }
QLabel#FileSize { color: $text2; font-size: 13px; }
QLabel#OutName { font-size: 13px; padding: 0 14px; border-radius: ${r}px; background: $fill; }

/* inputs */
QLineEdit, QComboBox { background: $fill; border: 1px solid $field_border; border-radius: ${r}px;
  padding: 0 14px; min-height: ${control_h}px; color: $text; selection-background-color: $accent; selection-color: #ffffff; }
QLineEdit:hover, QComboBox:hover { background: $fill_hover; }
QLineEdit:focus, QComboBox:focus, QComboBox:on { border: 1px solid $accent; }
QLineEdit:disabled, QComboBox:disabled { color: $text3; }
QComboBox#FmtCombo { min-height: 32px; min-width: 64px; padding: 0 12px; font-size: 13px; }
QComboBoxPrivateContainer { background: transparent; border: none; }
QComboBox QAbstractItemView { background: $popup; color: $text; border: 1px solid $separator; border-radius: ${r}px;
  padding: 5px; outline: 0; selection-background-color: $accent; selection-color: #ffffff; }
QComboBox QAbstractItemView::item { padding: 4px 10px; min-height: 26px; border-radius: 7px; }
QComboBox QAbstractItemView::item:hover { background: $accent_soft; color: $text; }
QComboBox QAbstractItemView::item:selected { background: $accent; color: #ffffff; }
QComboBox QAbstractItemView::item:disabled { color: $text3; background: transparent; }

/* buttons: plain QPushButton is the glass capsule; PrimaryButton is the filled accent capsule */
QPushButton { color: $text; border-radius: 18px; padding: 8px 18px; font-size: 13px; font-weight: 500; min-height: 20px;
  background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 $ghost_hover, stop:1 $ghost);
  border: 1px solid $ghost_border; border-top-color: $btn_rim; }
QPushButton:hover { background: $ghost_hover; }
QPushButton:pressed { background: $fill_hover; border-top-color: $ghost_border; }
QPushButton:disabled { color: $text3; background: $fill; border-color: transparent; }
QPushButton#PrimaryButton { color: $on_accent; border-radius: 22px; min-height: ${control_h}px; padding: 0 26px;
  font-size: 14px; font-weight: 600;
  background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 $accent_top, stop:0.55 $accent, stop:1 $accent_bottom);
  border: 1px solid $accent_edge; border-top-color: $accent_rim; }
QPushButton#PrimaryButton:hover { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 $accent_top, stop:1 $accent_hover); }
QPushButton#PrimaryButton:pressed { background: $accent_press; border-top-color: $accent_edge; }
QPushButton#PrimaryButton:disabled { background: $fill; color: $text3; border-color: transparent; }
QPushButton#FieldButton { border-radius: ${r}px; min-height: ${control_h}px; max-height: ${control_h}px; padding: 0 16px; }

/* segmented control */
QFrame#Segmented { background: $seg_track; border: none; border-radius: 11px; }
QPushButton[seg="true"] { background: transparent; border: 1px solid transparent; border-radius: 8px;
  color: $text; padding: 7px 10px; font-size: 13px; font-weight: 500; min-height: 18px; }
QPushButton[seg="true"]:hover { background: transparent; color: $text; }
QPushButton[seg="true"]:checked { background: transparent; border: 1px solid transparent; font-weight: 600; }
QPushButton[seg="true"]:disabled { color: $text3; }

/* downloader metadata preview */
QFrame#Poster { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 $poster_a, stop:1 $poster_b);
  border: none; border-radius: 14px; }
QLabel#PosterPlay { background: rgba(255, 255, 255, 220); border-radius: 23px; }
QLabel#PosterDuration { background: rgba(0, 0, 0, 150); color: #ffffff; font-size: 11px;
  padding: 2px 7px; border-radius: 6px; }
QLabel#StepNumber { background: $accent_soft; color: $accent; border-radius: 10px; font-size: 11px; font-weight: 700; }
QLabel#MetaTitle { font-size: 15px; font-weight: 600; }
QLabel#MetaRow { color: $text2; font-size: 13px; }

/* converter */
QFrame#DropZone { border: 1.5px dashed $drop_border; border-radius: 18px; background: $drop_bg; }
QFrame#DropZone:hover, QFrame#DropZone[drag="true"] { border-color: $accent; background: $accent_soft; }
QLabel#DzIcon { background: $accent_soft; border-radius: 18px; }
QLabel#DzTitle { font-size: 17px; font-weight: 600; }
QLabel#DzSub { color: $text3; font-size: 13px; }
QLabel#FormatBadge { color: $accent; background: $accent_soft; border: 1px solid $accent_line; border-radius: 18px;
  font-size: 26px; font-weight: 800; padding: 0 16px; }
QLabel#FileTitle { font-size: 16px; font-weight: 700; }
QFrame#InfoTile { background: $fill; border: none; border-radius: 12px; }
QLabel#InfoKey { color: $text3; font-size: 11px; font-weight: 600; }
QLabel#InfoValue { font-size: 13px; font-weight: 600; }
QLabel#ToggleTitle { font-size: 14px; font-weight: 600; }

/* compressor */
QFrame#Control { background: $fill; border: 1px solid $field_border; border-radius: ${r}px; }
QFrame#Control[active="true"] { border: 1px solid $accent; }
QLineEdit#NameEdit { background: transparent; border: none; padding: 0; min-height: 0; font-size: 14px; }
QLineEdit#NameEdit:hover, QLineEdit#NameEdit:focus { background: transparent; border: none; }
QLineEdit#KbpsEdit { background: transparent; border: none; padding: 0; min-height: 0; }
QLabel#Unit { color: $text3; font-size: 13px; }
QLabel#BarLabel { color: $text2; font-size: 13px; }
QLabel#BarAmount { font-size: 13px; }
QLabel#BarAmount[accent="true"] { color: $accent; font-weight: 600; }
QProgressBar#BarOrig, QProgressBar#BarNew { min-height: 12px; max-height: 12px; border-radius: 6px; }
QProgressBar#BarOrig::chunk { background: $text3; border-radius: 6px; }
QProgressBar#BarNew::chunk { background: $accent; border-radius: 6px; }
QFrame#Saving { border-radius: 14px; }
QFrame#Saving[state="idle"] { background: $fill; border: 1px solid transparent; }
QFrame#Saving[state="ok"] { background: $ok_soft; border: 1px solid $ok_line; }
QFrame#Saving[state="warn"] { background: $warn_soft; border: 1px solid $warn_line; }
QLabel#SavingBig { font-size: 30px; font-weight: 700; color: $text3; }
QLabel#SavingBig[state="ok"] { color: $ok; }
QLabel#SavingBig[state="warn"] { color: $warn; }
QLabel#SavingTitle { font-size: 14px; font-weight: 600; }
QLabel#SavingSub { color: $text2; font-size: 12px; }

/* subtitle preview table */
QTableWidget { background: transparent; border: none; color: $text; font-size: 14px; outline: none;
  alternate-background-color: $fill; }
QTableWidget::item { padding: 6px 8px; border: none; }
QTableWidget::item:selected { background: $accent_soft; color: $text; }
QHeaderView { background: transparent; border: none; }
QHeaderView::section { background: transparent; color: $text3; border: none; border-bottom: 1px solid $separator;
  padding: 6px 8px; font-size: 11px; font-weight: 600; }
QTableCornerButton::section { background: transparent; border: none; }

/* queue rail */
QLabel#RailTitle { font-size: 15px; font-weight: 700; }
QLabel#CountBadge { color: $accent; background: $accent_soft; border-radius: 10px; padding: 2px 9px;
  font-size: 11px; font-weight: 600; }
QPushButton#RailClear { background: transparent; border: none; color: $accent; padding: 2px 4px; font-size: 12px;
  min-height: 0; }
QPushButton#RailClear:hover { color: $accent_hover; background: transparent; }
QPushButton#JobOpen { background: $accent_soft; border: none; color: $accent; border-radius: 9px;
  padding: 3px 10px; font-size: 12px; font-weight: 600; min-height: 0; }
QPushButton#JobOpen:hover { background: $accent_line; }
QLabel#RailEmpty { color: $text3; font-size: 13px; padding: 24px 0; }
QLabel#RailFoot { color: $text3; font-size: 11px; }
QFrame#JobCard { background: $inner; border: 1px solid $separator; border-radius: 16px; }
QFrame#JobCard[state="running"] { border: 1px solid $accent_line; }
QFrame#JobCard[state="failed"] { border: 1px solid $err_line; }
QLabel#JobIcon { border-radius: 9px; }
$job_icon_rules
QLabel#JobIcon[state="done"] { background: $ok_soft; }
QLabel#JobIcon[state="failed"] { background: $err_soft; }
QLabel#JobTitle { font-size: 13px; font-weight: 600; }
QLabel#JobSummary { color: $text3; font-size: 11px; }
QLabel#JobStatus { color: $text2; font-size: 11px; }
QLabel#JobPercent { color: $text2; font-size: 11px; }
QPushButton#JobCancel { padding: 0; border-radius: 13px; background: $fill; border: none; min-height: 0; }
QPushButton#JobCancel:hover { background: $err_soft; }
QProgressBar { background: $fill; border: none; border-radius: 3px; min-height: 6px; max-height: 6px; }
QProgressBar::chunk { border-radius: 3px; background: $accent; }
$job_bar_rules
QFrame#JobCard[state="done"] QProgressBar::chunk { background: $ok; }
QFrame#JobCard[state="failed"] QProgressBar::chunk { background: $err; }
QFrame#JobCard[state="cancelled"] QProgressBar::chunk { background: $text3; }

/* bottom progress pill */
QLabel#PillTitle { font-size: 14px; font-weight: 600; }
QLabel#PillStatus { color: $text2; font-size: 12px; }
QLabel#PillPercent { font-size: 20px; font-weight: 700; }
QLabel#PillMore { color: $text2; font-size: 11px; font-weight: 600; background: $fill; border-radius: 10px; padding: 3px 9px; }
QProgressBar#PillBar { min-height: 8px; max-height: 8px; border-radius: 4px; }
QProgressBar#PillBar::chunk { border-radius: 4px; }

/* toasts */
QLabel#ToastBadge { border-radius: 17px; background: $accent_soft; }
QLabel#ToastBadge[kind="success"] { background: $ok_soft; }
QLabel#ToastBadge[kind="error"] { background: $err_soft; }
QLabel#ToastTitle { font-size: 14px; font-weight: 600; }
QLabel#ToastBody { color: $text2; font-size: 13px; }
QPushButton#ToastClose { padding: 0; min-height: 0; border: none; border-radius: 12px; background: transparent; }
QPushButton#ToastClose:hover { background: $fill; }
QPushButton#ToastAction { padding: 5px 14px; min-height: 0; border-radius: 13px; font-size: 12px; font-weight: 600;
  color: $accent; background: $accent_soft; border: none; }
QPushButton#ToastAction:hover { background: $accent_line; color: $on_accent; }

/* performance panel */
QLabel#StatusDot { border-radius: 5px; background: $text3; }
QLabel#StatusDot[state="ok"] { background: $ok; }
QLabel#StatusDot[state="warn"] { background: $warn; }
QWidget#InfoTile { background: $fill; border-radius: 12px; }

/* appearance switcher */
QLabel#AppearanceLabel { color: $text3; font-size: 11px; font-weight: 600; padding: 0 4px; }

/* scrollbars */
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: $scroll; border-radius: 3px; min-height: 28px; margin: 0 1px; }
QScrollBar::handle:vertical:hover { background: $scroll_hover; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { height: 0; }
""")


def _build_stylesheet(t: dict[str, object]) -> str:
    c = {k: css(v) for k, v in t.items() if isinstance(v, QColor)}
    dark = bool(t["dark"])
    soft = 0.22 if dark else 0.14
    for name in ("ok", "err", "warn"):
        c[f"{name}_soft"] = css(rgba(QColor(t[name]).name(), soft))  # type: ignore[arg-type]
        c[f"{name}_line"] = css(rgba(QColor(t[name]).name(), 0.45))  # type: ignore[arg-type]
    icon_rules, bar_rules = [], []
    for kind, token in KIND_COLOR.items():
        base = QColor(t[token]).name()  # type: ignore[arg-type]
        icon_rules.append(f'QLabel#JobIcon[kind="{kind}"] {{ background: {css(rgba(base, soft))}; }}')
        bar_rules.append(f'QProgressBar[kind="{kind}"]::chunk {{ background: {base}; }}')
    c.update(
        sans=SANS, mono=MONO, r=str(RADIUS), r_sm=str(RADIUS_SM), r_lg=str(RADIUS_LG),
        control_h=str(CONTROL_HEIGHT),
        job_icon_rules="\n".join(icon_rules), job_bar_rules="\n".join(bar_rules),
        poster_a="#2b3a55" if dark else "#c9dcf7", poster_b="#1a1a22" if dark else "#e9e3f7",
        scrim=css(rgba("#000000", 0.34) if dark else rgba("#1c2440", 0.18)),
        btn_rim=css(rgba("#ffffff", 0.22) if dark else rgba("#ffffff", 1.0)),
        accent_top=QColor(t["accent"]).lighter(118).name(),  # type: ignore[arg-type]
        accent_bottom=QColor(t["accent"]).darker(108).name(),  # type: ignore[arg-type]
        accent_rim=css(rgba("#ffffff", 0.45)),
        accent_edge=css(QColor(QColor(t["accent"]).darker(125))),  # type: ignore[arg-type]
        drop_border=css(rgba("#ffffff", 0.22) if dark else rgba("#000000", 0.16)),
        drop_bg=css(rgba("#ffffff", 0.03) if dark else rgba("#ffffff", 0.35)),
    )
    return _STYLE_TEMPLATE.substitute(c) + _combo_arrow_rule()


# Stroke icons (24x24 viewBox). Keys match TaskQueue task kinds and PAGES.
_ICONS = {
    "download": '<path d="M12 3v12m0 0 4-4m-4 4-4-4"/><path d="M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/>',
    "convert": '<path d="M4 8h13m0 0-4-4m4 4-4 4"/><path d="M20 16H7m0 0 4 4m-4-4 4-4"/>',
    "compress": '<path d="M8 3v3a2 2 0 0 1-2 2H3"/><path d="M21 8h-3a2 2 0 0 1-2-2V3"/>'
                '<path d="M3 16h3a2 2 0 0 1 2 2v3"/><path d="M16 21v-3a2 2 0 0 1 2-2h3"/>',
    "subtitle": '<rect x="3" y="5" width="18" height="14" rx="3"/><path d="M7 14h4"/><path d="M13 14h4"/>',
    "queue": '<path d="M4 6h16M4 12h16M4 18h10"/>',
    "close": '<path d="M6 6l12 12M18 6 6 18"/>',
    "link": '<path d="M9.5 14.5l5-5"/><path d="M11 6.5 12.5 5a4 4 0 0 1 5.7 5.7L16.5 12"/>'
            '<path d="M13 17.5 11.5 19a4 4 0 0 1-5.7-5.7L7.5 12"/>',
    "paste": '<rect x="6" y="4" width="12" height="16" rx="2.5"/><path d="M9 4.5h6V7a1 1 0 0 1-1 1h-4a1 1 0 0 1-1-1z"/>',
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "trash": '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/>',
    "video": '<rect x="2" y="6" width="14" height="12" rx="2"/><path d="M16 10l6-3v10l-6-3z"/>',
    "audio": '<path d="M4 10v4M8 6v12M12 3v18M16 7v10M20 10v4"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7.5V12l3 1.8"/>',
    "chevron": '<path d="m6 9 6 6 6-6"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "play": '<path d="M8 5l12 7-12 7z" fill="currentColor" stroke="none"/>',
    "upload": '<path d="M12 15V3m0 0 4 4m-4-4-4 4"/><path d="M4 15v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4"/>',
    "film": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m10 9 5 3-5 3z" fill="currentColor" stroke="none"/>',
    "music": '<path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>',
    "sparkles": '<path d="M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>',
    "gear": '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73'
            'l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38'
            'a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18'
            'a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08'
            'a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08'
            'a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
    "chip": '<rect x="6" y="6" width="12" height="12" rx="2"/><rect x="9.5" y="9.5" width="5" height="5" rx="1"/>'
            '<path d="M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M4.6 4.6l1.4 1.4M18 18l1.4 1.4'
           'M2.5 12h2M19.5 12h2M4.6 19.4 6 18M18 6l1.4-1.4"/>',
    "moon": '<path d="M20 14.5A8.5 8.5 0 1 1 9.5 4a7 7 0 0 0 10.5 10.5z"/>',
    "auto": '<circle cx="12" cy="12" r="8.5"/><path d="M12 3.5a8.5 8.5 0 0 1 0 17z" fill="currentColor" stroke="none"/>',
}


@lru_cache(maxsize=None)
def make_icon(name: str, color: str = "#6e6e73", size: int = 20, stroke: float = 1.8) -> QIcon:
    """Render one of the stroke icons in the given #rrggbb colour. Needs a QGuiApplication."""
    return QIcon(icon_pixmap(name, color, size, stroke))


@lru_cache(maxsize=None)
def icon_pixmap(name: str, color: str, size: int, stroke: float = 1.8) -> QPixmap:
    body = _ICONS.get(name, _ICONS["queue"]).replace("currentColor", color)
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="{color}" stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
    )
    scale = 2  # crisp on high-DPI screens
    pixmap = QPixmap(size * scale, size * scale)
    pixmap.fill(Qt.GlobalColor.transparent)
    pixmap.setDevicePixelRatio(scale)
    try:
        from PySide6.QtSvg import QSvgRenderer
    except ImportError:  # QtSvg missing: the UI still works, just without icons
        return pixmap
    painter = QPainter(pixmap)
    QSvgRenderer(svg.encode("utf-8")).render(painter, QRectF(0, 0, size, size))
    painter.end()
    return pixmap


def themed_icon(name: str, role: str = "text2", size: int = 20, stroke: float = 1.8) -> QIcon:
    """Icon in the current colour of a token, e.g. themed_icon("folder", "text2")."""
    return make_icon(name, hex_color(role), size, stroke)


def round_combo_popups(root: QWidget) -> None:
    """Make every combo popup under `root` a frameless, translucent window so its list is rounded."""
    for combo in root.findChildren(QComboBox):
        combo.setItemDelegate(QStyledItemDelegate(combo))  # lets the stylesheet pad and round each item
        popup = combo.view().window()  # the combo's private popup container
        popup.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        popup.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint, True)
        popup.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)


def repolish(widget: QWidget) -> None:
    """Re-apply the stylesheet after a dynamic property (state, on, kind) changed."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def _palette(t: dict[str, object]) -> QPalette:
    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: t["popup"],
        QPalette.ColorRole.WindowText: t["text"],
        QPalette.ColorRole.Base: t["popup"],
        QPalette.ColorRole.AlternateBase: t["fill"],
        QPalette.ColorRole.Text: t["text"],
        QPalette.ColorRole.Button: t["popup"],
        QPalette.ColorRole.ButtonText: t["text"],
        QPalette.ColorRole.ToolTipBase: t["popup"],
        QPalette.ColorRole.ToolTipText: t["text"],
        QPalette.ColorRole.PlaceholderText: t["text3"],
        QPalette.ColorRole.Highlight: t["accent"],
        QPalette.ColorRole.HighlightedText: t["on_accent"],
        QPalette.ColorRole.Link: t["accent"],
    }
    for role, value in roles.items():
        palette.setColor(role, QColor(value))  # type: ignore[arg-type]
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(t["text3"]))  # type: ignore[arg-type]
    return palette


def _combo_arrow_rule() -> str:
    """QSS can only show a custom drop-down arrow from a file, so write the chevron to the temp dir."""
    try:
        folder = Path(tempfile.gettempdir()) / "cliphub-ui"
        folder.mkdir(exist_ok=True)
        tone = hex_color("text2")
        path = folder / f"chevron-{tone.lstrip('#')}.png"
        if not path.is_file() and not icon_pixmap("chevron", tone, 14, 2.0).save(str(path), "PNG"):
            return ""
    except OSError:
        return ""
    return (
        "QComboBox::drop-down { border: none; width: 34px; background: transparent; }"
        f"QComboBox::down-arrow {{ image: url({path.as_posix()}); width: 14px; height: 14px; }}"
    )


class ThemeManager(QObject):
    """Owns the appearance setting and re-themes the app when it or the OS scheme changes."""

    changed = Signal()

    def __init__(self, app: QApplication) -> None:
        super().__init__(app)
        self._app = app
        self._settings = QSettings("ClipHUB", "ClipHUB")
        saved = str(self._settings.value("appearance", "system"))
        self._appearance = saved if saved in APPEARANCES else "system"
        self._dark: bool | None = None
        app.styleHints().colorSchemeChanged.connect(self._on_system_scheme)

    @property
    def appearance(self) -> str:
        return self._appearance

    def set_appearance(self, appearance: str) -> None:
        if appearance not in APPEARANCES or appearance == self._appearance:
            return
        self._appearance = appearance
        self._settings.setValue("appearance", appearance)
        self.apply(force=True)

    def _system_is_dark(self) -> bool:
        return self._app.styleHints().colorScheme() == Qt.ColorScheme.Dark

    def _wants_dark(self) -> bool:
        if self._appearance == "system":
            return self._system_is_dark()
        return self._appearance == "dark"

    def _on_system_scheme(self, _scheme: Qt.ColorScheme) -> None:
        if self._appearance == "system":
            self.apply()

    def apply(self, force: bool = False) -> None:
        global _current
        hints = self._app.styleHints()
        # Native title bars follow the chosen scheme; "system" hands control back to the OS.
        if self._appearance == "system":
            if hasattr(hints, "unsetColorScheme"):
                hints.unsetColorScheme()
        elif hasattr(hints, "setColorScheme"):
            hints.setColorScheme(Qt.ColorScheme.Dark if self._appearance == "dark" else Qt.ColorScheme.Light)
        dark = self._wants_dark()
        if dark == self._dark and not force:
            return
        self._dark = dark
        _current = DARK if dark else LIGHT
        self._app.setPalette(_palette(_current))
        self._app.setStyleSheet(_build_stylesheet(_current))
        self.changed.emit()


_manager: ThemeManager | None = None


def manager() -> ThemeManager | None:
    return _manager


def on_change(callback: Callable[[], None]) -> None:
    """Call `callback` now and after every theme change (for icons and custom painting).

    Pass a bound method of a QObject so the connection ends when that object is destroyed.
    """
    callback()
    if _manager is not None:
        _manager.changed.connect(callback)


def _fonts_dir() -> Path:
    import sys
    root = getattr(sys, "_MEIPASS", None)
    return (Path(root) if root else Path(__file__).resolve().parent.parent) / "assets" / "fonts"


def load_fonts(app: QApplication) -> list[str]:
    """Register the bundled fonts and set the app font to the Apple-like family list."""
    import sys
    from PySide6.QtGui import QFont, QFontDatabase

    for path in sorted(_fonts_dir().glob("*.ttf")):
        QFontDatabase.addApplicationFont(str(path))
    families: list[str] = []
    if sys.platform == "darwin":
        families += [QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family(), "Sukhumvit Set"]
    families += BUNDLED_FAMILIES
    global SANS
    SANS = ", ".join(f'"{name}"' for name in families) + ", sans-serif"
    font = QFont()
    font.setFamilies(families)
    font.setPixelSize(14)
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)  # closer to Apple's rendering
    app.setFont(font)
    return families


def apply_theme(app: QApplication) -> ThemeManager:
    global _manager
    load_fonts(app)
    app.setStyle("Fusion")  # consistent base so the stylesheet renders the same on Windows and macOS
    _manager = ThemeManager(app)
    _manager.apply(force=True)
    return _manager
