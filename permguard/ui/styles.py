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
}

MAIN_STYLE = f"""
QMainWindow, QWidget {{
    background-color: {C['bg']};
    color: {C['text']};
    font-family: Inter, sans-serif;
    font-size: 13px;
}}
QTabWidget::pane {{
    border: 1px solid {C['border']};
    border-radius: 0;
    background: {C['panel']};
}}
QTabBar::tab {{
    background: {C['bg']};
    color: {C['muted']};
    padding: 8px 16px;
    border-radius: 6px 6px 0 0;
    margin-right: 2px;
    font-weight: 500;
}}
QTabBar::tab:selected {{
    background: {C['panel']};
    color: {C['accent']};
    border-bottom: 2px solid {C['accent']};
}}
QTableWidget {{
    background: {C['panel']};
    gridline-color: {C['border']};
    border: none;
    color: {C['text']};
    alternate-background-color: #20213a;
    selection-background-color: {C['border']};
    selection-color: {C['text']};
}}
QTableWidget::item {{ padding: 5px 8px; }}
QHeaderView::section {{
    background: {C['bg']};
    color: {C['muted']};
    padding: 5px 8px;
    border: none;
    font-weight: 600;
    font-size: 11px;
    letter-spacing: 1px;
}}
QPushButton {{
    background: {C['border']};
    color: {C['text']};
    border: none;
    border-radius: 6px;
    padding: 7px 16px;
    font-weight: 500;
}}
QPushButton:hover {{ background: {C['accent']}; color: {C['bg']}; }}
QPushButton:disabled {{ background: {C['surface']}; color: {C['muted']}; }}
QPushButton#danger  {{ background: {C['danger']};  color: white; }}
QPushButton#danger:hover  {{ background: #d08770; }}
QPushButton#success {{ background: {C['success']}; color: {C['bg']}; }}
QPushButton#success:hover {{ background: #8fbcbb; color: {C['bg']}; }}
QPushButton#flat {{
    background: transparent; color: {C['muted']};
    padding: 4px 8px; font-size: 12px;
}}
QPushButton#flat:hover {{ color: {C['text']}; background: {C['border']}; }}
QFrame#sep {{ background: {C['border']}; max-height: 1px; }}
QGroupBox {{
    color: {C['muted']}; font-size: 11px; font-weight: 600;
    letter-spacing: 1px; border: 1px solid {C['border']};
    border-radius: 8px; margin-top: 14px; padding-top: 8px;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
QCheckBox {{ color: {C['text']}; spacing: 8px; }}
QCheckBox::indicator {{
    width: 18px; height: 18px; border-radius: 4px;
    border: 2px solid {C['border']}; background: {C['bg']};
}}
QCheckBox::indicator:checked {{ background: {C['accent']}; border-color: {C['accent']}; }}
QSpinBox {{
    background: {C['border']}; color: {C['text']};
    border: none; border-radius: 6px; padding: 4px 8px;
}}
QTextEdit {{
    background: {C['surface']}; color: {C['text']};
    border: 1px solid {C['border']}; border-radius: 6px;
    font-family: 'JetBrains Mono', monospace; font-size: 12px;
}}
QScrollBar:vertical {{
    background: {C['bg']}; width: 6px; border-radius: 3px;
}}
QScrollBar::handle:vertical {{
    background: {C['border']}; border-radius: 3px; min-height: 20px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QMenu {{
    background: {C['panel']}; color: {C['text']};
    border: 1px solid {C['border']}; border-radius: 6px; padding: 4px;
}}
QMenu::item {{ padding: 6px 20px; border-radius: 4px; }}
QMenu::item:selected {{ background: {C['border']}; }}
QToolTip {{
    background: {C['panel']}; color: {C['text']};
    border: 1px solid {C['border']}; border-radius: 4px; padding: 4px 8px;
}}
"""

DIALOG_STYLE = f"""
QDialog, QWidget {{
    background-color: {C['panel']};
    color: {C['text']};
    font-family: Inter, sans-serif;
}}
QPushButton {{
    border: none; border-radius: 8px;
    padding: 10px 20px; font-size: 13px; font-weight: 600;
}}
QPushButton#allow {{
    background: {C['success']}; color: {C['bg']};
}}
QPushButton#allow:hover {{ background: #8fbcbb; }}
QPushButton#once {{
    background: {C['warning']}; color: {C['bg']};
}}
QPushButton#once:hover {{ background: #d4a84b; }}
QPushButton#deny {{
    background: {C['danger']}; color: white;
}}
QPushButton#deny:hover {{ background: #d08770; }}
QCheckBox {{ color: {C['muted']}; font-size: 12px; spacing: 8px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border-radius: 3px;
    border: 2px solid {C['border']}; background: {C['bg']};
}}
QCheckBox::indicator:checked {{ background: {C['accent']}; border-color: {C['accent']}; }}
"""
