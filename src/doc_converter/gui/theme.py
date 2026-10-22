"""doc_converter.gui.theme —— 桌面端配色与样式表。"""

from __future__ import annotations

import base64
import tempfile
from pathlib import Path

_CHECK_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAASklEQVR4nO2PwQ4AMARD1f//"
    "c5fsJGaE47LeiFetyFdbJGlnncDWRCefAaBlwAscGtB1zODDgK5jBe+9FAkyOKwAd5zB"
    "qRgkeVQLS4Uv+v5cpjkAAAAASUVORK5CYII="
)
_check_path: str | None = None


def _checked_icon_path() -> str:
    global _check_path
    if _check_path is None:
        path = Path(tempfile.gettempdir()) / "docconverter_check.png"
        try:
            if not path.exists():
                path.write_bytes(base64.b64decode(_CHECK_PNG_B64))
            _check_path = path.as_posix()
        except OSError:  # pragma: no cover
            _check_path = ""
    return _check_path or ""


LIGHT = {
    "bg": "#f3f5f9",
    "card": "#ffffff",
    "text": "#1f2937",
    "muted": "#6b7280",
    "border": "#dbe1ea",
    "accent": "#2563eb",
    "accent_hover": "#1d4ed8",
    "accent_text": "#ffffff",
    "ok": "#15803d",
    "warn": "#b45309",
    "error": "#dc2626",
    "input": "#ffffff",
    "hover": "#eef2ff",
    "header": "#ffffff",
}

DARK = {
    "bg": "#111827",
    "card": "#1f2937",
    "text": "#e5e7eb",
    "muted": "#9ca3af",
    "border": "#374151",
    "accent": "#3b82f6",
    "accent_hover": "#60a5fa",
    "accent_text": "#0b1120",
    "ok": "#4ade80",
    "warn": "#fbbf24",
    "error": "#f87171",
    "input": "#111827",
    "hover": "#1e293b",
    "header": "#111827",
}


def palette(dark: bool) -> dict:
    return DARK if dark else LIGHT


def stylesheet(dark: bool) -> str:
    c = palette(dark)
    return f"""
    QWidget {{
        background: {c['bg']};
        color: {c['text']};
        font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif;
        font-size: 13px;
    }}
    QMainWindow, QDialog {{ background: {c['bg']}; }}
    QLabel, QCheckBox, QRadioButton {{
        background: transparent;
        color: {c['text']};
    }}
    QScrollArea {{ background: transparent; border: none; }}
    QWidget#scrollBody {{ background: transparent; }}
    QFrame#card, QFrame#dropArea, QFrame#panel {{
        background: {c['card']};
        border: 1px solid {c['border']};
        border-radius: 12px;
    }}
    QFrame#dropArea {{
        border: 2px dashed {c['border']};
    }}
    QFrame#dropArea[dragActive="true"] {{
        border: 2px dashed {c['accent']};
        background: {c['hover']};
    }}
    QLabel#title {{ font-size: 20px; font-weight: 700; color: {c['text']}; }}
    QLabel#subtitle {{ color: {c['muted']}; font-size: 12px; }}
    QLabel#section {{ font-weight: 600; font-size: 13px; color: {c['text']}; }}
    QLabel#hint {{ color: {c['muted']}; }}
    QLabel#bigHint {{ color: {c['muted']}; font-size: 15px; }}
    QLabel#status {{ color: {c['muted']}; }}
    QPushButton {{
        background: {c['card']};
        color: {c['text']};
        border: 1px solid {c['border']};
        border-radius: 8px;
        padding: 6px 12px;
        min-height: 18px;
    }}
    QPushButton:hover {{ background: {c['hover']}; border-color: {c['accent']}; }}
    QPushButton:disabled {{ color: {c['muted']}; background: {c['bg']}; }}
    QPushButton#primary {{
        background: {c['accent']};
        color: {c['accent_text']};
        border: none;
        border-radius: 8px;
        font-weight: 600;
        padding: 9px 20px;
    }}
    QPushButton#primary:hover {{ background: {c['accent_hover']}; }}
    QPushButton#primary:disabled {{ background: {c['border']}; color: {c['muted']}; }}
    QPushButton#ghost {{
        background: transparent;
        border: none;
        color: {c['accent']};
        padding: 4px 8px;
    }}
    QPushButton#ghost:hover {{ background: {c['hover']}; }}
    QLineEdit, QComboBox, QSpinBox {{
        background: {c['input']};
        border: 1px solid {c['border']};
        border-radius: 8px;
        padding: 4px 10px;
        min-height: 18px;
        selection-background-color: {c['accent']};
        selection-color: {c['accent_text']};
    }}
    QLineEdit:focus, QComboBox:focus {{ border-color: {c['accent']}; }}
    QLineEdit:disabled, QComboBox:disabled {{
        color: {c['muted']};
        background: {c['bg']};
        border-color: {c['border']};
    }}
    QCheckBox:disabled {{ color: {c['muted']}; }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox QAbstractItemView {{
        background: {c['card']};
        border: 1px solid {c['border']};
        selection-background-color: {c['accent']};
        selection-color: {c['accent_text']};
        outline: 0;
    }}
    QTableWidget {{
        background: {c['card']};
        border: 1px solid {c['border']};
        border-radius: 10px;
        gridline-color: {c['border']};
        selection-background-color: {c['hover']};
        selection-color: {c['text']};
    }}
    QHeaderView::section {{
        background: {c['card']};
        color: {c['muted']};
        border: none;
        border-bottom: 1px solid {c['border']};
        padding: 8px 6px;
        font-weight: 600;
    }}
    QTableWidget::item {{ padding: 4px 6px; }}
    QProgressBar {{
        background: {c['card']};
        border: 1px solid {c['border']};
        border-radius: 7px;
        height: 14px;
        text-align: center;
        font-size: 11px;
    }}
    QProgressBar::chunk {{ background: {c['accent']}; border-radius: 6px; }}
    QPlainTextEdit {{
        background: {c['card']};
        border: 1px solid {c['border']};
        border-radius: 10px;
        font-family: "Cascadia Mono", Consolas, "Microsoft YaHei", monospace;
        font-size: 12px;
        padding: 8px;
    }}
    QCheckBox {{ spacing: 8px; }}
    QCheckBox::indicator {{
        width: 16px; height: 16px;
        border: 1px solid {c['border']};
        border-radius: 4px;
        background: {c['input']};
    }}
    QCheckBox::indicator:checked {{
        background: {c['accent']};
        border-color: {c['accent']};
        image: url("{_checked_icon_path()}");
    }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{
        background: {c['border']}; border-radius: 5px; min-height: 30px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: transparent;
    }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle:horizontal {{
        background: {c['border']}; border-radius: 5px; min-width: 30px;
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
        background: transparent;
    }}
    QToolTip {{
        background: {c['card']};
        color: {c['text']};
        border: 1px solid {c['border']};
        padding: 4px 8px;
    }}
    """
