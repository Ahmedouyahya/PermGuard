"""
main.py — PermGuard entry point.
Starts monitors, wires signals, launches the main window.
"""
import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore    import QTimer

from .core.permissions import PermissionDB
from .core.monitor     import CameraMonitor, MicMonitor
from .ui.main_window   import MainWindow


def main():
    # Handle --version / --help before starting Qt
    if len(sys.argv) > 1:
        if sys.argv[1] in ("--version", "-v"):
            from . import __version__
            print(f"PermGuard {__version__}")
            sys.exit(0)
        if sys.argv[1] in ("--help", "-h"):
            print("PermGuard — Android-like privacy manager for Linux/KDE")
            print("Usage: permguard [--version] [--help]")
            sys.exit(0)

    app = QApplication(sys.argv)
    app.setApplicationName("PermGuard")
    app.setApplicationVersion("0.1.0")
    app.setQuitOnLastWindowClosed(False)

    # Keep Python signal handlers alive (for SIGINT/SIGTERM)
    pulse = QTimer()
    pulse.start(500)
    pulse.timeout.connect(lambda: None)

    db  = PermissionDB()

    # Re-apply any firewall rules saved from the previous session
    from .core.firewall import restore_rules_on_startup
    restore_rules_on_startup()

    win = MainWindow(db)

    # Start background monitors
    cam_mon = CameraMonitor()
    mic_mon = MicMonitor()

    cam_mon.new_access.connect(win.handle_access)
    mic_mon.new_access.connect(win.handle_access)

    cam_mon.start()
    mic_mon.start()

    win.show()
    db.log("PermGuard started")

    ret = app.exec()
    cam_mon.stop()
    mic_mon.stop()
    sys.exit(ret)


if __name__ == "__main__":
    main()
