"""
widgets.py — Reusable UI components shared across tabs.
"""
import os
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui  import QFont

from .styles import C
from ..core.system import kill_pid
from ..core.permissions import PermissionDB


def hsep() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setObjectName("sep")
    return f


def help_icon(tooltip: str) -> QLabel:
    """Small circular '?' label that shows a tooltip on hover.
    Use to explain what a control does without cluttering the UI."""
    lbl = QLabel("?")
    lbl.setToolTip(tooltip)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setFixedSize(18, 18)
    lbl.setCursor(Qt.CursorShape.WhatsThisCursor)
    lbl.setStyleSheet(
        f"QLabel {{ background:{C['surface']}; color:{C['muted']};"
        f"border:1px solid {C['border']}; border-radius:9px;"
        f"font-size:11px; font-weight:700; }}"
        f"QLabel:hover {{ color:{C['accent']}; border-color:{C['accent']}; }}"
    )
    return lbl


def badge(text: str, color: str) -> QLabel:
    lbl = QLabel(text)
    fg = C["bg"] if color not in (C["danger"], C["purple"]) else "white"
    lbl.setStyleSheet(
        f"background:{color}; color:{fg}; border-radius:10px;"
        f"padding:3px 10px; font-weight:700; font-size:11px; letter-spacing:0.5px;"
    )
    return lbl


def build_table(headers: list, rows: list,
                kill_col: int | None = None,
                extra_btn_col: int | None = None,
                extra_btn_label: str = "Action",
                extra_btn_fn=None,
                refresh_fn=None) -> QTableWidget:
    """Build a styled read-only table. kill_col adds a Kill button column."""
    has_action = kill_col is not None or extra_btn_fn is not None
    ncols = len(headers) + (1 if has_action else 0)
    tbl = QTableWidget(len(rows), ncols)
    tbl.setHorizontalHeaderLabels(headers + (["Action"] if has_action else []))
    tbl.verticalHeader().setVisible(False)
    tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    tbl.setAlternatingRowColors(True)
    tbl.setShowGrid(False)
    tbl.verticalHeader().setDefaultSectionSize(38)
    hdr = tbl.horizontalHeader()
    hdr.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    hdr.setHighlightSections(False)
    if has_action:
        hdr.setSectionResizeMode(len(headers), QHeaderView.ResizeMode.Fixed)
        tbl.setColumnWidth(len(headers), 110)

    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            item = QTableWidgetItem(str(val))
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            tbl.setItem(r, c, item)
        if has_action and refresh_fn:
            if kill_col is not None:
                pid  = str(row[kill_col])
                name = str(row[1]) if len(row) > 1 else "?"
                btn  = QPushButton("Kill")
                btn.setObjectName("danger")
                btn.setMinimumWidth(90)
                btn.setMinimumHeight(30)
                btn.clicked.connect(lambda _, p=pid, n=name: _kill_dialog(p, n, refresh_fn))
                tbl.setCellWidget(r, len(headers), btn)
    return tbl


def _kill_dialog(pid: str, name: str, refresh_fn):
    if pid in ("—", "?", ""):
        return
    reply = QMessageBox.question(
        None, "Kill Process",
        f"Terminate <b>{name}</b> (PID {pid})?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    )
    if reply == QMessageBox.StandardButton.Yes:
        ok, err = kill_pid(pid)
        if ok:
            QMessageBox.information(None, "Done", f"{name} terminated.")
        else:
            QMessageBox.warning(None, "Error", f"Could not kill process:\n{err}")
        refresh_fn()


# ── Generic Permission Tab ────────────────────────────────────────────────────

class PermTab(QWidget):
    """A tab that displays a list of active accesses with optional Kill buttons."""

    def __init__(self, icon: str, title: str, desc: str,
                 data_fn, headers: list, kill_col: int | None = None):
        super().__init__()
        self.data_fn  = data_fn
        self.headers  = headers
        self.kill_col = kill_col
        self._badge_lbl = QLabel()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)

        # Header row
        hdr = QHBoxLayout()
        hdr.setSpacing(14)
        ico = QLabel(icon)
        ico.setFont(QFont("Noto Color Emoji", 20))
        ico.setFixedWidth(32)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        ttl = QLabel(title)
        ttl.setFont(QFont("Inter", 15, QFont.Weight.Bold))
        ttl.setStyleSheet(f"color: {C['text']};")
        sub = QLabel(desc)
        sub.setStyleSheet(f"color: {C['muted']}; font-size: 12px;")
        text_col.addWidget(ttl)
        text_col.addWidget(sub)

        hdr.addWidget(ico)
        hdr.addLayout(text_col)
        hdr.addStretch()
        hdr.addWidget(self._badge_lbl)
        layout.addLayout(hdr)
        layout.addWidget(hsep())

        self._body = QVBoxLayout()
        self._body.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self._body)

        foot = QHBoxLayout()
        foot.addStretch()
        r_btn = QPushButton("↻  Refresh")
        r_btn.setObjectName("flat")
        r_btn.clicked.connect(self.refresh)
        foot.addWidget(r_btn)
        layout.addLayout(foot)

        self.refresh()

    def refresh(self):
        rows = self.data_fn()
        while self._body.count():
            c = self._body.takeAt(0)
            if c.widget():
                c.widget().deleteLater()

        if not rows:
            placeholder = _EmptyState("Nothing active right now")
            self._body.addWidget(placeholder)
            self._badge_lbl.setText("Safe")
            self._badge_lbl.setStyleSheet(
                f"background:{C['success']};color:{C['bg']};border-radius:10px;"
                f"padding:3px 10px;font-weight:700;font-size:11px;")
        else:
            tbl = build_table(self.headers, rows,
                              kill_col=self.kill_col, refresh_fn=self.refresh)
            self._body.addWidget(tbl)
            self._badge_lbl.setText(f"  {len(rows)} Active  ")
            self._badge_lbl.setStyleSheet(
                f"background:{C['danger']};color:white;border-radius:10px;"
                f"padding:3px 10px;font-weight:700;font-size:11px;")
        return rows


# ── Empty state placeholder ───────────────────────────────────────────────────

class _EmptyState(QWidget):
    def __init__(self, message: str, icon: str = "✓"):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(8)

        ico = QLabel(icon)
        ico.setFont(QFont("Noto Color Emoji", 28))
        ico.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ico.setStyleSheet(f"color: {C['success']}; background: transparent;")

        lbl = QLabel(message)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(
            f"color: {C['muted']}; font-size: 13px; background: transparent;")

        layout.addStretch()
        layout.addWidget(ico)
        layout.addWidget(lbl)
        layout.addStretch()
        self.setMinimumHeight(140)


# ── Stat Card (for dashboard) ─────────────────────────────────────────────────

class StatCard(QFrame):
    clicked = pyqtSignal()

    _BASE_STYLE = (
        f"QFrame#card {{ background:{C['panel']}; border-radius:12px;"
        f"border:1px solid {C['border']}; }}"
    )
    _HOVER_STYLE = (
        f"QFrame#card {{ background:{C['hover']}; border-radius:12px;"
        f"border:1px solid {C['accent']}40; }}"
    )

    def __init__(self, icon: str, title: str, accent: str):
        super().__init__()
        self.setObjectName("card")
        self.setStyleSheet(self._BASE_STYLE)
        self.setFixedHeight(100)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._accent = accent

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(6)

        top = QHBoxLayout()
        ico_lbl = QLabel(icon)
        ico_lbl.setFont(QFont("Noto Color Emoji", 18))
        self._count = QLabel("—")
        self._count.setFont(QFont("Inter", 24, QFont.Weight.Bold))
        self._count.setStyleSheet(f"color: {C['muted']};")
        top.addWidget(ico_lbl)
        top.addStretch()
        top.addWidget(self._count)
        layout.addLayout(top)

        self._title = QLabel(title)
        self._title.setStyleSheet(f"color: {C['muted']}; font-size: 12px; font-weight: 500;")
        layout.addWidget(self._title)

    def update(self, count: int):
        if count:
            self._count.setText(str(count))
            self._count.setStyleSheet(f"color: {C['danger']}; font-size: 24px; font-weight: 700;")
        else:
            self._count.setText("✓")
            self._count.setStyleSheet(f"color: {C['success']}; font-size: 24px; font-weight: 700;")

    def enterEvent(self, event):
        self.setStyleSheet(self._HOVER_STYLE)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setStyleSheet(self._BASE_STYLE)
        super().leaveEvent(event)

    def mousePressEvent(self, _):
        self.clicked.emit()
