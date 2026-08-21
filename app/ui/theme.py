"""Shared Light Studio theme for the desktop workspace."""

# Hallmark · pre-emit critique: P4 H4 E4 S4 R5 V4
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

# Hallmark · genre: modern-minimal · macrostructure: Workbench
# theme: custom Light Studio · warm paper / system Thai UI / teal signal accent
COLORS = {
    "window": "#F3F0E9",
    "panel": "#FFFEFB",
    "panel_alt": "#EAE7E0",
    "ink": "#202522",
    "muted": "#59615D",
    "rule": "#6E7771",
    "canvas": "#242925",
    "accent": "#0F766E",
    "accent_hover": "#0B5F59",
    "warning": "#9A5B00",
    "danger": "#B42318",
}

LIGHT_STUDIO_STYLESHEET = f"""
QMainWindow, QDialog {{
    background: {COLORS["window"]};
    color: {COLORS["ink"]};
}}
QWidget {{
    color: {COLORS["ink"]};
}}
QToolBar, QMenuBar, QMenu, QStatusBar, QDockWidget::title {{
    background: {COLORS["panel"]};
    border-color: {COLORS["rule"]};
}}
QMenuBar::item {{
    background: transparent;
    color: {COLORS["ink"]};
    padding: 4px 10px;
}}
QMenuBar::item:selected, QMenuBar::item:hover {{
    background: {COLORS["accent"]};
    color: {COLORS["panel"]};
}}
QMenuBar::item:disabled, QMenuBar::item:disabled:selected,
QMenuBar::item:disabled:hover {{
    background: transparent;
    color: {COLORS["muted"]};
}}
QMenu::item {{
    background: transparent;
    color: {COLORS["ink"]};
    padding: 6px 24px 6px 12px;
}}
QMenu::item:selected, QMenu::item:hover {{
    background: {COLORS["accent"]};
    color: {COLORS["panel"]};
}}
QMenu::item:disabled, QMenu::item:disabled:selected,
QMenu::item:disabled:hover {{
    background: transparent;
    color: {COLORS["muted"]};
}}
QToolBar {{
    spacing: 4px;
    padding: 4px;
    border-bottom: 1px solid {COLORS["rule"]};
}}
QPushButton, QToolButton, QComboBox, QLineEdit, QTextEdit, QPlainTextEdit,
QSpinBox, QDoubleSpinBox, QListWidget, QTabWidget::pane {{
    background: {COLORS["panel"]};
    border: 2px solid {COLORS["rule"]};
    border-radius: 4px;
    min-height: 36px;
}}
QPushButton, QToolButton {{
    padding: 4px 10px;
    min-width: 36px;
}}
QPushButton:hover, QToolButton:hover {{
    background: {COLORS["panel_alt"]};
}}
QPushButton:pressed, QToolButton:pressed {{
    background: {COLORS["rule"]};
    color: {COLORS["panel"]};
}}
QToolButton:checked:hover {{
    background: {COLORS["accent_hover"]};
    border-color: {COLORS["accent_hover"]};
}}
QPushButton:focus, QToolButton:focus, QComboBox:focus, QLineEdit:focus,
QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QListWidget:focus, QTabBar::tab:focus {{
    border-color: {COLORS["accent"]};
}}
QPushButton:disabled, QToolButton:disabled, QComboBox:disabled,
QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled {{
    color: {COLORS["muted"]};
    background: {COLORS["panel_alt"]};
}}
QToolButton:checked, QTabBar::tab:selected, QListWidget::item:selected {{
    background: {COLORS["accent"]};
    color: {COLORS["panel"]};
    border-color: {COLORS["accent"]};
}}
QTabBar::tab {{
    background: {COLORS["panel_alt"]};
    border: 2px solid {COLORS["rule"]};
    border-bottom: 0;
    min-height: 36px;
    padding: 4px 10px;
}}
QGraphicsView {{
    background: {COLORS["canvas"]};
    border: 1px solid {COLORS["canvas"]};
}}
QFrame#ocrBlockCard {{
    background: {COLORS["panel"]};
    border: 2px solid {COLORS["rule"]};
    border-radius: 4px;
}}
QFrame#ocrBlockCard[selected="true"] {{
    border-color: {COLORS["accent"]};
    background: {COLORS["panel_alt"]};
}}
QLabel#blockStatusLabel[statusTone="normal"] {{ color: {COLORS["muted"]}; }}
QLabel#blockStatusLabel[statusTone="success"] {{ color: {COLORS["accent"]}; }}
QLabel#blockStatusLabel[statusTone="warning"] {{ color: {COLORS["warning"]}; }}
QLabel#blockStatusLabel[statusTone="danger"] {{ color: {COLORS["danger"]}; }}
QProgressBar {{
    background: {COLORS["panel_alt"]};
    border: 0;
}}
QProgressBar::chunk {{ background: {COLORS["accent"]}; }}
"""


def apply_light_studio_theme(application: QApplication) -> None:
    """Apply the shared palette and prefer the installed Thai UI face."""
    if "Noto Sans Thai UI" in QFontDatabase.families():
        application.setFont(QFont("Noto Sans Thai UI", application.font().pointSize()))
    application.setStyleSheet(LIGHT_STUDIO_STYLESHEET)
