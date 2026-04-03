"""
main.py — PermGuard entry point.
Starts monitors, wires signals, launches the main window.
"""
import sys
from pathlib import Path
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore    import QTimer
from PyQt6.QtGui     import QIcon

from . import __version__
from .core.permissions import PermissionDB
from .core.monitor     import CameraMonitor, MicMonitor, FileMonitor, PackageInstallMonitor
from .ui.main_window   import MainWindow

# Icon lives one level above the package: <install_dir>/assets/icon.svg
_ICON_PATH = Path(__file__).parents[1] / "assets" / "icon.svg"


def _load_icon() -> QIcon:
    if _ICON_PATH.exists():
        icon = QIcon(str(_ICON_PATH))
        if not icon.isNull():
            return icon
    # Fallback to system theme icons
    for name in ("security-high", "dialog-password", "preferences-system-privacy"):
        icon = QIcon.fromTheme(name)
        if not icon.isNull():
            return icon
    return QIcon()


def main():
    # Handle --version / --help before starting Qt
    if len(sys.argv) > 1:
        if sys.argv[1] in ("--version", "-v"):
            print(f"PermGuard {__version__}")
            sys.exit(0)
        if sys.argv[1] in ("--help", "-h"):
            print("PermGuard — Android-like privacy manager for Linux")
            print("Usage: permguard [--version] [--help]")
            sys.exit(0)

    app = QApplication(sys.argv)
    app.setApplicationName("PermGuard")
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(False)

    icon = _load_icon()
    app.setWindowIcon(icon)

    # Keep Python signal handlers alive (for SIGINT/SIGTERM)
    pulse = QTimer()
    pulse.start(500)
    pulse.timeout.connect(lambda: None)

    db = PermissionDB()

    # Re-apply persisted rules from previous session
    from .core.firewall import restore_rules_on_startup
    from .core.system   import restore_device_state
    restore_rules_on_startup()   # iptables network blocks
    restore_device_state()       # camera chmod + mic suspend

    win = MainWindow(db, icon)

    # Start background monitors
    cam_mon = CameraMonitor()
    mic_mon = MicMonitor()
    pkg_mon = PackageInstallMonitor()

    # File monitor loads its protected paths from the saved db state
    saved_paths = db._db.get("__protected_paths__", {}).get("paths", None)
    file_mon = FileMonitor(protected_paths=saved_paths)
    win._file_mon = file_mon   # allow Settings tab to update paths at runtime

    cam_mon.new_access.connect(win.handle_access)
    mic_mon.new_access.connect(win.handle_access)
    file_mon.new_access.connect(win.handle_access)
    pkg_mon.new_access.connect(win.handle_access)

    cam_mon.start()
    mic_mon.start()
    file_mon.start()
    pkg_mon.start()

    win.show()
    db.log(f"PermGuard {__version__} started")

    ret = app.exec()
    cam_mon.stop()
    mic_mon.stop()
    file_mon.stop()
    pkg_mon.stop()
    sys.exit(ret)


if __name__ == "__main__":
    main()
