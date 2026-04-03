"""
styles.py — Colors and Qt stylesheets for PermGuard.
"""

C = {
    "bg":      "#1e2030",
    "panel":   "#252738",
    "border":  "#313244",
    "accent":  "#88c0d0",
    "danger":  "#bf616a",
    "success": "#a3be8c",
    "warning": "#ebcb8b",
    "purple":  "#b48ead",
    "text":    "#cdd6f4",
    "muted":   "#6c7086",
    "surface": "#2a2d3e",
    "hover":   "#2e3149",
}

MAIN_STYLE = f"""
QMainWindow, QWidget {{
    background-color: {C['bg']};
    color: {C['text']};
    font-family: Inter, sans-serif;
    font-size: 13px;
}}

/* ── Tab bar ── */
QTabWidget::pane {{
    border: none;
    border-top: 1px solid {C['border']};
    background: {C['panel']};
}}
QTabBar {{
    background: {C['bg']};
}}
QTabBar::tab {{
    background: transparent;
    color: {C['muted']};
    padding: 10px 18px;
    margin-right: 1px;
    border: none;
    border-bottom: 2px solid transparent;
    font-weight: 500;
    font-size: 13px;
}}
QTabBar::tab:hover {{
    color: {C['text']};
    background: {C['hover']};
}}
QTabBar::tab:selected {{
    color: {C['accent']};
    background: {C['panel']};
    border-bottom: 2px solid {C['accent']};
    font-weight: 600;
}}

/* ── Tables ── */
QTableWidget {{
    background: {C['panel']};
    gridline-color: {C['border']};
    border: none;
    color: {C['text']};
    alternate-background-color: {C['surface']};
    selection-background-color: {C['hover']};
    selection-color: {C['text']};
    outline: none;
}}
QTableWidget::item {{
    padding: 8px 10px;
    border: none;
}}
QTableWidget::item:selected {{
    background: {C['hover']};
    color: {C['text']};
}}
QHeaderView::section {{
    background: {C['bg']};
    color: {C['muted']};
    padding: 6px 10px;
    border: none;
    border-bottom: 1px solid {C['border']};
    font-weight: 600;
    font-size: 11px;
    letter-spacing: 0.8px;
    text-transform: uppercase;
}}
QHeaderView::section:first {{
    border-top-left-radius: 0;
}}

/* ── Buttons ── */
QPushButton {{
    background: {C['surface']};
    color: {C['text']};
    border: 1px solid {C['border']};
    border-radius: 6px;
    padding: 7px 16px;
    font-weight: 500;
    font-size: 13px;
}}
QPushButton:hover {{
    background: {C['border']};
    border-color: {C['accent']};
    color: {C['accent']};
}}
QPushButton:pressed {{
    background: {C['accent']};
    color: {C['bg']};
    border-color: {C['accent']};
}}
QPushButton:disabled {{
    background: {C['surface']};
    color: {C['muted']};
    border-color: {C['border']};
}}
QPushButton#danger {{
    background: {C['danger']};
    color: white;
    border-color: {C['danger']};
}}
QPushButton#danger:hover {{
    background: #cc6a72;
    border-color: #cc6a72;
    color: white;
}}
QPushButton#success {{
    background: {C['success']};
    color: {C['bg']};
    border-color: {C['success']};
}}
QPushButton#success:hover {{
    background: #8fbcbb;
    border-color: #8fbcbb;
    color: {C['bg']};
}}
QPushButton#flat {{
    background: transparent;
    color: {C['muted']};
    border: none;
    padding: 5px 10px;
    font-size: 12px;
}}
QPushButton#flat:hover {{
    color: {C['text']};
    background: {C['hover']};
    border-radius: 6px;
}}

/* ── Separators ── */
QFrame#sep {{
    background: {C['border']};
    max-height: 1px;
    border: none;
}}

/* ── Group box ── */
QGroupBox {{
    color: {C['muted']};
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.8px;
    text-transform: uppercase;
    border: 1px solid {C['border']};
    border-radius: 8px;
    margin-top: 16px;
    padding-top: 10px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    background: {C['bg']};
}}

/* ── Checkbox ── */
QCheckBox {{
    color: {C['text']};
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 18px;
    height: 18px;
    border-radius: 4px;
    border: 2px solid {C['border']};
    background: {C['bg']};
}}
QCheckBox::indicator:hover {{
    border-color: {C['accent']};
}}
QCheckBox::indicator:checked {{
    background: {C['accent']};
    border-color: {C['accent']};
    image: url(none);
}}

/* ── SpinBox ── */
QSpinBox {{
    background: {C['surface']};
    color: {C['text']};
    border: 1px solid {C['border']};
    border-radius: 6px;
    padding: 4px 8px;
}}
QSpinBox:hover {{
    border-color: {C['accent']};
}}

/* ── Text edit ── */
QTextEdit {{
    background: {C['surface']};
    color: {C['text']};
    border: 1px solid {C['border']};
    border-radius: 6px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px;
    padding: 4px;
}}
QTextEdit:focus {{
    border-color: {C['accent']};
}}

/* ── Scrollbars ── */
QScrollBar:vertical {{
    background: transparent;
    width: 6px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {C['border']};
    border-radius: 3px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: {C['muted']};
}}
QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {{ height: 0; border: none; }}
QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {{ background: transparent; }}
QScrollBar:horizontal {{
    background: transparent;
    height: 6px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {C['border']};
    border-radius: 3px;
    min-width: 24px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {C['muted']};
}}
QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal {{ width: 0; border: none; }}

/* ── Progress bar ── */
QProgressBar {{
    background: {C['border']};
    border: none;
    border-radius: 3px;
    height: 4px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background: {C['accent']};
    border-radius: 3px;
}}
QProgressBar[warning="true"]::chunk {{
    background: {C['warning']};
}}
QProgressBar[danger="true"]::chunk {{
    background: {C['danger']};
}}

/* ── Menus ── */
QMenu {{
    background: {C['panel']};
    color: {C['text']};
    border: 1px solid {C['border']};
    border-radius: 8px;
    padding: 4px;
}}
QMenu::item {{
    padding: 7px 22px;
    border-radius: 4px;
}}
QMenu::item:selected {{
    background: {C['hover']};
    color: {C['text']};
}}
QMenu::separator {{
    height: 1px;
    background: {C['border']};
    margin: 4px 8px;
}}

/* ── Tooltips ── */
QToolTip {{
    background: {C['panel']};
    color: {C['text']};
    border: 1px solid {C['border']};
    border-radius: 4px;
    padding: 5px 9px;
    font-size: 12px;
}}
"""

DIALOG_STYLE = f"""
QDialog, QWidget {{
    background-color: {C['panel']};
    color: {C['text']};
    font-family: Inter, sans-serif;
}}
QPushButton {{
    border: none;
    border-radius: 8px;
    padding: 11px 20px;
    font-size: 13px;
    font-weight: 600;
    min-height: 36px;
}}
QPushButton#allow {{
    background: {C['success']};
    color: {C['bg']};
}}
QPushButton#allow:hover {{
    background: #8fbcbb;
}}
QPushButton#once {{
    background: {C['surface']};
    color: {C['text']};
    border: 1px solid {C['border']};
}}
QPushButton#once:hover {{
    background: {C['border']};
    border-color: {C['warning']};
    color: {C['warning']};
}}
QPushButton#deny {{
    background: transparent;
    color: {C['muted']};
    border: 1px solid {C['border']};
}}
QPushButton#deny:hover {{
    background: {C['danger']};
    border-color: {C['danger']};
    color: white;
}}
QCheckBox {{
    color: {C['muted']};
    font-size: 12px;
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 3px;
    border: 2px solid {C['border']};
    background: {C['bg']};
}}
QCheckBox::indicator:checked {{
    background: {C['accent']};
    border-color: {C['accent']};
}}
QProgressBar {{
    background: {C['border']};
    border: none;
    border-radius: 2px;
    height: 3px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background: {C['accent']};
    border-radius: 2px;
}}
QProgressBar[warning="true"]::chunk {{
    background: {C['warning']};
}}
QProgressBar[danger="true"]::chunk {{
    background: {C['danger']};
}}
"""
