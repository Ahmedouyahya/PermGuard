"""
permission_dialog.py — Android-style permission request popup.

Shows when an app tries to access camera or microphone.
User choices: Allow (remember), Allow this time, Deny.
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QCheckBox, QFrame, QProgressBar
)
from PyQt6.QtCore  import Qt, QTimer, pyqtSignal
from PyQt6.QtGui   import QFont

from .styles import C, DIALOG_STYLE

# Resource metadata
RESOURCE_META = {
    "camera": {
        "icon":  "📷",
        "label": "Camera",
        "desc":  "access your camera and see video",
        "color": C["danger"],
    },
    "microphone": {
        "icon":  "🎤",
        "label": "Microphone",
        "desc":  "record audio from your microphone",
        "color": C["warning"],
    },
    "screen": {
        "icon":  "🖥",
        "label": "Screen",
        "desc":  "capture your screen",
        "color": C["purple"],
    },
}

# Decision constants
DECISION_ALLOW = "allow"
DECISION_ONCE  = "once"
DECISION_DENY  = "deny"


class PermissionDialog(QDialog):
    """
    Floating dialog shown when a new app requests a resource.

    Signals:
        decided(decision: str, remember: bool)
            decision — "allow" | "once" | "deny"
            remember — whether to save the decision permanently
    """
    decided = pyqtSignal(str, bool)

    AUTO_DENY_SECS = 30

    def __init__(self, app_name: str, pid: str, resource: str,
                 cmdline: str = "", parent=None):
        super().__init__(parent)
        self.app_name = app_name
        self.pid      = pid
        self.resource = resource
        self.cmdline  = cmdline
        self._countdown = self.AUTO_DENY_SECS

        self.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(False)
        self.setStyleSheet(DIALOG_STYLE)
        self._build()
        self._start_timer()

        # Position: top-center of screen (like Android)
        screen = self.screen().availableGeometry() if self.screen() else None
        if screen:
            self.adjustSize()
            self.move(
                screen.center().x() - self.width() // 2,
                screen.y() + 72,
            )

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build(self):
        meta = RESOURCE_META.get(self.resource, RESOURCE_META["camera"])

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background: {C['panel']};
                border-radius: 18px;
                border: 1px solid {C['border']};
            }}
        """)
        card.setFixedWidth(380)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(26, 26, 26, 22)
        layout.setSpacing(14)

        # ── Resource badge + countdown bar ───────────────────────────────────
        header_row = QHBoxLayout()
        header_row.setSpacing(14)

        res_icon = QLabel(meta["icon"])
        res_icon.setFont(QFont("Noto Color Emoji", 26))
        res_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        res_icon.setStyleSheet(
            f"background: {meta['color']}1a; border-radius: 14px;"
            f"padding: 10px; min-width: 56px; max-width: 56px;"
            f"min-height: 56px; max-height: 56px; border: 1px solid {meta['color']}33;"
        )
        header_row.addWidget(res_icon)

        header_text = QVBoxLayout()
        header_text.setSpacing(3)
        perm_label = QLabel(f"{meta['label']} Access Request")
        perm_label.setFont(QFont("Inter", 11, QFont.Weight.Bold))
        perm_label.setStyleSheet(
            f"color: {meta['color']}; letter-spacing: 0.3px; background: transparent;")
        self._countdown_lbl = QLabel(f"Auto-deny in {self._countdown}s")
        self._countdown_lbl.setStyleSheet(
            f"color: {C['muted']}; font-size: 11px; background: transparent;")
        header_text.addWidget(perm_label)
        header_text.addWidget(self._countdown_lbl)
        header_row.addLayout(header_text)
        header_row.addStretch()
        layout.addLayout(header_row)

        # Countdown progress bar
        self._progress = QProgressBar()
        self._progress.setRange(0, self.AUTO_DENY_SECS)
        self._progress.setValue(self.AUTO_DENY_SECS)
        self._progress.setFixedHeight(3)
        self._progress.setStyleSheet(
            f"QProgressBar {{ background: {C['border']}; border: none; "
            f"border-radius: 2px; }} "
            f"QProgressBar::chunk {{ background: {meta['color']}; border-radius: 2px; }}"
        )
        layout.addWidget(self._progress)

        # ── App name ─────────────────────────────────────────────────────────
        app_lbl = QLabel(self.app_name)
        app_lbl.setFont(QFont("Inter", 19, QFont.Weight.Bold))
        app_lbl.setStyleSheet(f"color: {C['text']}; background: transparent;")
        layout.addWidget(app_lbl)

        desc = QLabel(f"wants to {meta['desc']}")
        desc.setStyleSheet(f"color: {C['muted']}; font-size: 13px; background: transparent;")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # Command line (monospaced pill)
        if self.cmdline:
            cmd_lbl = QLabel(self.cmdline[:58] + ("…" if len(self.cmdline) > 58 else ""))
            cmd_lbl.setStyleSheet(
                f"color: {C['muted']}; font-size: 11px;"
                f"font-family: 'JetBrains Mono', monospace;"
                f"background: {C['bg']}; border-radius: 5px; padding: 4px 9px;"
            )
            layout.addWidget(cmd_lbl)

        pid_lbl = QLabel(f"PID {self.pid}")
        pid_lbl.setStyleSheet(
            f"color: {C['muted']}; font-size: 11px; background: transparent;")
        layout.addWidget(pid_lbl)

        # ── Separator ────────────────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"background: {C['border']}; max-height: 1px; border: none;")
        layout.addWidget(sep)

        # ── Remember checkbox ────────────────────────────────────────────────
        self._remember = QCheckBox("Remember my choice for this app")
        self._remember.setChecked(True)
        layout.addWidget(self._remember)

        # ── Buttons (stacked vertically for clean look) ──────────────────────
        layout.addSpacing(2)

        allow_btn = QPushButton("Allow")
        allow_btn.setObjectName("allow")
        allow_btn.clicked.connect(lambda: self._decide(DECISION_ALLOW))

        once_btn = QPushButton("Allow this time only")
        once_btn.setObjectName("once")
        once_btn.clicked.connect(lambda: self._decide(DECISION_ONCE))

        deny_btn = QPushButton("Deny")
        deny_btn.setObjectName("deny")
        deny_btn.clicked.connect(lambda: self._decide(DECISION_DENY))

        layout.addWidget(allow_btn)
        layout.addWidget(once_btn)
        layout.addWidget(deny_btn)

        outer.addWidget(card)

    # ── Timer ─────────────────────────────────────────────────────────────────

    def _start_timer(self):
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

    def _tick(self):
        self._countdown -= 1
        self._countdown_lbl.setText(f"Auto-deny in {self._countdown}s")
        self._progress.setValue(self._countdown)
        # Shift progress bar color as time runs out
        if self._countdown <= 10:
            meta = RESOURCE_META.get(self.resource, RESOURCE_META["camera"])
            self._progress.setStyleSheet(
                f"QProgressBar {{ background: {C['border']}; border: none; "
                f"border-radius: 2px; }} "
                f"QProgressBar::chunk {{ background: {C['danger']}; border-radius: 2px; }}"
            )
        if self._countdown <= 0:
            self._timer.stop()
            self._decide(DECISION_DENY)

    # ── Decision ──────────────────────────────────────────────────────────────

    def _decide(self, decision: str):
        self._timer.stop()
        remember = self._remember.isChecked() and decision != DECISION_ONCE
        self.decided.emit(decision, remember)
        self.accept()
