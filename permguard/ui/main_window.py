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
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame
)
from PyQt6.QtCore  import Qt, QTimer, pyqtSignal
from PyQt6.QtGui   import QFont, QIcon, QAction, QColor

from .styles      import C, MAIN_STYLE
from .widgets     import PermTab, StatCard, hsep, build_table
from .permission_dialog import PermissionDialog, DECISION_ALLOW, DECISION_ONCE, DECISION_DENY
from ..core.data  import (get_camera_users, get_mic_users, get_screen_share,
                           get_network_conns, get_open_ports, get_usb_devices, get_top_procs)
from ..core.system import (camera_is_blocked, set_camera_blocked,
                            mic_is_suspended, set_mic_suspended, kill_pid)
from ..core.permissions import PermissionDB, ALLOW, DENY, ASK, LOG_FILE

AUTOSTART_DIR  = Path.home() / ".config/autostart"
AUTOSTART_FILE = AUTOSTART_DIR / "permguard.desktop"


class MainWindow(QMainWindow):
    def __init__(self, db: PermissionDB, parent=None):
        super().__init__(parent)
        self.db           = db
        self._dialog_queue = deque()   # queued AccessEvents waiting for dialog
        self._active_dialog = None
        self._prev_cam    = set()
        self._prev_mic    = set()

        self.setWindowTitle("PermGuard — Privacy Manager")
        self.setMinimumSize(960, 620)
        self.resize(1080, 680)
        self.setStyleSheet(MAIN_STYLE)

        self._build_ui()
        self._setup_tray()

        # Auto-refresh timer
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._auto_refresh)
        self._timer.start(5000)

        signal.signal(signal.SIGTERM, lambda *_: self._quit())
        signal.signal(signal.SIGINT,  lambda *_: self._quit())

    # ── Permission handling (called by monitors) ──────────────────────────────

    def handle_access(self, evt):
        """Called when a monitor detects a new access attempt."""
        decision = self.db.get(evt.app_name, evt.resource)
        if decision == ALLOW:
            self.db.log(f"Auto-allowed: {evt.app_name} → {evt.resource} (PID {evt.pid})")
            return
        if decision == DENY:
            self.db.log(f"Auto-denied: {evt.app_name} → {evt.resource} (PID {evt.pid})")
            self._enforce_deny(evt)
            return
        # ASK — queue a dialog
        self._dialog_queue.append(evt)
        if self._active_dialog is None:
            self._show_next_dialog()

    def _show_next_dialog(self):
        if not self._dialog_queue:
            self._active_dialog = None
            return
        evt = self._dialog_queue.popleft()
        dlg = PermissionDialog(
            app_name=evt.app_name,
            pid=evt.pid,
            resource=evt.resource,
            cmdline=evt.cmdline,
        )
        self._active_dialog = dlg

        def on_decision(decision: str, remember: bool):
            self.db.log(
                f"User decision: {evt.app_name} → {evt.resource} = {decision}"
                f" (remember={remember}, PID {evt.pid})"
            )
            if remember and decision in (ALLOW, DENY):
                self.db.set(evt.app_name, evt.resource, decision)
                self._perm_tab.refresh()
            if decision == DENY:
                self._enforce_deny(evt)
            self._active_dialog = None
            self._show_next_dialog()   # show next queued dialog
            # tray notification
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
        bar.setFixedHeight(50)
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(20, 0, 20, 0)
        logo = QLabel("🛡  PermGuard")
        logo.setFont(QFont("Inter", 14, QFont.Weight.Bold))
        logo.setStyleSheet(f"color:{C['accent']}; background:transparent;")
        self._status_lbl = QLabel("Monitoring…")
        self._status_lbl.setStyleSheet(f"color:{C['muted']}; font-size:12px; background:transparent;")
        bl.addWidget(logo)
        bl.addStretch()
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
        self._net_tab  = PermTab("🌐", "Network",      "Active connections per process",
                                  get_network_conns,["PID","Process","State","Local","Remote"])
        self._usb_tab  = PermTab("🔌", "USB Devices",  "Currently connected USB devices",
                                  get_usb_devices,  ["Bus","Device","ID","Description"])
        self._port_tab = PermTab("🔒", "Open Ports",   "Listening ports on this machine",
                                  get_open_ports,   ["Proto","Address","Process","PID"])
        self._proc_tab = _ProcessTab()
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
        self._tabs.addTab(self._perm_tab, "🔑  Permissions")
        self._tabs.addTab(self._sett_tab, "⚙  Settings")

        self._dash.switch_tab.connect(self._tabs.setCurrentIndex)
        self._sett_tab.interval_changed.connect(
            lambda s: self._timer.setInterval(s * 1000))

        root.addWidget(self._tabs)

    def _setup_tray(self):
        self._tray = QSystemTrayIcon(self)
        self._tray.setIcon(QIcon.fromTheme("security-high",
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
        if self._tray_ok:
            self._tray.show()

    # ── Auto-refresh ──────────────────────────────────────────────────────────

    def _auto_refresh(self):
        cam  = get_camera_users()
        mic  = get_mic_users()
        scr  = get_screen_share()
        net  = get_network_conns()
        usb  = get_usb_devices()
        port = get_open_ports()
        self._dash.refresh(cam, mic, scr, net, usb, port)

        idx = self._tabs.currentIndex()
        live = [None, self._cam_tab, self._mic_tab, self._scr_tab,
                self._net_tab, self._usb_tab, self._port_tab,
                self._proc_tab, self._perm_tab, self._sett_tab]
        if 0 < idx < len(live) and live[idx]:
            live[idx].refresh()

        now = datetime.datetime.now().strftime("%H:%M:%S")
        self._status_lbl.setText(f"Last refresh: {now}")

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

    def _quit(self):
        self.db.log("PermGuard closed by user")
        from PyQt6.QtWidgets import QApplication
        QApplication.quit()


# ── Dashboard Tab ─────────────────────────────────────────────────────────────

class _DashboardTab(QWidget):
    switch_tab = pyqtSignal(int)

    def __init__(self, db: PermissionDB):
        super().__init__()
        self.db = db
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        title = QLabel("Privacy Overview")
        title.setFont(QFont("Inter", 16, QFont.Weight.Bold))
        title.setStyleSheet(f"color:{C['accent']};")
        layout.addWidget(title)
        layout.addWidget(hsep())

        # Stat cards
        from PyQt6.QtWidgets import QGridLayout
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

        # Block toggles
        layout.addWidget(hsep())
        blk_title = QLabel("Quick Blocks")
        blk_title.setFont(QFont("Inter", 13, QFont.Weight.Bold))
        layout.addWidget(blk_title)

        blk_row = QHBoxLayout()
        self._cam_btn = QPushButton()
        self._mic_btn = QPushButton()
        self._cam_btn.setFixedWidth(220)
        self._mic_btn.setFixedWidth(220)
        self._cam_btn.clicked.connect(self._toggle_cam)
        self._mic_btn.clicked.connect(self._toggle_mic)
        blk_row.addWidget(self._cam_btn)
        blk_row.addWidget(self._mic_btn)
        blk_row.addStretch()
        layout.addLayout(blk_row)
        self._update_block_btns()

        layout.addStretch()
        note = QLabel("Cards auto-refresh every 5s  ·  Click a card to jump to its tab")
        note.setStyleSheet(f"color:{C['muted']}; font-size:11px;")
        layout.addWidget(note)

    def refresh(self, cam, mic, scr, net, usb, ports):
        self._cards["camera"].update(len(cam))
        self._cards["mic"].update(len(mic))
        self._cards["screen"].update(len(scr))
        self._cards["network"].update(len(net))
        self._cards["usb"].update(len(usb))
        self._cards["ports"].update(len(ports))
        self._update_block_btns()

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


# ── Permissions Management Tab ────────────────────────────────────────────────

class _PermissionsTab(QWidget):
    def __init__(self, db: PermissionDB):
        super().__init__()
        self.db = db
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        hdr = QHBoxLayout()
        ttl = QLabel("App Permissions")
        ttl.setFont(QFont("Inter", 15, QFont.Weight.Bold))
        ttl.setStyleSheet(f"color:{C['accent']};")
        sub = QLabel("Saved decisions — edit or revoke any rule")
        sub.setStyleSheet(f"color:{C['muted']}; font-size:12px;")
        left = QVBoxLayout()
        left.setSpacing(2)
        left.addWidget(ttl)
        left.addWidget(sub)
        ico = QLabel("🔑")
        ico.setFont(QFont("Noto Color Emoji", 18))
        hdr.addWidget(ico)
        hdr.addLayout(left)
        hdr.addStretch()
        layout.addLayout(hdr)
        layout.addWidget(hsep())

        self._body = QVBoxLayout()
        layout.addLayout(self._body)

        foot = QHBoxLayout()
        foot.addStretch()
        clr_btn = QPushButton("Reset All Rules")
        clr_btn.setObjectName("danger")
        clr_btn.clicked.connect(self._reset_all)
        r_btn = QPushButton("⟳  Refresh")
        r_btn.setObjectName("flat")
        r_btn.clicked.connect(self.refresh)
        foot.addWidget(clr_btn)
        foot.addWidget(r_btn)
        layout.addLayout(foot)

        self.refresh()

    def refresh(self):
        while self._body.count():
            c = self._body.takeAt(0)
            if c.widget():
                c.widget().deleteLater()

        rules = self.db.all_rules()
        if not rules:
            lbl = QLabel("  No saved rules yet.\n  Permission dialogs will appear when apps request access.")
            lbl.setStyleSheet(f"color:{C['muted']}; padding:24px;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._body.addWidget(lbl)
            return

        ncols = 4
        tbl = QTableWidget(len(rules), ncols)
        tbl.setHorizontalHeaderLabels(["App", "Resource", "Decision", "Action"])
        tbl.verticalHeader().setVisible(False)
        tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.setAlternatingRowColors(True)
        hdr = tbl.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        tbl.setColumnWidth(3, 90)

        for r, (app, res, decision) in enumerate(rules):
            tbl.setItem(r, 0, QTableWidgetItem(app))
            tbl.setItem(r, 1, QTableWidgetItem(res))
            d_item = QTableWidgetItem(decision.upper())
            color = C["success"] if decision == "allow" else C["danger"]
            d_item.setForeground(QColor(color))
            tbl.setItem(r, 2, d_item)
            btn = QPushButton("Revoke")
            btn.setObjectName("flat")
            btn.setFixedWidth(80)
            btn.clicked.connect(lambda _, a=app, res_=res: self._revoke(a, res_))
            tbl.setCellWidget(r, 3, btn)

        self._body.addWidget(tbl)

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
    interval_changed = pyqtSignal(int)

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
        self._interval.setValue(5)
        self._interval.setSuffix(" seconds")
        self._interval.valueChanged.connect(self.interval_changed.emit)
        ir.addWidget(self._interval)
        ir.addStretch()
        gl.addLayout(ir)
        layout.addWidget(gen)

        # Notifications
        notif = QGroupBox("NOTIFICATIONS")
        nl = QVBoxLayout(notif)
        self._notif_cam = QCheckBox("Show dialog when camera access starts")
        self._notif_mic = QCheckBox("Show dialog when microphone access starts")
        self._notif_cam.setChecked(True)
        self._notif_mic.setChecked(True)
        nl.addWidget(self._notif_cam)
        nl.addWidget(self._notif_mic)
        layout.addWidget(notif)

        # Flatpak
        fp = QGroupBox("FLATPAK APP SANDBOX")
        fpl = QVBoxLayout(fp)
        fpl.addWidget(QLabel("Manage Flatpak app permissions (camera, mic, filesystem…)"))
        fsb = QPushButton("Open Flatseal")
        fsb.setFixedWidth(150)
        fsb.clicked.connect(self._open_flatseal)
        fpl.addWidget(fsb)
        layout.addWidget(fp)

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
            script = Path(__file__).resolve().parents[2] / "permguard" / "main.py"
            AUTOSTART_FILE.write_text(
                f"[Desktop Entry]\nName=PermGuard\n"
                f"Exec=python3 {script}\nType=Application\n"
                f"X-KDE-autostart-after=panel\n"
            )
        else:
            AUTOSTART_FILE.unlink(missing_ok=True)

    def _open_flatseal(self):
        try:
            subprocess.Popen(["flatpak", "run", "com.github.tchx84.Flatseal"])
        except Exception:
            QMessageBox.information(self, "Flatseal",
                "Install with:\n\nflatpak install flathub com.github.tchx84.Flatseal")
