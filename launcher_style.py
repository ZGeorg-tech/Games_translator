"""Fixed Qt palette for the launcher process, not the in-game overlay."""
from PyQt6.QtGui import QColor, QFont, QPalette
from resource_registry import resource_path


BACKGROUND = "#191d21"
PANEL = "#22282e"
FIELD = "#171b20"
TEXT = "#edf1f5"
MUTED = "#aebac5"
BORDER = "#404a54"
FOCUS = "#83d7cf"
STATUS = {"success": "#8cdbaf", "warning": "#ffc586", "error": "#ff9b99", "info": "#9bd1f8"}

WARNING_DOCUMENT_CSS = f"""
body {{ font-family: 'Segoe UI', sans-serif; font-size: 10pt; color: {TEXT}; }}
p {{ margin: 5px 0 8px 0; }}
.section-title {{ margin: 13px 0 6px 0; font-size: 11pt; font-weight: 700; color: {FOCUS}; }}
.section-title.critical {{ color: {STATUS["warning"]}; }}
.solution {{ margin: 6px 0 10px 0; color: {MUTED}; }}
ul {{ margin-top: 4px; margin-bottom: 9px; }}
"""


def status_style(tone):
    return f"color: {STATUS[tone]}; font-weight: 600;"


def apply_launcher_style(app):
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    palette = QPalette()
    for role, value in {
        "Window": BACKGROUND, "WindowText": TEXT, "Base": FIELD, "AlternateBase": PANEL,
        "Text": TEXT, "Button": PANEL, "ButtonText": TEXT, "PlaceholderText": MUTED,
        "Highlight": "#286963", "HighlightedText": "#ffffff", "ToolTipBase": PANEL, "ToolTipText": TEXT,
        "Link": FOCUS,
    }.items():
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(value))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor("#89949f"))
    app.setPalette(palette)
    app.setProperty("launcherDarkUi", True)
    icons = (resource_path("app.assets") / "icons").as_posix()
    app.setStyleSheet(f"""
        LauncherWindow, QDialog, OverlayLoadingSplash {{ background: {BACKGROUND}; }}
        QScrollArea#launcherScroll {{ border: none; }}
        QLabel[uiRole="muted"] {{ color: {MUTED}; }}
        QLabel[uiRole="heading"] {{ font-size: 11pt; font-weight: 600; }}
        QLabel[uiRole="warning"] {{ color: {STATUS["warning"]}; font-weight: 600; }}
        QLabel[uiRole="warningBanner"] {{
            color: {STATUS["warning"]}; background: #362b23; border: 1px solid #66513d;
            border-radius: 6px; padding: 10px; font-weight: 600;
        }}
        QLabel[uiRole="keyCapture"] {{
            color: {FOCUS}; background: {FIELD}; border: 1px solid {BORDER};
            border-radius: 6px; padding: 9px; font-size: 11pt; font-weight: 600;
        }}
        QGroupBox {{
            background: {PANEL}; border: 1px solid {BORDER}; border-radius: 8px;
            margin-top: 10px; padding-top: 10px; font-weight: 600;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin; subcontrol-position: top left;
            left: 11px; padding: 0 5px; background: {BACKGROUND}; color: {TEXT};
        }}
        QGroupBox QLabel, QGroupBox QCheckBox {{ font-weight: 400; }}
        QPushButton {{
            background: #2a323a; color: {TEXT}; border: 1px solid {BORDER};
            border-radius: 6px; padding: 4px 10px; min-height: 18px; font-weight: 500;
        }}
        QPushButton:hover {{ background: #353f48; border-color: #677580; }}
        QPushButton:pressed {{ background: #20272d; }}
        QPushButton:disabled {{ background: #22282d; color: #8d99a4; border-color: #353e47; }}
        QPushButton[uiRole="primary"], QPushButton[uiRole="launch"] {{
            background: #286963; border-color: #458d85; color: #ffffff; font-weight: 600;
        }}
        QPushButton[uiRole="launch"] {{ padding: 9px 12px; font-size: 11pt; }}
        QPushButton[uiRole="primary"]:hover, QPushButton[uiRole="launch"]:hover {{ background: #327b74; }}
        QPushButton[uiRole="primary"]:pressed, QPushButton[uiRole="launch"]:pressed {{ background: #21534e; }}
        QPushButton[uiRole="primary"]:disabled, QPushButton[uiRole="launch"]:disabled {{
            background: #263332; color: #a1b1af; border-color: #3d5050;
        }}
        QPushButton[uiRole="languageMode"]:disabled {{
            color: #c1eee8; background: #24423f; border-color: #56877f; font-weight: 600;
        }}
        QPushButton[uiRole="warningAction"] {{ color: {STATUS["warning"]}; }}
        QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit, QTextEdit, QPlainTextEdit {{
            background: {FIELD}; color: {TEXT}; border: 1px solid {BORDER};
            border-radius: 6px; padding: 4px 7px; font-weight: 400;
            selection-background-color: #286963; selection-color: #ffffff;
        }}
        QComboBox {{ padding-right: 23px; }}
        QSpinBox, QDoubleSpinBox {{ padding-right: 19px; }}
        QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QLineEdit:hover {{ border-color: #677580; }}
        QPushButton:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
        QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{ border-color: {FOCUS}; }}
        QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QLineEdit:disabled {{
            background: #22282e; color: #8d99a4; border-color: #353e47;
        }}
        QComboBox::drop-down {{
            subcontrol-origin: padding; subcontrol-position: top right; width: 22px; border: none;
        }}
        QComboBox::down-arrow {{ image: url("{icons}/ui-chevron-down.svg"); width: 12px; height: 12px; }}
        QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{
            subcontrol-origin: padding; width: 18px; border: none; border-left: 1px solid {BORDER};
        }}
        QAbstractSpinBox::up-button {{ subcontrol-position: top right; }}
        QAbstractSpinBox::down-button {{ subcontrol-position: bottom right; }}
        QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {{ background: #353f48; }}
        QAbstractSpinBox::up-arrow {{ image: url("{icons}/ui-chevron-up.svg"); width: 11px; height: 9px; }}
        QAbstractSpinBox::down-arrow {{ image: url("{icons}/ui-chevron-down.svg"); width: 11px; height: 9px; }}
        QComboBox QAbstractItemView {{
            background: {PANEL}; color: {TEXT}; border: 1px solid #566370;
            selection-background-color: #286963; selection-color: #ffffff; outline: 0;
        }}
        QComboBox QAbstractItemView::item {{ min-height: 23px; padding: 2px 7px; }}
        QCheckBox {{ spacing: 7px; padding: 2px 0; }}
        QCheckBox:hover {{ color: {FOCUS}; }}
        QCheckBox:focus {{ color: {FOCUS}; }}
        QCheckBox:disabled {{ color: #89949f; }}
        QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid #71808c; border-radius: 3px; background: {FIELD}; }}
        QCheckBox::indicator:checked {{ background: #286963; border-color: #62a69e; image: url("{icons}/ui-check.svg"); }}
        QCheckBox::indicator:hover, QCheckBox::indicator:focus {{ border-color: {FOCUS}; }}
        QCheckBox::indicator:disabled {{ border-color: {BORDER}; }}
        QScrollBar:vertical {{ background: {FIELD}; width: 11px; margin: 0; }}
        QScrollBar::handle:vertical {{ background: #596774; border: 2px solid {FIELD}; border-radius: 5px; min-height: 25px; }}
        QScrollBar::handle:vertical:hover {{ background: #7c8c99; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
        QProgressBar {{ background: {FIELD}; border: 1px solid {BORDER}; border-radius: 4px; }}
        QProgressBar::chunk {{ background: #5dbdb2; border-radius: 3px; }}
        QToolTip {{ background: {PANEL}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px; }}
    """)
