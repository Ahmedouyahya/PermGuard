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
from PyQt6.QtGui   import QFont, QIcon, QAction, QColor

from .styles      import C, MAIN_STYLE
from .widgets     import PermTab, StatCard, hsep, build_table
from .permission_dialog import PermissionDialog, DECISION_ALLOW, DECISION_ONCE, DECISION_DENY
from ..core.data  import (get_camera_users, get_mic_users, get_screen_share,
                           get_network_conns, get_open_ports, get_usb_devices, get_top_procs)
from ..core.system import (camera_is_blocked, set_camera_blocked,
                            mic_is_suspended, set_mic_suspended, kill_pid)
from ..core.permissions import PermissionDB, ALLOW, DENY, ASK, LOG_FILE
from ..core.firewall   import (block_app, unblock_app, is_blocked,
                                get_blocked_apps, clear_all_blocks,
                                iptables_available, restore_rules_on_startup)
from ..core.usb_control import get_usb_ports, set_authorized, disable_all_usb

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

        self.setWindowTitle("PermGuard — Privacy Manager")
        self.setWindowIcon(self._app_icon)
        self.setMinimumSize(1100, 660)
        self.resize(1280, 740)
        self.setStyleSheet(MAIN_STYLE)

        self._build_ui()
        self._setup_tray()

        # Auto-refresh timer
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._auto_refresh)
        self._timer.start(5000)

        # Signal handlers registered in main() before Qt starts, but keep here as fallback
        try:
            signal.signal(signal.SIGTERM, lambda *_: self._quit())
            signal.signal(signal.SIGINT,  lambda *_: self._quit())
        except (OSError, ValueError):
            pass  # not in main thread or already registered

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
        self._tabs.addTab(self._perm_tab, "🔑  Permissions")
        self._tabs.addTab(self._sett_tab, "⚙  Settings")

        self._dash.switch_tab.connect(self._tabs.setCurrentIndex)
        self._sett_tab.interval_changed.connect(
            lambda s: self._timer.setInterval(s * 1000))

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
                self._proc_tab, self._fw_tab, self._perm_tab, self._sett_tab]
        if 0 < idx < len(live) and live[idx]:
            live[idx].refresh()

        now = datetime.datetime.now().strftime("%H:%M:%S")
        self._status_lbl.setText(f"Updated {now}")
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
        title.setStyleSheet(f"color:{C['text']};")
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
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)

        # Header
        hdr = QHBoxLayout()
        ico = QLabel("🔑")
        ico.setFont(QFont("Noto Color Emoji", 20))
        ico.setFixedWidth(32)
        ttl = QLabel("App Permissions")
        ttl.setFont(QFont("Inter", 15, QFont.Weight.Bold))
        ttl.setStyleSheet(f"color:{C['text']};")
        sub = QLabel("Saved allow/deny rules per app — set manually or auto-saved from dialogs")
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
        add_btn.setFixedWidth(110)
        add_btn.clicked.connect(self._add_rule_dialog)
        hdr.addWidget(add_btn)
        layout.addLayout(hdr)
        layout.addWidget(hsep())

        self._body = QVBoxLayout()
        self._body.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self._body)

        foot = QHBoxLayout()
        foot.addStretch()
        clr_btn = QPushButton("Reset All Rules")
        clr_btn.setObjectName("danger")
        clr_btn.clicked.connect(self._reset_all)
        r_btn = QPushButton("↻  Refresh")
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
            from .widgets import _EmptyState
            self._body.addWidget(_EmptyState(
                "No saved rules yet — dialogs will appear when apps request access",
                icon="🔑"
            ))
            return

        ncols = 4
        tbl = QTableWidget(len(rules), ncols)
        tbl.setHorizontalHeaderLabels(["App", "Resource", "Decision", ""])
        tbl.verticalHeader().setVisible(False)
        tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.setAlternatingRowColors(True)
        tbl.setShowGrid(False)
        tbl.verticalHeader().setDefaultSectionSize(38)
        hdr = tbl.horizontalHeader()
        hdr.setHighlightSections(False)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        tbl.setColumnWidth(3, 90)

        for r, (app, res, decision) in enumerate(rules):
            tbl.setItem(r, 0, QTableWidgetItem(app))
            tbl.setItem(r, 1, QTableWidgetItem(res.capitalize()))
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
        res_combo.addItems(["camera", "microphone", "screen"])
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
        tbl.setColumnWidth(len(headers)-1, 120)

        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                tbl.setItem(r, c, QTableWidgetItem(str(val)))
            pid  = str(row[0])
            name = str(row[1])
            blocked = is_blocked(name)
            btn = QPushButton("Unblock" if blocked else "Block Net")
            btn.setObjectName("success" if blocked else "danger")
            btn.setFixedWidth(110)
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
        lockdown_btn.setFixedWidth(160)
        lockdown_btn.clicked.connect(self._lockdown)
        hdr.addWidget(lockdown_btn)
        layout.addLayout(hdr)
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
        tbl.setColumnWidth(len(headers)-1, 110)

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
            btn.setFixedWidth(100)
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
        clr_btn.setFixedWidth(140)
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
        tbl.setColumnWidth(3, 100)

        for r, rule in enumerate(rules):
            tbl.setItem(r, 0, QTableWidgetItem(rule["name"]))
            tbl.setItem(r, 1, QTableWidgetItem(str(rule.get("uid", "?"))))
            tbl.setItem(r, 2, QTableWidgetItem(str(rule.get("pid", "?"))))
            btn = QPushButton("Unblock")
            btn.setObjectName("success")
            btn.setFixedWidth(90)
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
