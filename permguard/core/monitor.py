"""
monitor.py — Background QThread workers that detect new camera/mic access
and emit signals so the main thread can show permission dialogs.
"""
import time
from PyQt6.QtCore import QThread, pyqtSignal
from .data import get_camera_pids, get_mic_streams


class AccessEvent:
    """Carries info about a new resource access attempt."""
    def __init__(self, pid: str, app_name: str, cmdline: str,
                 resource: str, stream_index: str = ""):
        self.pid          = pid
        self.app_name     = app_name
        self.cmdline      = cmdline
        self.resource     = resource          # "camera" | "microphone"
        self.stream_index = stream_index      # for mic streams (pactl kill)

    def __repr__(self):
        return f"<AccessEvent {self.app_name}({self.pid}) → {self.resource}>"


class CameraMonitor(QThread):
    """Polls /dev/video* every second and emits new_access when a new PID appears."""
    new_access  = pyqtSignal(object)   # AccessEvent
    access_gone = pyqtSignal(str)      # pid

    INTERVAL = 1.0  # seconds

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running  = False
        self._known    = set()

    def run(self):
        self._running = True
        while self._running:
            try:
                current = get_camera_pids()
                # New PIDs
                for pid in current - self._known:
                    from .system import proc_name, proc_cmdline
                    evt = AccessEvent(
                        pid=pid,
                        app_name=proc_name(pid),
                        cmdline=proc_cmdline(pid),
                        resource="camera",
                    )
                    self.new_access.emit(evt)
                # Gone PIDs
                for pid in self._known - current:
                    self.access_gone.emit(pid)
                self._known = current
            except Exception:
                pass
            time.sleep(self.INTERVAL)

    def stop(self):
        self._running = False
        self.wait(2000)


class MicMonitor(QThread):
    """Polls PipeWire source-outputs every second and emits new_access for new streams."""
    new_access  = pyqtSignal(object)   # AccessEvent
    access_gone = pyqtSignal(str)      # pid

    INTERVAL = 1.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self._known   = {}   # pid -> stream_index

    def run(self):
        self._running = True
        while self._running:
            try:
                streams = get_mic_streams()
                current = {s["pid"]: s for s in streams}
                # New PIDs
                for pid, s in current.items():
                    if pid not in self._known:
                        evt = AccessEvent(
                            pid=pid,
                            app_name=s["app_name"],
                            cmdline=s["cmdline"],
                            resource="microphone",
                            stream_index=s["stream_index"],
                        )
                        self.new_access.emit(evt)
                # Gone PIDs
                for pid in list(self._known):
                    if pid not in current:
                        self.access_gone.emit(pid)
                self._known = current
            except Exception:
                pass
            time.sleep(self.INTERVAL)

    def stop(self):
        self._running = False
        self.wait(2000)
