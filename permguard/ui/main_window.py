"""
main_window.py — Main application window with all tabs.
"""
import subprocess, datetime, signal
from pathlib import Path
from collections import deque

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QLabel, QPushButton, QSystemTrayIcon, QMenu,
    QMessageBox, QGroupBox, QCheckBox, QSpinBox, QTextEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame,
    QDialog, QLineEdit, QComboBox
)
from PyQt6.QtCore  import Qt, QTimer, pyqtSignal
from PyQt6.QtGui   import QFont, QIcon, QAction, QColor, QPixmap, QPainter, QBrush

from .styles      import C, MAIN_STYLE
from .widgets     import PermTab, StatCard, hsep, build_table
from .permission_dialog import PermissionDialog, DECISION_ALLOW, DECISION_ONCE, DECISION_DENY
from ..core.data  import (get_camera_users, get_mic_users, get_screen_share,
                           get_network_conns, get_open_ports, get_usb_devices, get_top_procs,
                           get_camera_pids, get_mic_pids)
from ..core.system import (camera_is_blocked, set_camera_blocked,
                            mic_is_suspended, set_mic_suspended, kill_pid)
from ..core.permissions import PermissionDB, ALLOW, DENY, ASK, LOG_FILE
from ..core.firewall   import (block_app, unblock_app, is_blocked,
                                get_blocked_apps, clear_all_blocks,
                                iptables_available, restore_rules_on_startup)
from ..core.usb_control import get_usb_ports, set_authorized, disable_all_usb
from ..core.monitor    import DEFAULT_SENSITIVE, PACKAGE_MANAGERS

AUTOSTART_DIR  = Path.home() / ".config/autostart"
AUTOSTART_FILE = AUTOSTART_DIR / "permguard.desktop"


class MainWindow(QMainWindow):
    def __init__(self, db: PermissionDB, app_icon: QIcon = None, parent=None):
        super().__init__(parent)
        self.db            = db
        self._dialog_queue = deque()
        self._active_dialog = None
        self._prev_cam     = set()
        self._prev_mic     = set()
        self._app_icon     = app_icon or QIcon()
        self._file_mon     = None   # set by main() after construction

        self.setWindowTitle("PermGuard — Privacy Manager")
        self.setWindowIcon(self._app_icon)
        self.setMinimumSize(1100, 660)
        self.resize(1280, 740)
        self.setStyleSheet(MAIN_STYLE)

        self._build_ui()
        self._setup_tray()

        # Auto-refresh timer — honor saved interval if present
        saved_interval = db._db.get("__settings__", {}).get("interval", 5)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._auto_refresh)
        self._timer.start(int(saved_interval) * 1000)

        # Signal handlers registered in main() before Qt starts, but keep here as fallback
        try:
            signal.signal(signal.SIGTERM, lambda *_: self._quit())
            signal.signal(signal.SIGINT,  lambda *_: self._quit())
        except (OSError, ValueError):
            pass  # not in main thread or already registered

    # ── Permission handling (called by monitors) ──────────────────────────────

    # ── Process freeze / resume ───────────────────────────────────────────────

    def _freeze(self, pid: str):
        """SIGSTOP — suspend the process while user decides."""
        try:
            import os as _os, signal as _sig
            _os.kill(int(pid), _sig.SIGSTOP)
            self.db.log(f"Frozen PID {pid} pending decision")
        except Exception:
            pass   # process may be root-owned or already gone — continue anyway

    def _thaw(self, pid: str):
        """SIGCONT — resume a previously frozen process."""
        try:
            import os as _os, signal as _sig
            _os.kill(int(pid), _sig.SIGCONT)
        except Exception:
            pass

    def _pid_alive(self, pid: str) -> bool:
        """Return True if the given pid still exists."""
        try:
            import os as _os
            _os.kill(int(pid), 0)
            return True
        except (ProcessLookupError, ValueError):
            return False
        except PermissionError:
            return True   # exists but we can't signal it

    def handle_access_gone(self, pid: str):
        """Called when a monitor observes an access source disappeared.
        Cleans up queued dialogs and dismisses the active dialog if it's
        for this pid — prevents zombies and stale prompts."""
        # Drop any queued events for this pid
        if self._dialog_queue:
            self._dialog_queue = deque(
                e for e in self._dialog_queue if e.pid != pid)
        # If the active dialog is for this pid, dismiss it silently
        if self._active_dialog is not None and getattr(
                self._active_dialog, "pid", None) == pid:
            try:
                self._active_dialog.close()
            except Exception:
                pass
            self._active_dialog = None
            self._show_next_dialog()
        # Best-effort thaw in case the process is stopped but still alive
        self._thaw(pid)

    # ── Permission handling (called by monitors) ──────────────────────────────

    def handle_access(self, evt):
        """Called when a monitor detects a new access attempt."""
        decision = self.db.get(evt.app_name, evt.resource)
        if decision == ALLOW:
            self.db.log(f"Auto-allowed: {evt.app_name} → {evt.resource} (PID {evt.pid})")
            self.db.record_access(evt.app_name, evt.resource, "allow", evt.pid)
            return
        if decision == DENY:
            self.db.log(f"Auto-denied: {evt.app_name} → {evt.resource} (PID {evt.pid})")
            self.db.record_access(evt.app_name, evt.resource, "deny", evt.pid)
            self._enforce_deny(evt)
            return
        # Check if notifications are disabled for this resource type
        if evt.resource == "camera" and not self._sett_tab.cam_notify:
            self.db.log(f"Silently allowed (notifications off): {evt.app_name} → camera")
            return
        if evt.resource == "microphone" and not self._sett_tab.mic_notify:
            self.db.log(f"Silently allowed (notifications off): {evt.app_name} → microphone")
            return
        # ASK — freeze immediately so the app has no access while we ask
        self._freeze(evt.pid)
        self._dialog_queue.append(evt)
        if self._active_dialog is None:
            self._show_next_dialog()

    def _show_next_dialog(self):
        # Skip any queued events whose pid already died
        while self._dialog_queue:
            evt = self._dialog_queue[0]
            if self._pid_alive(evt.pid):
                break
            self._dialog_queue.popleft()
            self.db.log(f"Skipped dialog for dead PID {evt.pid} ({evt.app_name})")
        if not self._dialog_queue:
            self._active_dialog = None
            return
        evt = self._dialog_queue.popleft()
        dlg = PermissionDialog(
            app_name=evt.app_name,
            pid=evt.pid,
            resource=evt.resource,
            cmdline=evt.cmdline,
            stream_index=evt.stream_index,
        )
        self._active_dialog = dlg

        def on_decision(decision: str, remember: bool):
            self.db.log(
                f"User decision: {evt.app_name} → {evt.resource} = {decision}"
                f" (remember={remember}, PID {evt.pid})"
            )
            self.db.record_access(evt.app_name, evt.resource, decision, evt.pid)
            if remember and decision in (ALLOW, DENY):
                self.db.set(evt.app_name, evt.resource, decision)
                self._perm_tab.refresh()
            if decision in (ALLOW, DECISION_ONCE):
                # User allowed — unfreeze so the app can proceed
                self._thaw(evt.pid)
            else:
                # User denied — kill it (SIGKILL works on stopped processes)
                self._enforce_deny(evt)
            self._active_dialog = None
            self._show_next_dialog()
            if self._tray_ok:
                verb = "allowed" if decision in (ALLOW, DECISION_ONCE) else "denied"
                self._tray.showMessage(
                    "PermGuard",
                    f"{evt.app_name} was {verb} {evt.resource} access.",
                    QSystemTrayIcon.MessageIcon.Information, 3000
                )

        dlg.decided.connect(on_decision)
        dlg.show()
        dlg.raise_()

    def _enforce_deny(self, evt):
        if evt.resource == "camera":
            kill_pid(evt.pid)
        elif evt.resource == "microphone":
            if evt.stream_index and evt.stream_index != "?":
                from ..core.system import kill_mic_stream
                kill_mic_stream(evt.stream_index)
            else:
                kill_pid(evt.pid)
        elif evt.resource in ("filesystem", "package_install"):
            kill_pid(evt.pid)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Top bar
        bar = QWidget()
        bar.setStyleSheet(f"background:{C['panel']}; border-bottom:1px solid {C['border']};")
        bar.setFixedHeight(52)
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(20, 0, 20, 0)
        bl.setSpacing(10)

        from .. import __version__ as _ver
        logo = QLabel(f"🛡  PermGuard  v{_ver}")
        logo.setFont(QFont("Inter", 14, QFont.Weight.Bold))
        logo.setStyleSheet(f"color:{C['accent']}; background:transparent;")

        # Green dot + status text
        self._dot_lbl = QLabel("●")
        self._dot_lbl.setStyleSheet(
            f"color:{C['success']}; font-size:10px; background:transparent;")
        self._status_lbl = QLabel("Monitoring")
        self._status_lbl.setStyleSheet(
            f"color:{C['muted']}; font-size:12px; background:transparent;")

        bl.addWidget(logo)
        bl.addStretch()
        bl.addWidget(self._dot_lbl)
        bl.addWidget(self._status_lbl)
        root.addWidget(bar)

        # Tabs
        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)

        self._dash     = _DashboardTab(self.db)
        self._cam_tab  = PermTab("📷", "Camera",       "Apps accessing your webcam",
                                  get_camera_users, ["PID","Process","User","Command"], kill_col=0)
        self._mic_tab  = PermTab("🎤", "Microphone",   "Apps capturing audio input",
                                  get_mic_users,    ["PID","App Name","User","Command"], kill_col=0)
        self._scr_tab  = PermTab("🖥", "Screen Share", "Active screen recording / sharing",
                                  get_screen_share, ["PID","Service","Info"])
        self._net_tab  = _NetworkTab()
        self._usb_tab  = _USBTab()
        self._port_tab = PermTab("🔒", "Open Ports",   "Listening ports on this machine",
                                  get_open_ports,   ["Proto","Address","Process","PID"])
        self._proc_tab = _ProcessTab()
        self._fw_tab   = _FirewallTab()
        self._file_tab = _FileAccessTab(self.db)
        self._perm_tab = _PermissionsTab(self.db)
        self._sett_tab = _SettingsTab(self.db)

        self._tabs.addTab(self._dash,     "🏠  Dashboard")
        self._tabs.addTab(self._cam_tab,  "📷  Camera")
        self._tabs.addTab(self._mic_tab,  "🎤  Mic")
        self._tabs.addTab(self._scr_tab,  "🖥  Screen")
        self._tabs.addTab(self._net_tab,  "🌐  Network")
        self._tabs.addTab(self._usb_tab,  "🔌  USB")
        self._tabs.addTab(self._port_tab, "🔒  Ports")
        self._tabs.addTab(self._proc_tab, "⚙️  Processes")
        self._tabs.addTab(self._fw_tab,   "🔥  Firewall")
        self._tabs.addTab(self._file_tab, "📂  Files")
        self._tabs.addTab(self._perm_tab, "🔑  Permissions")
        self._tabs.addTab(self._sett_tab, "⚙  Settings")

        self._dash.switch_tab.connect(self._tabs.setCurrentIndex)
        self._sett_tab.interval_changed.connect(
            lambda s: self._timer.setInterval(s * 1000))
        self._sett_tab.protected_paths_changed.connect(self._on_paths_changed)
        self._file_tab.paths_changed.connect(self._on_paths_changed)

        root.addWidget(self._tabs)

    def _setup_tray(self):
        self._tray = QSystemTrayIcon(self)
        self._tray.setIcon(self._app_icon if not self._app_icon.isNull()
                           else QIcon.fromTheme("security-high",
                                QIcon.fromTheme("dialog-password")))
        menu = QMenu()
        show_a = QAction("Show PermGuard", self)
        show_a.triggered.connect(self.show)
        quit_a = QAction("Quit", self)
        quit_a.triggered.connect(self._quit)
        menu.addAction(show_a)
        menu.addSeparator()
        menu.addAction(quit_a)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(
            lambda r: self.show() if r == QSystemTrayIcon.ActivationReason.Trigger else None)
        self._tray_ok = QSystemTrayIcon.isSystemTrayAvailable()
        self._tray_indicator_state = ""  # "", "cam", "mic", "both"
        if self._tray_ok:
            self._tray.show()

    def _make_indicator_icon(self, cam_live: bool, mic_live: bool) -> QIcon:
        """Overlay colored dots on the tray icon to indicate live cam/mic."""
        base = self._app_icon
        size = 64
        pixmap = base.pixmap(size, size) if not base.isNull() else QPixmap(size, size)
        if base.isNull():
            pixmap.fill(QColor(C["accent"]))

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        dot_size = 16
        if cam_live:
            painter.setBrush(QBrush(QColor(C["danger"])))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(size - dot_size - 2, 2, dot_size, dot_size)
        if mic_live:
            painter.setBrush(QBrush(QColor(C["warning"])))
            painter.setPen(Qt.PenStyle.NoPen)
            y = 2 if not cam_live else dot_size + 4
            painter.drawEllipse(size - dot_size - 2, y, dot_size, dot_size)
        painter.end()
        return QIcon(pixmap)

    def _update_tray_indicator(self, cam_live: bool, mic_live: bool):
        if not self._tray_ok:
            return
        state = f"{'c' if cam_live else ''}{'m' if mic_live else ''}"
        if state == self._tray_indicator_state:
            return  # no change
        self._tray_indicator_state = state
        if not cam_live and not mic_live:
            self._tray.setIcon(self._app_icon)
            self._tray.setToolTip("PermGuard — Monitoring")
        else:
            self._tray.setIcon(self._make_indicator_icon(cam_live, mic_live))
            parts = []
            if cam_live:
                parts.append("Camera LIVE")
            if mic_live:
                parts.append("Mic LIVE")
            self._tray.setToolTip(f"PermGuard — {' + '.join(parts)}")

    # ── Auto-refresh ──────────────────────────────────────────────────────────

    def _auto_refresh(self):
        idx = self._tabs.currentIndex()

        if idx == 0:
            # Dashboard is visible — fetch all summary data
            cam  = get_camera_users()
            mic  = get_mic_users()
            scr  = get_screen_share()
            net  = get_network_conns()
            usb  = get_usb_devices()
            port = get_open_ports()
            self._dash.refresh(cam, mic, scr, net, usb, port)
        else:
            # Only refresh the active tab (avoid fetching everything)
            live = [None, self._cam_tab, self._mic_tab, self._scr_tab,
                    self._net_tab, self._usb_tab, self._port_tab,
                    self._proc_tab, self._fw_tab, self._file_tab,
                    self._perm_tab, self._sett_tab]
            if idx < len(live) and live[idx]:
                live[idx].refresh()

        # Update tray privacy indicators (lightweight check ~3ms each)
        cam_live = bool(get_camera_pids())
        mic_live = bool(get_mic_pids())
        self._update_tray_indicator(cam_live, mic_live)

        # Also update the top bar indicator
        if cam_live or mic_live:
            parts = []
            if cam_live:
                parts.append("📷 Camera")
            if mic_live:
                parts.append("🎤 Mic")
            self._dot_lbl.setStyleSheet(
                f"color:{C['danger']}; font-size:10px; background:transparent;")
            self._status_lbl.setText(f"{' + '.join(parts)} LIVE")
            self._status_lbl.setStyleSheet(
                f"color:{C['danger']}; font-size:12px; font-weight:bold; background:transparent;")
        else:
            now = datetime.datetime.now().strftime("%H:%M:%S")
            self._status_lbl.setText(f"Updated {now}")
            self._status_lbl.setStyleSheet(
                f"color:{C['muted']}; font-size:12px; background:transparent;")
            self._dot_lbl.setStyleSheet(
                f"color:{C['success']}; font-size:10px; background:transparent;")

    # ── Close ─────────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        event.ignore()
        self.hide()
        if self._tray_ok:
            self._tray.showMessage("PermGuard",
                "Monitoring in background. Right-click tray icon → Quit to exit.",
                QSystemTrayIcon.MessageIcon.Information, 3000)
        else:
            self.showMinimized()

    def _on_paths_changed(self, paths: list):
        """Relay updated protected path list to the file monitor."""
        if self._file_mon is not None:
            self._file_mon.set_paths(paths)

    def _quit(self):
        self.db.log("PermGuard closed by user")
        from PyQt6.QtWidgets import QApplication
        QApplication.quit()


# ── Dashboard Tab (Android 12-style Privacy Dashboard) ───────────────────────

_RESOURCE_ICONS = {
    "camera": "📷", "microphone": "🎤", "screen": "🖥",
    "filesystem": "📂", "package_install": "📦", "clipboard": "📋",
}
_RESOURCE_COLORS = {
    "camera": C["danger"], "microphone": C["warning"], "screen": C["purple"],
    "filesystem": C["accent"], "package_install": C["danger"], "clipboard": C["success"],
}
_DECISION_COLORS = {
    "allow": C["success"], "deny": C["danger"], "once": C["warning"],
}


class _DashboardTab(QWidget):
    switch_tab = pyqtSignal(int)

    def __init__(self, db: PermissionDB):
        super().__init__()
        self.db = db

        from PyQt6.QtWidgets import QScrollArea, QGridLayout
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        # ── Title ────────────────────────────────────────────────────────────
        title = QLabel("Privacy Dashboard")
        title.setFont(QFont("Inter", 16, QFont.Weight.Bold))
        title.setStyleSheet(f"color:{C['text']};")
        layout.addWidget(title)

        subtitle = QLabel("What apps accessed in the last 24 hours")
        subtitle.setStyleSheet(f"color:{C['muted']}; font-size:12px;")
        layout.addWidget(subtitle)
        layout.addWidget(hsep())

        # ── Live stat cards ──────────────────────────────────────────────────
        grid = QGridLayout()
        grid.setSpacing(12)
        self._cards = {
            "camera":  StatCard("📷", "Camera Access",   C["danger"]),
            "mic":     StatCard("🎤", "Mic Access",      C["danger"]),
            "screen":  StatCard("🖥", "Screen Share",    C["warning"]),
            "network": StatCard("🌐", "Network Conns",   C["accent"]),
            "usb":     StatCard("🔌", "USB Devices",     C["purple"]),
            "ports":   StatCard("🔒", "Open Ports",      C["warning"]),
        }
        positions  = [(0,0),(0,1),(0,2),(1,0),(1,1),(1,2)]
        tab_indices = [1, 2, 3, 4, 5, 6]
        for (key, card), pos, idx in zip(self._cards.items(), positions, tab_indices):
            grid.addWidget(card, *pos)
            card.clicked.connect(lambda i=idx: self.switch_tab.emit(i))
        layout.addLayout(grid)

        # ── Quick block toggles ──────────────────────────────────────────────
        layout.addWidget(hsep())
        blk_title = QLabel("Quick Blocks")
        blk_title.setFont(QFont("Inter", 13, QFont.Weight.Bold))
        blk_title.setStyleSheet(f"color:{C['text']};")
        layout.addWidget(blk_title)

        blk_row = QHBoxLayout()
        self._cam_btn = QPushButton()
        self._mic_btn = QPushButton()
        self._cam_btn.setMinimumWidth(200)
        self._mic_btn.setMinimumWidth(200)
        self._cam_btn.clicked.connect(self._toggle_cam)
        self._mic_btn.clicked.connect(self._toggle_mic)
        blk_row.addWidget(self._cam_btn)
        blk_row.addWidget(self._mic_btn)
        blk_row.addStretch()
        layout.addLayout(blk_row)
        self._update_block_btns()

        # ── 24h Privacy Timeline ─────────────────────────────────────────────
        layout.addWidget(hsep())
        tl_hdr = QHBoxLayout()
        tl_title = QLabel("Privacy Timeline — Last 24 Hours")
        tl_title.setFont(QFont("Inter", 13, QFont.Weight.Bold))
        tl_title.setStyleSheet(f"color:{C['text']};")
        tl_hdr.addWidget(tl_title)
        tl_hdr.addStretch()

        # Summary badges
        self._tl_allow_badge = QLabel()
        self._tl_deny_badge = QLabel()
        tl_hdr.addWidget(self._tl_allow_badge)
        tl_hdr.addWidget(self._tl_deny_badge)
        layout.addLayout(tl_hdr)

        self._timeline_body = QVBoxLayout()
        self._timeline_body.setSpacing(4)
        layout.addLayout(self._timeline_body)

        layout.addStretch()
        note = QLabel("Cards auto-refresh every 5s  ·  Timeline updates on each access event")
        note.setStyleSheet(f"color:{C['muted']}; font-size:11px;")
        layout.addWidget(note)

        scroll.setWidget(inner)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        self._refresh_timeline()

    def refresh(self, cam, mic, scr, net, usb, ports):
        self._cards["camera"].update(len(cam))
        self._cards["mic"].update(len(mic))
        self._cards["screen"].update(len(scr))
        self._cards["network"].update(len(net))
        self._cards["usb"].update(len(usb))
        self._cards["ports"].update(len(ports))
        self._update_block_btns()
        self._refresh_timeline()

    # ── Timeline ─────────────────────────────────────────────────────────────

    def _refresh_timeline(self):
        # Clear old
        while self._timeline_body.count():
            c = self._timeline_body.takeAt(0)
            if c.widget():
                c.widget().deleteLater()

        events = self.db.get_timeline(24)
        if not events:
            lbl = QLabel("No access events in the last 24 hours — your privacy is clean.")
            lbl.setStyleSheet(f"color:{C['muted']}; font-size:13px; padding:16px;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._timeline_body.addWidget(lbl)
            self._tl_allow_badge.setText("")
            self._tl_deny_badge.setText("")
            return

        # Summary counts
        n_allow = sum(1 for e in events if e.get("decision") in ("allow", "once"))
        n_deny  = sum(1 for e in events if e.get("decision") == "deny")
        self._tl_allow_badge.setText(f"  {n_allow} allowed  ")
        self._tl_allow_badge.setStyleSheet(
            f"background:{C['success']};color:{C['bg']};border-radius:10px;"
            f"padding:3px 10px;font-weight:700;font-size:11px;")
        self._tl_deny_badge.setText(f"  {n_deny} denied  ")
        self._tl_deny_badge.setStyleSheet(
            f"background:{C['danger']};color:white;border-radius:10px;"
            f"padding:3px 10px;font-weight:700;font-size:11px;")

        # Group by hour for display, show last 50 individual events
        for e in events[:50]:
            row = self._make_timeline_row(e)
            self._timeline_body.addWidget(row)

    def _make_timeline_row(self, event: dict) -> QWidget:
        w = QFrame()
        w.setStyleSheet(
            f"QFrame {{ background:{C['surface']}; border-radius:8px;"
            f"border:1px solid {C['border']}; }}")
        h = QHBoxLayout(w)
        h.setContentsMargins(12, 8, 12, 8)
        h.setSpacing(10)

        resource = event.get("resource", "?")
        decision = event.get("decision", "?")
        app      = event.get("app", "?")
        ts_str   = event.get("ts", "")

        # Time
        try:
            ts = datetime.datetime.fromisoformat(ts_str)
            time_str = ts.strftime("%H:%M")
        except Exception:
            time_str = "??:??"
        time_lbl = QLabel(time_str)
        time_lbl.setFixedWidth(50)
        time_lbl.setStyleSheet(
            f"color:{C['muted']}; font-size:12px; font-family:'JetBrains Mono',monospace;"
            f"background:transparent;")
        h.addWidget(time_lbl)

        # Resource icon
        icon_lbl = QLabel(_RESOURCE_ICONS.get(resource, "?"))
        icon_lbl.setFixedWidth(24)
        icon_lbl.setStyleSheet("background:transparent; font-size:14px;")
        h.addWidget(icon_lbl)

        # App name
        app_lbl = QLabel(app)
        app_lbl.setFont(QFont("Inter", 12, QFont.Weight.Bold))
        app_lbl.setStyleSheet(f"color:{C['text']}; background:transparent;")
        h.addWidget(app_lbl)

        # Resource name
        res_lbl = QLabel(resource.replace("_", " ").title())
        res_lbl.setStyleSheet(
            f"color:{_RESOURCE_COLORS.get(resource, C['muted'])}; font-size:11px;"
            f"background:transparent;")
        h.addWidget(res_lbl)

        h.addStretch()

        # Decision badge
        dec_color = _DECISION_COLORS.get(decision, C["muted"])
        dec_lbl = QLabel(decision.upper())
        dec_lbl.setStyleSheet(
            f"background:{dec_color}; color:{'white' if decision == 'deny' else C['bg']};"
            f"border-radius:8px; padding:2px 10px; font-weight:700; font-size:10px;"
            f"letter-spacing:0.5px;")
        h.addWidget(dec_lbl)

        return w

    # ── Block toggles ────────────────────────────────────────────────────────

    def _update_block_btns(self):
        blocked = camera_is_blocked()
        sus     = mic_is_suspended()
        self._cam_btn.setText("🎥  Unblock Camera" if blocked else "🚫  Block Camera")
        self._cam_btn.setObjectName("success" if blocked else "danger")
        self._mic_btn.setText("🎙  Unblock Mic" if sus else "🚫  Block Mic")
        self._mic_btn.setObjectName("success" if sus else "danger")
        self._cam_btn.setStyle(self._cam_btn.style())
        self._mic_btn.setStyle(self._mic_btn.style())

    def _toggle_cam(self):
        ok, err = set_camera_blocked(not camera_is_blocked())
        if not ok:
            QMessageBox.warning(self, "Error", f"Cannot toggle camera:\n{err}")
        self._update_block_btns()

    def _toggle_mic(self):
        ok, err = set_mic_suspended(not mic_is_suspended())
        if not ok and err:
            QMessageBox.warning(self, "Error", f"Cannot toggle microphone:\n{err}")
        self._update_block_btns()


# ── Permissions Management Tab (per-app grouped view) ────────────────────────

class _PermissionsTab(QWidget):
    def __init__(self, db: PermissionDB):
        super().__init__()
        self.db = db

        from PyQt6.QtWidgets import QScrollArea
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Header (outside scroll area)
        hdr_widget = QWidget()
        hdr_layout = QVBoxLayout(hdr_widget)
        hdr_layout.setContentsMargins(20, 20, 20, 0)
        hdr_layout.setSpacing(12)

        hdr = QHBoxLayout()
        ico = QLabel("🔑")
        ico.setFont(QFont("Noto Color Emoji", 20))
        ico.setFixedWidth(32)
        ttl = QLabel("App Permissions")
        ttl.setFont(QFont("Inter", 15, QFont.Weight.Bold))
        ttl.setStyleSheet(f"color:{C['text']};")
        sub = QLabel("Tap an app to manage all its permissions")
        sub.setStyleSheet(f"color:{C['muted']}; font-size:12px;")
        left = QVBoxLayout()
        left.setSpacing(2)
        left.addWidget(ttl)
        left.addWidget(sub)
        hdr.addWidget(ico)
        hdr.addLayout(left)
        hdr.addStretch()

        add_btn = QPushButton("+ Add Rule")
        add_btn.setObjectName("success")
        add_btn.setMinimumWidth(120)
        add_btn.clicked.connect(self._add_rule_dialog)
        hdr.addWidget(add_btn)
        hdr_layout.addLayout(hdr)
        hdr_layout.addWidget(hsep())
        layout.addWidget(hdr_widget)

        # Scrollable body
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self._scroll_inner = QWidget()
        self._body = QVBoxLayout(self._scroll_inner)
        self._body.setContentsMargins(20, 8, 20, 8)
        self._body.setSpacing(8)
        scroll.setWidget(self._scroll_inner)
        layout.addWidget(scroll)

        # Footer
        foot_widget = QWidget()
        foot_layout = QHBoxLayout(foot_widget)
        foot_layout.setContentsMargins(20, 8, 20, 16)
        foot_layout.addStretch()
        clr_btn = QPushButton("Reset All Rules")
        clr_btn.setObjectName("danger")
        clr_btn.clicked.connect(self._reset_all)
        r_btn = QPushButton("↻  Refresh")
        r_btn.setObjectName("flat")
        r_btn.clicked.connect(self.refresh)
        foot_layout.addWidget(clr_btn)
        foot_layout.addWidget(r_btn)
        layout.addWidget(foot_widget)

        self.refresh()

    def refresh(self):
        while self._body.count():
            c = self._body.takeAt(0)
            if c.widget():
                c.widget().deleteLater()

        rules = self.db.all_rules()
        if not rules:
            from .widgets import _EmptyState
            self._body.addWidget(_EmptyState(
                "No saved rules yet — dialogs will appear when apps request access",
                icon="🔑"
            ))
            return

        # Group rules by app
        apps: dict[str, list[tuple[str, str]]] = {}
        for app, res, decision in rules:
            apps.setdefault(app, []).append((res, decision))

        # Get last-seen times from timeline
        last_seen = self.db.get_app_last_seen()

        for app_name, perms in sorted(apps.items()):
            card = self._make_app_card(app_name, perms, last_seen.get(app_name))
            self._body.addWidget(card)

    def _make_app_card(self, app_name: str, perms: list[tuple[str, str]],
                       last_seen_ts: str | None) -> QFrame:
        card = QFrame()
        card.setStyleSheet(
            f"QFrame {{ background:{C['surface']}; border-radius:10px;"
            f"border:1px solid {C['border']}; }}")

        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        # App header row
        top = QHBoxLayout()
        top.setSpacing(10)

        app_lbl = QLabel(app_name)
        app_lbl.setFont(QFont("Inter", 13, QFont.Weight.Bold))
        app_lbl.setStyleSheet(f"color:{C['text']}; background:transparent;")
        top.addWidget(app_lbl)

        # Last seen
        if last_seen_ts:
            try:
                from datetime import datetime as _dt
                ts = _dt.fromisoformat(last_seen_ts)
                ago = _dt.now() - ts
                if ago.days > 0:
                    seen_str = f"{ago.days}d ago"
                elif ago.seconds >= 3600:
                    seen_str = f"{ago.seconds // 3600}h ago"
                elif ago.seconds >= 60:
                    seen_str = f"{ago.seconds // 60}m ago"
                else:
                    seen_str = "just now"
                seen_lbl = QLabel(f"Last seen: {seen_str}")
                seen_lbl.setStyleSheet(
                    f"color:{C['muted']}; font-size:11px; background:transparent;")
                top.addWidget(seen_lbl)
            except Exception:
                pass

        top.addStretch()

        # Revoke all button
        revoke_all = QPushButton("Revoke All")
        revoke_all.setObjectName("flat")
        revoke_all.setMinimumWidth(110)
        revoke_all.clicked.connect(lambda _, a=app_name: self._revoke_all_for_app(a))
        top.addWidget(revoke_all)

        layout.addLayout(top)

        # Permission rows
        for resource, decision in perms:
            prow = QHBoxLayout()
            prow.setSpacing(8)

            icon = _RESOURCE_ICONS.get(resource, "?")
            icon_lbl = QLabel(icon)
            icon_lbl.setFixedWidth(24)
            icon_lbl.setStyleSheet("background:transparent; font-size:13px;")
            prow.addWidget(icon_lbl)

            res_lbl = QLabel(resource.replace("_", " ").title())
            res_lbl.setStyleSheet(
                f"color:{C['text']}; font-size:12px; background:transparent;")
            res_lbl.setFixedWidth(130)
            prow.addWidget(res_lbl)

            dec_color = C["success"] if decision == "allow" else C["danger"]
            dec_lbl = QLabel(decision.upper())
            dec_lbl.setStyleSheet(
                f"background:{dec_color}; "
                f"color:{'white' if decision == 'deny' else C['bg']};"
                f"border-radius:8px; padding:2px 10px; font-weight:700;"
                f"font-size:10px; letter-spacing:0.5px;")
            prow.addWidget(dec_lbl)

            prow.addStretch()

            # Toggle button
            toggle = QPushButton("Switch to Deny" if decision == "allow" else "Switch to Allow")
            toggle.setObjectName("flat")
            toggle.setMinimumWidth(160)
            toggle.clicked.connect(
                lambda _, a=app_name, r=resource, d=decision:
                    self._toggle_rule(a, r, d))
            prow.addWidget(toggle)

            revoke = QPushButton("×")
            revoke.setObjectName("flat")
            revoke.setFixedWidth(30)
            revoke.setToolTip("Revoke (will ask again)")
            revoke.clicked.connect(
                lambda _, a=app_name, r=resource: self._revoke(a, r))
            prow.addWidget(revoke)

            layout.addLayout(prow)

        return card

    def _toggle_rule(self, app: str, resource: str, current: str):
        new = "deny" if current == "allow" else "allow"
        self.db.set(app, resource, new)
        self.refresh()

    def _revoke_all_for_app(self, app: str):
        self.db.remove(app)
        self.refresh()

    def _add_rule_dialog(self):
        """Small dialog to manually add an allow/deny rule."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Add Permission Rule")
        dlg.setFixedWidth(360)
        dlg.setStyleSheet(
            f"QDialog {{ background:{C['panel']}; color:{C['text']}; "
            f"font-family:Inter,sans-serif; }}"
            f"QLabel {{ color:{C['text']}; }}"
            f"QLineEdit, QComboBox {{ background:{C['surface']}; color:{C['text']}; "
            f"border:1px solid {C['border']}; border-radius:6px; padding:6px 10px; font-size:13px; }}"
            f"QLineEdit:focus, QComboBox:focus {{ border-color:{C['accent']}; }}"
            f"QComboBox::drop-down {{ border:none; }}"
            f"QPushButton {{ background:{C['surface']}; color:{C['text']}; "
            f"border:1px solid {C['border']}; border-radius:6px; padding:8px 18px; font-weight:500; }}"
            f"QPushButton:hover {{ background:{C['border']}; border-color:{C['accent']}; color:{C['accent']}; }}"
            f"QPushButton#save {{ background:{C['success']}; color:{C['bg']}; border-color:{C['success']}; }}"
            f"QPushButton#save:hover {{ background:#8fbcbb; }}"
        )

        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(22, 22, 22, 20)
        lay.setSpacing(14)

        title_lbl = QLabel("Add Permission Rule")
        title_lbl.setFont(QFont("Inter", 13, QFont.Weight.Bold))
        lay.addWidget(title_lbl)

        sub_lbl = QLabel("Set a permanent allow or deny rule for any app.")
        sub_lbl.setStyleSheet(f"color:{C['muted']}; font-size:12px;")
        sub_lbl.setWordWrap(True)
        lay.addWidget(sub_lbl)

        # App name input
        app_lbl = QLabel("App name (process name, e.g. firefox, zoom, obs)")
        app_lbl.setStyleSheet(f"color:{C['muted']}; font-size:11px;")
        app_input = QLineEdit()
        app_input.setPlaceholderText("e.g. firefox")
        lay.addWidget(app_lbl)
        lay.addWidget(app_input)

        # Resource picker
        res_lbl = QLabel("Resource")
        res_lbl.setStyleSheet(f"color:{C['muted']}; font-size:11px;")
        res_combo = QComboBox()
        res_combo.addItems([
            "camera", "microphone", "screen",
            "filesystem", "package_install",
        ])
        lay.addWidget(res_lbl)
        lay.addWidget(res_combo)

        # Decision picker
        dec_lbl = QLabel("Decision")
        dec_lbl.setStyleSheet(f"color:{C['muted']}; font-size:11px;")
        dec_combo = QComboBox()
        dec_combo.addItems(["allow", "deny"])
        lay.addWidget(dec_lbl)
        lay.addWidget(dec_combo)

        lay.addSpacing(4)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        cancel_btn = QPushButton("Cancel")
        save_btn   = QPushButton("Save Rule")
        save_btn.setObjectName("save")
        cancel_btn.clicked.connect(dlg.reject)

        def _save():
            name = app_input.text().strip()
            if not name:
                app_input.setStyleSheet(
                    f"border:1px solid {C['danger']}; border-radius:6px; "
                    f"padding:6px 10px; background:{C['surface']}; color:{C['text']};")
                return
            self.db.set(name, res_combo.currentText(), dec_combo.currentText())
            self.db.log(f"Manual rule: {name} → {res_combo.currentText()} = {dec_combo.currentText()}")
            dlg.accept()
            self.refresh()

        save_btn.clicked.connect(_save)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        lay.addLayout(btn_row)

        dlg.exec()

    def _revoke(self, app: str, resource: str):
        self.db.remove(app, resource)
        self.refresh()

    def _reset_all(self):
        reply = QMessageBox.question(self, "Reset All",
            "Delete all saved permission rules?\nApps will be asked again next time.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.db.reset_all()
            self.refresh()


# ── Processes Tab ─────────────────────────────────────────────────────────────

class _ProcessTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        hdr = QHBoxLayout()
        ico = QLabel("⚙️")
        ico.setFont(QFont("Noto Color Emoji", 18))
        ttl = QLabel("Running Processes")
        ttl.setFont(QFont("Inter", 15, QFont.Weight.Bold))
        ttl.setStyleSheet(f"color:{C['accent']};")
        sub = QLabel("Top processes by CPU — kill suspicious ones")
        sub.setStyleSheet(f"color:{C['muted']}; font-size:12px;")
        left = QVBoxLayout()
        left.setSpacing(2)
        left.addWidget(ttl)
        left.addWidget(sub)
        hdr.addWidget(ico)
        hdr.addLayout(left)
        hdr.addStretch()
        layout.addLayout(hdr)
        layout.addWidget(hsep())
        self._body = QVBoxLayout()
        layout.addLayout(self._body)
        foot = QHBoxLayout()
        foot.addStretch()
        r_btn = QPushButton("⟳  Refresh")
        r_btn.setObjectName("flat")
        r_btn.clicked.connect(self.refresh)
        foot.addWidget(r_btn)
        layout.addLayout(foot)
        self.refresh()

    def refresh(self):
        rows = get_top_procs()
        while self._body.count():
            c = self._body.takeAt(0)
            if c.widget():
                c.widget().deleteLater()
        tbl = build_table(["PID","User","CPU%","MEM%","Command"], rows,
                           kill_col=0, refresh_fn=self.refresh)
        self._body.addWidget(tbl)


# ── Settings Tab ──────────────────────────────────────────────────────────────

class _SettingsTab(QWidget):
    interval_changed       = pyqtSignal(int)
    protected_paths_changed = pyqtSignal(list)

    def __init__(self, db: PermissionDB):
        super().__init__()
        self.db = db
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)
        title = QLabel("Settings")
        title.setFont(QFont("Inter", 16, QFont.Weight.Bold))
        title.setStyleSheet(f"color:{C['accent']};")
        layout.addWidget(title)
        layout.addWidget(hsep())

        # General
        gen = QGroupBox("GENERAL")
        gl = QVBoxLayout(gen)
        self._autostart_cb = QCheckBox("Launch PermGuard at login")
        self._autostart_cb.setChecked(AUTOSTART_FILE.exists())
        self._autostart_cb.toggled.connect(self._toggle_autostart)
        gl.addWidget(self._autostart_cb)
        ir = QHBoxLayout()
        ir.addWidget(QLabel("Auto-refresh every"))
        self._interval = QSpinBox()
        self._interval.setRange(2, 120)
        saved_interval = self.db._db.get("__settings__", {}).get("interval", 5)
        self._interval.setValue(saved_interval)
        self._interval.setSuffix(" seconds")
        def _on_interval_changed(v):
            self.interval_changed.emit(v)
            self._save_setting("interval", v)
        self._interval.valueChanged.connect(_on_interval_changed)
        ir.addWidget(self._interval)
        ir.addStretch()
        gl.addLayout(ir)
        layout.addWidget(gen)

        # Notifications (persisted in db under __settings__)
        settings = self.db._db.get("__settings__", {})
        notif = QGroupBox("NOTIFICATIONS")
        nl = QVBoxLayout(notif)
        self._notif_cam = QCheckBox("Show dialog when camera access starts")
        self._notif_mic = QCheckBox("Show dialog when microphone access starts")
        self._notif_cam.setChecked(settings.get("notif_cam", True))
        self._notif_mic.setChecked(settings.get("notif_mic", True))
        self._notif_cam.toggled.connect(
            lambda on: self._save_setting("notif_cam", on))
        self._notif_mic.toggled.connect(
            lambda on: self._save_setting("notif_mic", on))
        nl.addWidget(self._notif_cam)
        nl.addWidget(self._notif_mic)
        layout.addWidget(notif)

        # Flatpak
        fp = QGroupBox("FLATPAK APP SANDBOX")
        fpl = QVBoxLayout(fp)
        fpl.addWidget(QLabel("Manage Flatpak app permissions (camera, mic, filesystem…)"))
        fsb = QPushButton("Open Flatseal")
        fsb.setMinimumWidth(160)
        fsb.clicked.connect(self._open_flatseal)
        fpl.addWidget(fsb)
        layout.addWidget(fp)

        # Updates
        from .. import __version__ as _app_ver
        upd = QGroupBox("UPDATES")
        upl = QVBoxLayout(upd)
        upl.setSpacing(8)
        upl.setContentsMargins(16, 12, 16, 14)
        ver_lbl = QLabel(f"Installed version:  <b>{_app_ver}</b>")
        ver_lbl.setStyleSheet(f"color:{C['text']};")
        upl.addWidget(ver_lbl)
        desc_lbl = QLabel("Fetches the latest source from GitHub and reinstalls.")
        desc_lbl.setStyleSheet(f"color:{C['muted']}; font-size:12px;")
        upl.addWidget(desc_lbl)
        row = QHBoxLayout()
        row.setContentsMargins(0, 4, 0, 0)
        self._update_btn = QPushButton("⟳  Update to latest version")
        self._update_btn.setObjectName("success")
        self._update_btn.setFixedSize(260, 36)
        self._update_btn.clicked.connect(self._run_update)
        row.addWidget(self._update_btn)
        row.addStretch()
        upl.addLayout(row)
        self._update_output = QTextEdit()
        self._update_output.setReadOnly(True)
        self._update_output.setFixedHeight(160)
        self._update_output.setStyleSheet(
            f"background:{C['bg']}; color:{C['muted']}; border:1px solid {C['border']};"
            f"border-radius:6px; font-family:'JetBrains Mono',monospace; font-size:11px;")
        self._update_output.setVisible(False)
        upl.addWidget(self._update_output)
        upd.setFixedHeight(140)
        self._update_group = upd
        layout.addWidget(upd)

        # Log
        log_g = QGroupBox("EVENT LOG")
        logl = QVBoxLayout(log_g)
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFixedHeight(160)
        logl.addWidget(self._log)
        clr = QPushButton("Clear Log")
        clr.setObjectName("flat")
        clr.clicked.connect(self._clear_log)
        logl.addWidget(clr)
        layout.addWidget(log_g)

        layout.addStretch()
        self._refresh_log()

    @property
    def cam_notify(self): return self._notif_cam.isChecked()
    @property
    def mic_notify(self): return self._notif_mic.isChecked()

    def _save_setting(self, key: str, value):
        """Persist a single setting to the permission DB under __settings__."""
        if "__settings__" not in self.db._db:
            self.db._db["__settings__"] = {}
        self.db._db["__settings__"][key] = value
        self.db.save()

    def refresh(self):
        self._refresh_log()

    def _refresh_log(self):
        lines = self.db.get_log(80)
        self._log.setPlainText("\n".join(lines) if lines else "No events yet.")

    def _clear_log(self):
        LOG_FILE.write_text("")
        self._refresh_log()

    def _toggle_autostart(self, on: bool):
        if on:
            AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
            import shutil, sys
            # Prefer the installed launcher; fall back to running as a module
            # so relative imports work (`python3 main.py` would break them).
            launcher = shutil.which("permguard")
            if not launcher:
                # Run PermGuard from the package we're executing from
                pkg_parent = Path(__file__).resolve().parents[2]
                py = sys.executable or "python3"
                launcher = f'env PYTHONPATH="{pkg_parent}" {py} -m permguard'
            icon_path = Path.home() / ".local/share/permguard/assets/icon.svg"
            AUTOSTART_FILE.write_text(
                f"[Desktop Entry]\nName=PermGuard\n"
                f"Comment=PermGuard privacy monitor\n"
                f"Exec={launcher}\n"
                f"Icon={icon_path}\n"
                f"Terminal=false\n"
                f"Type=Application\n"
                f"X-KDE-autostart-after=panel\n"
                f"X-GNOME-Autostart-enabled=true\n"
            )
        else:
            AUTOSTART_FILE.unlink(missing_ok=True)

    def _open_flatseal(self):
        try:
            subprocess.Popen(["flatpak", "run", "com.github.tchx84.Flatseal"])
        except Exception:
            QMessageBox.information(self, "Flatseal",
                "Install with:\n\nflatpak install flathub com.github.tchx84.Flatseal")

    def _run_update(self):
        from ..core.updater import UpdateWorker, restart_permguard
        self._update_btn.setEnabled(False)
        self._update_btn.setText("Updating…")
        self._update_output.setVisible(True)
        self._update_output.clear()
        self._update_group.setFixedHeight(320)

        worker = UpdateWorker(self)
        self._update_worker = worker   # keep alive

        def on_line(msg: str):
            self._update_output.append(msg)
            sb = self._update_output.verticalScrollBar()
            sb.setValue(sb.maximum())

        def on_done(ok: bool, summary: str):
            self._update_btn.setEnabled(True)
            self._update_btn.setText("⟳  Update to latest version")
            if not ok:
                QMessageBox.warning(self, "Update Failed", summary)
                return
            reply = QMessageBox.question(
                self, "Update Complete",
                summary + "\n\nRestart PermGuard now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                if restart_permguard():
                    # systemd will relaunch us shortly — quit cleanly
                    from PyQt6.QtWidgets import QApplication
                    QApplication.quit()
                else:
                    QMessageBox.information(
                        self, "Restart",
                        "Close PermGuard and relaunch it to load the new code.")

        worker.line.connect(on_line)
        worker.finished_ok.connect(on_done)
        worker.start()


# ── Network Tab (with Block button) ──────────────────────────────────────────

class _NetworkTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        hdr = QHBoxLayout()
        ico = QLabel("🌐"); ico.setFont(QFont("Noto Color Emoji", 18))
        ttl = QLabel("Network")
        ttl.setFont(QFont("Inter", 15, QFont.Weight.Bold))
        ttl.setStyleSheet(f"color:{C['accent']};")
        sub = QLabel("Active connections — cut network access per app")
        sub.setStyleSheet(f"color:{C['muted']}; font-size:12px;")
        left = QVBoxLayout(); left.setSpacing(2)
        left.addWidget(ttl); left.addWidget(sub)
        hdr.addWidget(ico); hdr.addLayout(left); hdr.addStretch()
        layout.addLayout(hdr)
        layout.addWidget(hsep())

        self._body = QVBoxLayout()
        layout.addLayout(self._body)

        foot = QHBoxLayout()
        foot.addStretch()
        r_btn = QPushButton("⟳  Refresh"); r_btn.setObjectName("flat")
        r_btn.setFixedSize(110, 30)
        r_btn.clicked.connect(self.refresh)
        foot.addWidget(r_btn)
        layout.addLayout(foot)
        self.refresh()

    def refresh(self):
        rows = get_network_conns()
        while self._body.count():
            c = self._body.takeAt(0)
            if c.widget(): c.widget().deleteLater()

        if not rows:
            lbl = QLabel("  No active connections")
            lbl.setStyleSheet(f"color:{C['muted']}; padding:24px;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._body.addWidget(lbl)
            return

        headers = ["PID", "Process", "State", "Local", "Remote", "Action"]
        tbl = QTableWidget(len(rows), len(headers))
        tbl.setHorizontalHeaderLabels(headers)
        tbl.verticalHeader().setVisible(False)
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        tbl.setAlternatingRowColors(True)
        hdr = tbl.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(len(headers)-1, QHeaderView.ResizeMode.Fixed)
        tbl.setColumnWidth(len(headers)-1, 140)
        tbl.verticalHeader().setDefaultSectionSize(40)

        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                tbl.setItem(r, c, QTableWidgetItem(str(val)))
            pid  = str(row[0])
            name = str(row[1])
            blocked = is_blocked(name)
            btn = QPushButton("Unblock" if blocked else "Block Net")
            btn.setObjectName("success" if blocked else "danger")
            btn.setFixedSize(120, 30)
            btn.clicked.connect(lambda _, p=pid, n=name, b=blocked: self._toggle(p, n, b))
            tbl.setCellWidget(r, len(headers)-1, btn)

        self._body.addWidget(tbl)

    def _toggle(self, pid, name, currently_blocked):
        if currently_blocked:
            ok, err = unblock_app(name)
            if not ok:
                QMessageBox.warning(self, "Error", f"Could not unblock:\n{err}")
        else:
            ok, err = block_app(pid, name)
            if not ok:
                QMessageBox.warning(self, "Error",
                    f"Could not block network:\n{err}\n\n"
                    "Tip: iptables requires root. Make sure pkexec is available.")
        self.refresh()


# ── USB Tab (with Enable/Disable toggle) ─────────────────────────────────────

class _USBTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        hdr = QHBoxLayout()
        ico = QLabel("🔌"); ico.setFont(QFont("Noto Color Emoji", 18))
        ttl = QLabel("USB Devices")
        ttl.setFont(QFont("Inter", 15, QFont.Weight.Bold))
        ttl.setStyleSheet(f"color:{C['accent']};")
        sub = QLabel("Enable or disable connected USB devices")
        sub.setStyleSheet(f"color:{C['muted']}; font-size:12px;")
        left = QVBoxLayout(); left.setSpacing(2)
        left.addWidget(ttl); left.addWidget(sub)
        hdr.addWidget(ico); hdr.addLayout(left); hdr.addStretch()

        lockdown_btn = QPushButton("⚠  Disable All USB")
        lockdown_btn.setObjectName("danger")
        lockdown_btn.setFixedSize(180, 32)
        lockdown_btn.clicked.connect(self._lockdown)
        hdr.addWidget(lockdown_btn)
        layout.addLayout(hdr)
        layout.addWidget(hsep())

        self._body = QVBoxLayout()
        layout.addLayout(self._body)

        foot = QHBoxLayout()
        foot.addStretch()
        r_btn = QPushButton("⟳  Refresh"); r_btn.setObjectName("flat")
        r_btn.setFixedSize(110, 30)
        r_btn.clicked.connect(self.refresh)
        foot.addWidget(r_btn)
        layout.addLayout(foot)
        self.refresh()

    def refresh(self):
        ports = get_usb_ports()
        while self._body.count():
            c = self._body.takeAt(0)
            if c.widget(): c.widget().deleteLater()

        if not ports:
            lbl = QLabel("  No USB devices found")
            lbl.setStyleSheet(f"color:{C['muted']}; padding:24px;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._body.addWidget(lbl)
            return

        headers = ["ID", "Vendor:Product", "Device", "Speed", "Status", "Action"]
        tbl = QTableWidget(len(ports), len(headers))
        tbl.setHorizontalHeaderLabels(headers)
        tbl.verticalHeader().setVisible(False)
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        tbl.setAlternatingRowColors(True)
        hdr = tbl.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(len(headers)-1, QHeaderView.ResizeMode.Fixed)
        tbl.setColumnWidth(len(headers)-1, 120)
        tbl.verticalHeader().setDefaultSectionSize(40)

        for r, p in enumerate(ports):
            authorized = p["authorized"]
            vals = [p["id"], f"{p['vendor_id']}:{p['product_id']}",
                    p["product"], p["speed"],
                    "✓ Enabled" if authorized else "✗ Disabled"]
            for c, val in enumerate(vals):
                item = QTableWidgetItem(val)
                if not authorized:
                    item.setForeground(QColor(C["danger"]))
                tbl.setItem(r, c, item)

            btn = QPushButton("Enable" if not authorized else "Disable")
            btn.setObjectName("success" if not authorized else "danger")
            btn.setFixedSize(100, 30)
            dev_id = p["id"]
            btn.clicked.connect(lambda _, d=dev_id, a=authorized: self._toggle(d, a))
            tbl.setCellWidget(r, len(headers)-1, btn)

        self._body.addWidget(tbl)

    def _toggle(self, device_id: str, currently_authorized: bool):
        action = "disable" if currently_authorized else "enable"
        reply = QMessageBox.question(self, f"{action.title()} USB Device",
            f"Are you sure you want to {action} device {device_id}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        ok, err = set_authorized(device_id, not currently_authorized)
        if not ok:
            QMessageBox.warning(self, "Error", f"Could not {action} device:\n{err}")
        self.refresh()

    def _lockdown(self):
        reply = QMessageBox.question(self, "USB Lockdown",
            "Disable ALL connected USB devices?\n\nThis will cut off mice, keyboards, and drives.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        ok, err = disable_all_usb()
        if not ok:
            QMessageBox.warning(self, "Error", f"Some devices could not be disabled:\n{err}")
        self.refresh()


# ── Firewall Tab ──────────────────────────────────────────────────────────────

class _FirewallTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        hdr = QHBoxLayout()
        ico = QLabel("🔥"); ico.setFont(QFont("Noto Color Emoji", 18))
        ttl = QLabel("Firewall Rules")
        ttl.setFont(QFont("Inter", 15, QFont.Weight.Bold))
        ttl.setStyleSheet(f"color:{C['accent']};")
        sub = QLabel("Apps with network access blocked by PermGuard")
        sub.setStyleSheet(f"color:{C['muted']}; font-size:12px;")
        left = QVBoxLayout(); left.setSpacing(2)
        left.addWidget(ttl); left.addWidget(sub)
        hdr.addWidget(ico); hdr.addLayout(left); hdr.addStretch()

        clr_btn = QPushButton("Clear All Rules")
        clr_btn.setObjectName("danger")
        clr_btn.setMinimumWidth(170)
        clr_btn.clicked.connect(self._clear_all)
        hdr.addWidget(clr_btn)
        layout.addLayout(hdr)

        # iptables availability warning
        if not iptables_available():
            warn = QLabel("⚠  iptables not available or insufficient permissions. "
                          "Network blocking requires pkexec + iptables.")
            warn.setStyleSheet(
                f"background:{C['warning']}22; color:{C['warning']};"
                f"border:1px solid {C['warning']}44; border-radius:6px; padding:8px;")
            warn.setWordWrap(True)
            layout.addWidget(warn)

        layout.addWidget(hsep())

        self._body = QVBoxLayout()
        layout.addLayout(self._body)

        foot = QHBoxLayout()
        foot.addStretch()
        r_btn = QPushButton("⟳  Refresh"); r_btn.setObjectName("flat")
        r_btn.clicked.connect(self.refresh)
        foot.addWidget(r_btn)
        layout.addLayout(foot)
        self.refresh()

    def refresh(self):
        rules = get_blocked_apps()
        while self._body.count():
            c = self._body.takeAt(0)
            if c.widget(): c.widget().deleteLater()

        if not rules:
            lbl = QLabel("  No active firewall rules.\n"
                         "  Use the Network tab to block an app.")
            lbl.setStyleSheet(f"color:{C['muted']}; padding:24px;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._body.addWidget(lbl)
            return

        tbl = QTableWidget(len(rules), 4)
        tbl.setHorizontalHeaderLabels(["App", "UID", "Last PID", "Action"])
        tbl.verticalHeader().setVisible(False)
        tbl.setAlternatingRowColors(True)
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        hdr = tbl.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        tbl.setColumnWidth(3, 130)

        for r, rule in enumerate(rules):
            tbl.setItem(r, 0, QTableWidgetItem(rule["name"]))
            tbl.setItem(r, 1, QTableWidgetItem(str(rule.get("uid", "?"))))
            tbl.setItem(r, 2, QTableWidgetItem(str(rule.get("pid", "?"))))
            btn = QPushButton("Unblock")
            btn.setObjectName("success")
            btn.setMinimumWidth(110)
            btn.setMinimumHeight(30)
            name = rule["name"]
            btn.clicked.connect(lambda _, n=name: self._unblock(n))
            tbl.setCellWidget(r, 3, btn)

        self._body.addWidget(tbl)

    def _unblock(self, name: str):
        ok, err = unblock_app(name)
        if not ok:
            QMessageBox.warning(self, "Error", f"Could not unblock:\n{err}")
        self.refresh()

    def _clear_all(self):
        reply = QMessageBox.question(self, "Clear All Rules",
            "Remove all network blocks?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            ok, err = clear_all_blocks()
            if not ok:
                QMessageBox.warning(self, "Error", f"Some rules could not be removed:\n{err}")
            self.refresh()


# ── File Access Tab ───────────────────────────────────────────────────────────

class _FileAccessTab(QWidget):
    """
    Shows protected directories and lets the user add/remove paths.
    Also displays recent file-access events from the log.
    """
    paths_changed = pyqtSignal(list)

    def __init__(self, db: PermissionDB):
        super().__init__()
        self.db = db
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)

        # Header
        hdr = QHBoxLayout()
        hdr.setSpacing(14)
        ico = QLabel("📂"); ico.setFont(QFont("Noto Color Emoji", 20)); ico.setFixedWidth(32)
        ttl = QLabel("File Access Control")
        ttl.setFont(QFont("Inter", 15, QFont.Weight.Bold))
        ttl.setStyleSheet(f"color:{C['text']};")
        sub = QLabel("Protected directories — apps need permission to read these")
        sub.setStyleSheet(f"color:{C['muted']}; font-size:12px;")
        left = QVBoxLayout(); left.setSpacing(2)
        left.addWidget(ttl); left.addWidget(sub)
        hdr.addWidget(ico); hdr.addLayout(left); hdr.addStretch()
        layout.addLayout(hdr)
        layout.addWidget(hsep())

        # Protected paths list
        paths_title = QLabel("Protected Paths")
        paths_title.setFont(QFont("Inter", 12, QFont.Weight.Bold))
        paths_title.setStyleSheet(f"color:{C['text']};")
        layout.addWidget(paths_title)

        self._paths_body = QVBoxLayout()
        self._paths_body.setSpacing(6)
        layout.addLayout(self._paths_body)

        add_row = QHBoxLayout()
        self._path_input = QLineEdit()
        self._path_input.setPlaceholderText("Add path, e.g. /home/user/Documents")
        self._path_input.setStyleSheet(
            f"background:{C['surface']}; color:{C['text']}; border:1px solid {C['border']};"
            f"border-radius:6px; padding:6px 10px; font-size:13px;")
        add_btn = QPushButton("+ Add")
        add_btn.setObjectName("success")
        add_btn.setMinimumWidth(90)
        add_btn.clicked.connect(self._add_path)
        self._path_input.returnPressed.connect(self._add_path)
        add_row.addWidget(self._path_input)
        add_row.addWidget(add_btn)
        layout.addLayout(add_row)

        layout.addWidget(hsep())

        # Recent events from log
        log_title = QLabel("Recent File-Access Events")
        log_title.setFont(QFont("Inter", 12, QFont.Weight.Bold))
        log_title.setStyleSheet(f"color:{C['text']};")
        layout.addWidget(log_title)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setStyleSheet(
            f"background:{C['surface']}; color:{C['muted']}; border:1px solid {C['border']};"
            f"border-radius:6px; font-family:'JetBrains Mono',monospace; font-size:11px;")
        self._log.setFixedHeight(180)
        layout.addWidget(self._log)

        foot = QHBoxLayout()
        foot.addStretch()
        r_btn = QPushButton("↻  Refresh"); r_btn.setObjectName("flat")
        r_btn.clicked.connect(self.refresh)
        foot.addWidget(r_btn)
        layout.addLayout(foot)

        self._load_paths()
        self.refresh()

    def _load_paths(self):
        saved = self.db._db.get("__protected_paths__", {}).get("paths", DEFAULT_SENSITIVE)
        self._paths = list(saved)
        self._rebuild_paths_ui()

    def _save_paths(self):
        if "__protected_paths__" not in self.db._db:
            self.db._db["__protected_paths__"] = {}
        self.db._db["__protected_paths__"]["paths"] = self._paths
        self.db.save()

    def get_paths(self) -> list[str]:
        return list(self._paths)

    def _rebuild_paths_ui(self):
        while self._paths_body.count():
            c = self._paths_body.takeAt(0)
            if c.widget(): c.widget().deleteLater()

        for path in self._paths:
            row = QHBoxLayout()
            lbl = QLabel(path)
            lbl.setStyleSheet(
                f"background:{C['surface']}; color:{C['text']}; border:1px solid {C['border']};"
                f"border-radius:6px; padding:5px 10px; font-size:12px;")
            rm_btn = QPushButton("✕")
            rm_btn.setObjectName("flat")
            rm_btn.setFixedWidth(30)
            rm_btn.setToolTip("Remove this path")
            rm_btn.clicked.connect(lambda _, p=path: self._remove_path(p))
            row.addWidget(lbl, 1)
            row.addWidget(rm_btn)
            w = QWidget(); w.setLayout(row)
            self._paths_body.addWidget(w)

        if not self._paths:
            lbl = QLabel("No protected paths — add one below")
            lbl.setStyleSheet(f"color:{C['muted']}; font-size:12px; padding:4px;")
            self._paths_body.addWidget(lbl)

    def _add_path(self):
        path = self._path_input.text().strip()
        if not path or path in self._paths:
            return
        # Expand ~ 
        from pathlib import Path as _P
        path = str(_P(path).expanduser())
        self._paths.append(path)
        self._save_paths()
        self._rebuild_paths_ui()
        self._path_input.clear()
        # Notify monitor via parent signal (handled in main_window)
        self.paths_changed.emit(self._paths)

    def _remove_path(self, path: str):
        if path in self._paths:
            self._paths.remove(path)
            self._save_paths()
            self._rebuild_paths_ui()
            self.paths_changed.emit(self._paths)

    def refresh(self):
        lines = [l for l in self.db.get_log(200) if "filesystem" in l or "package" in l or "File" in l or "frozen" in l.lower()]
        self._log.setPlainText("\n".join(lines) if lines else "No file-access events yet.")
