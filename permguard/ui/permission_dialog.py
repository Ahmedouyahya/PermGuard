"""
permission_dialog.py — Android-style permission request popup.

Shows when an app tries to access camera or microphone.
User choices: Allow (remember), Allow this time, Deny (remember).
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QCheckBox, QFrame
)
from PyQt6.QtCore  import Qt, QTimer, pyqtSignal
from PyQt6.QtGui   import QIcon, QFont, QPixmap

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

    AUTO_DENY_SECS = 30   # auto-deny after this many seconds of no response

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
        self.setModal(False)   # don't block other dialogs
        self.setStyleSheet(DIALOG_STYLE)
        self._build()
        self._start_timer()

        # Center on screen
        screen = self.screen().availableGeometry() if self.screen() else None
        if screen:
            self.move(
                screen.center().x() - 200,
                screen.y() + 80,   # near top — like Android
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
                border-radius: 16px;
                border: 1px solid {C['border']};
            }}
        """)
        card.setFixedWidth(400)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(28, 28, 28, 24)
        layout.setSpacing(16)

        # ── Top: resource icon + countdown ───────────────────────────────────
        top_row = QHBoxLayout()
        res_icon = QLabel(meta["icon"])
        res_icon.setFont(QFont("Noto Color Emoji", 32))
        res_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        res_icon.setStyleSheet(
            f"background: {meta['color']}22; border-radius: 12px;"
            f"padding: 8px; min-width: 60px; max-width: 60px;"
            f"min-height: 60px; max-height: 60px;"
        )
        top_row.addWidget(res_icon)
        top_row.addSpacing(12)

        top_text = QVBoxLayout()
        perm_label = QLabel(f"{meta['label']} Access Request")
        perm_label.setFont(QFont("Inter", 12, QFont.Weight.Bold))
        perm_label.setStyleSheet(f"color: {meta['color']};")
        self._countdown_lbl = QLabel(f"Auto-deny in {self._countdown}s")
        self._countdown_lbl.setStyleSheet(f"color: {C['muted']}; font-size: 11px;")
        top_text.addWidget(perm_label)
        top_text.addWidget(self._countdown_lbl)
        top_row.addLayout(top_text)
        top_row.addStretch()
        layout.addLayout(top_row)

        # ── Separator ────────────────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"background: {C['border']}; max-height: 1px;")
        layout.addWidget(sep)

        # ── App info ─────────────────────────────────────────────────────────
        app_label = QLabel(self.app_name)
        app_label.setFont(QFont("Inter", 18, QFont.Weight.Bold))
        app_label.setStyleSheet(f"color: {C['text']};")
        layout.addWidget(app_label)

        desc = QLabel(f"wants to {meta['desc']}")
        desc.setStyleSheet(f"color: {C['muted']}; font-size: 13px;")
        layout.addWidget(desc)

        if self.cmdline:
            cmd_lbl = QLabel(self.cmdline[:55] + ("…" if len(self.cmdline) > 55 else ""))
            cmd_lbl.setStyleSheet(
                f"color: {C['muted']}; font-size: 11px;"
                f"font-family: 'JetBrains Mono', monospace;"
                f"background: {C['bg']}; border-radius: 4px; padding: 4px 8px;"
            )
            layout.addWidget(cmd_lbl)

        pid_lbl = QLabel(f"PID {self.pid}")
        pid_lbl.setStyleSheet(f"color: {C['muted']}; font-size: 11px;")
        layout.addWidget(pid_lbl)

        # ── Remember checkbox ─────────────────────────────────────────────────
        self._remember = QCheckBox("Remember my choice for this app")
        self._remember.setChecked(True)
        layout.addWidget(self._remember)

        # ── Buttons ───────────────────────────────────────────────────────────
        layout.addSpacing(4)
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        deny_btn  = QPushButton("Deny")
        once_btn  = QPushButton("Allow this time")
        allow_btn = QPushButton("Allow")

        deny_btn.setObjectName("deny")
        once_btn.setObjectName("once")
        allow_btn.setObjectName("allow")

        deny_btn.clicked.connect(lambda: self._decide(DECISION_DENY))
        once_btn.clicked.connect(lambda: self._decide(DECISION_ONCE))
        allow_btn.clicked.connect(lambda: self._decide(DECISION_ALLOW))

        btn_layout.addWidget(deny_btn)
        btn_layout.addWidget(once_btn)
        btn_layout.addWidget(allow_btn)
        layout.addLayout(btn_layout)

        outer.addWidget(card)

    # ── Timer ─────────────────────────────────────────────────────────────────

    def _start_timer(self):
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

    def _tick(self):
        self._countdown -= 1
        self._countdown_lbl.setText(f"Auto-deny in {self._countdown}s")
        if self._countdown <= 0:
            self._timer.stop()
            self._decide(DECISION_DENY)

    # ── Decision ──────────────────────────────────────────────────────────────

    def _decide(self, decision: str):
        self._timer.stop()
        remember = self._remember.isChecked() and decision != DECISION_ONCE
        self.decided.emit(decision, remember)
        self.accept()
