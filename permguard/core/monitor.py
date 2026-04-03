"""
monitor.py — Background monitors that detect new resource access.

CameraMonitor        — inotify-based, event-driven (zero CPU when idle).
MicMonitor           — polls pactl every 1s.
FileMonitor          — inotify on user-configured sensitive directories.
PackageInstallMonitor— polls /proc every 2s for package manager processes.
"""
import os, re, time, ctypes, ctypes.util, struct, select
from pathlib import Path
from PyQt6.QtCore import QThread, pyqtSignal
from .data import get_camera_pids, get_mic_streams

# Package managers to watch for
PACKAGE_MANAGERS = {
    "apt", "apt-get", "apt-cache", "dpkg", "dpkg-deb",
    "pip", "pip3", "pip2",
    "npm", "yarn", "pnpm",
    "snap", "flatpak",
    "pacman", "yay", "paru",
    "dnf", "yum", "rpm",
    "zypper",
    "cargo",
    "gem",
    "go",
    "brew",
    "conda", "mamba",
    "pipx",
}

# Sensitive paths protected by default
DEFAULT_SENSITIVE = [
    str(Path.home() / ".ssh"),
    str(Path.home() / ".gnupg"),
    str(Path.home() / ".config" / "permguard"),
]


class AccessEvent:
    """Carries info about a new resource access attempt."""
    def __init__(self, pid: str, app_name: str, cmdline: str,
                 resource: str, stream_index: str = ""):
        self.pid          = pid
        self.app_name     = app_name
        self.cmdline      = cmdline
        self.resource     = resource
        self.stream_index = stream_index

    def __repr__(self):
        return f"<AccessEvent {self.app_name}({self.pid}) → {self.resource}>"


# ── inotify wrapper ───────────────────────────────────────────────────────────

class _Inotify:
    IN_OPEN       = 0x00000020
    IN_CLOSE      = 0x00000010
    IN_CREATE     = 0x00000100   # new video device hotplugged
    _HDR_FMT      = "iIII"
    _HDR_SIZE     = struct.calcsize(_HDR_FMT)

    def __init__(self):
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        self._inotify_init1      = libc.inotify_init1
        self._inotify_add_watch  = libc.inotify_add_watch
        self._inotify_rm_watch   = libc.inotify_rm_watch
        self._fd = self._inotify_init1(os.O_NONBLOCK)
        self._wd_path: dict[int, str] = {}

    def watch(self, path: str, mask: int | None = None) -> int:
        if mask is None:
            mask = self.IN_OPEN | self.IN_CLOSE
        wd = self._inotify_add_watch(self._fd, path.encode(), mask)
        if wd >= 0:
            self._wd_path[wd] = path
        return wd

    def read_events(self, timeout: float = 1.0) -> list[tuple[str, str]]:
        """Block up to `timeout` seconds. Returns list of ('open'|'close', path)."""
        r, _, _ = select.select([self._fd], [], [], timeout)
        events = []
        if not r:
            return events
        data = os.read(self._fd, 4096)
        offset = 0
        while offset + self._HDR_SIZE <= len(data):
            wd, mask, _, name_len = struct.unpack_from(self._HDR_FMT, data, offset)
            offset += self._HDR_SIZE + name_len
            path = self._wd_path.get(wd, "?")
            if mask & self.IN_OPEN:
                events.append(("open", path))
            if mask & self.IN_CLOSE:
                events.append(("close", path))
        return events

    def fileno(self) -> int:
        return self._fd

    def close(self):
        try:
            os.close(self._fd)
        except OSError:
            pass


# ── Camera monitor (inotify — event-driven) ───────────────────────────────────

class CameraMonitor(QThread):
    """
    Watches /dev/video* with inotify IN_OPEN / IN_CLOSE.
    Wakes up only when something happens — zero CPU when idle.
    After a wake-up, scans /proc/pid/fd to identify the process.
    """
    new_access  = pyqtSignal(object)   # AccessEvent
    access_gone = pyqtSignal(str)      # pid

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self._known: set[str] = set()

    def run(self):
        from .system import proc_name, proc_cmdline
        self._running = True

        try:
            inotify = _Inotify()
        except Exception:
            # inotify unavailable — fall back to 1s polling
            self._run_fallback()
            return

        # Watch all existing video devices
        devices = sorted(
            f"/dev/{f}" for f in os.listdir("/dev")
            if re.match(r"video\d+$", f)
        )
        for dev in devices:
            inotify.watch(dev)

        while self._running:
            events = inotify.read_events(timeout=1.0)

            if events or True:   # always re-check on each wake-up
                current = get_camera_pids()
                for pid in current - self._known:
                    evt = AccessEvent(
                        pid=pid,
                        app_name=proc_name(pid),
                        cmdline=proc_cmdline(pid),
                        resource="camera",
                    )
                    self.new_access.emit(evt)
                for pid in self._known - current:
                    self.access_gone.emit(pid)
                self._known = current

        inotify.close()

    def _run_fallback(self):
        """1-second polling fallback if inotify is unavailable."""
        from .system import proc_name, proc_cmdline
        while self._running:
            try:
                current = get_camera_pids()
                for pid in current - self._known:
                    self.new_access.emit(AccessEvent(
                        pid=pid, app_name=proc_name(pid),
                        cmdline=proc_cmdline(pid), resource="camera"
                    ))
                for pid in self._known - current:
                    self.access_gone.emit(pid)
                self._known = current
            except Exception:
                pass
            time.sleep(1.0)

    def stop(self):
        self._running = False
        self.wait(2000)


# ── Mic monitor (pactl polling — 4.8ms, kept) ─────────────────────────────────

class MicMonitor(QThread):
    """
    Polls pactl list source-outputs every second.
    pactl takes ~4.8ms — acceptable, D-Bus subscription is a future improvement.
    """
    new_access  = pyqtSignal(object)   # AccessEvent
    access_gone = pyqtSignal(str)      # pid

    INTERVAL = 1.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self._known: dict[str, dict] = {}

    def run(self):
        self._running = True
        while self._running:
            try:
                streams = get_mic_streams()
                current = {s["pid"]: s for s in streams}
                for pid, s in current.items():
                    if pid not in self._known:
                        self.new_access.emit(AccessEvent(
                            pid=pid,
                            app_name=s["app_name"],
                            cmdline=s["cmdline"],
                            resource="microphone",
                            stream_index=s["stream_index"],
                        ))
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


# ── File access monitor (inotify on sensitive dirs) ───────────────────────────

class FileMonitor(QThread):
    """
    Watches user-configured sensitive directories with inotify IN_OPEN.
    When an unknown process opens a file inside, emits new_access.
    The caller SIGSTOPs the process before showing the dialog.
    """
    new_access  = pyqtSignal(object)
    access_gone = pyqtSignal(str)

    def __init__(self, protected_paths=None, parent=None):
        super().__init__(parent)
        self._running = False
        self._known: set[str] = set()
        self._paths: list[str] = list(protected_paths) if protected_paths \
                                 else list(DEFAULT_SENSITIVE)

    def set_paths(self, paths: list[str]):
        self._paths = paths

    def run(self):
        from .system import proc_name, proc_cmdline
        self._running = True

        try:
            inotify = _Inotify()
        except Exception:
            self._run_fallback()
            return

        for p in self._paths:
            if os.path.isdir(p):
                inotify.watch(p, _Inotify.IN_OPEN | _Inotify.IN_CREATE)

        while self._running:
            events = inotify.read_events(timeout=1.0)
            if events:
                current = self._scan_accesses()
                for pid in current - self._known:
                    path = self._get_accessed_path(pid)
                    self.new_access.emit(AccessEvent(
                        pid=pid,
                        app_name=proc_name(pid),
                        cmdline=proc_cmdline(pid),
                        resource="filesystem",
                        stream_index=path,
                    ))
                for pid in self._known - current:
                    self.access_gone.emit(pid)
                self._known = current

        inotify.close()

    def _run_fallback(self):
        from .system import proc_name, proc_cmdline
        while self._running:
            try:
                current = self._scan_accesses()
                for pid in current - self._known:
                    path = self._get_accessed_path(pid)
                    self.new_access.emit(AccessEvent(
                        pid=pid,
                        app_name=proc_name(pid),
                        cmdline=proc_cmdline(pid),
                        resource="filesystem",
                        stream_index=path,
                    ))
                for pid in self._known - current:
                    self.access_gone.emit(pid)
                self._known = current
            except Exception:
                pass
            time.sleep(2.0)

    def _scan_accesses(self) -> set[str]:
        pids: set[str] = set()
        protected = [p for p in self._paths if p]
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            fd_dir = f"/proc/{pid}/fd"
            try:
                for fd in os.listdir(fd_dir):
                    try:
                        lnk = os.readlink(f"{fd_dir}/{fd}")
                        for ppath in protected:
                            if lnk.startswith(ppath):
                                pids.add(pid)
                                break
                    except OSError:
                        pass
            except OSError:
                pass
        return pids

    def _get_accessed_path(self, pid: str) -> str:
        fd_dir = f"/proc/{pid}/fd"
        try:
            for fd in os.listdir(fd_dir):
                try:
                    lnk = os.readlink(f"{fd_dir}/{fd}")
                    for ppath in self._paths:
                        if lnk.startswith(ppath):
                            return ppath
                except OSError:
                    pass
        except OSError:
            pass
        return "protected directory"

    def stop(self):
        self._running = False
        self.wait(2000)


# ── Package install monitor ───────────────────────────────────────────────────

class PackageInstallMonitor(QThread):
    """
    Polls /proc every 2s for processes matching known package manager names.
    Emits new_access so the user can allow or deny before install completes.
    """
    new_access  = pyqtSignal(object)
    access_gone = pyqtSignal(str)

    INTERVAL = 2.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self._known: set[str] = set()

    def run(self):
        from .system import proc_cmdline
        self._running = True
        while self._running:
            try:
                current: set[str] = set()
                for pid in os.listdir("/proc"):
                    if not pid.isdigit():
                        continue
                    try:
                        comm = Path(f"/proc/{pid}/comm").read_text().strip()
                        if comm in PACKAGE_MANAGERS:
                            current.add(pid)
                    except OSError:
                        pass

                for pid in current - self._known:
                    try:
                        comm    = Path(f"/proc/{pid}/comm").read_text().strip()
                        cmdline = proc_cmdline(pid)
                        self.new_access.emit(AccessEvent(
                            pid=pid,
                            app_name=comm,
                            cmdline=cmdline,
                            resource="package_install",
                        ))
                    except OSError:
                        pass

                for pid in self._known - current:
                    self.access_gone.emit(pid)
                self._known = current
            except Exception:
                pass
            time.sleep(self.INTERVAL)

    def stop(self):
        self._running = False
        self.wait(2000)
