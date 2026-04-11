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
    "apt", "apt-get", "dpkg", "dpkg-deb",
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

# Read-only subcommands/words that don't modify the system — skip these
_READ_ONLY_SUBCMDS = {
    "list", "show", "search", "info", "status", "policy",
    "depends", "rdepends", "madison", "changelog",
    "check", "verify", "audit", "doctor", "outdated",
    "help", "--help", "-h", "--version", "-V",
}

# Per-tool read-only flag mappings (short flags differ between tools)
_READ_ONLY_FLAGS: dict[str, set[str]] = {
    "dpkg":     {"-l", "--list", "-s", "--status", "-p", "--print-avail",
                 "-L", "--listfiles", "-S", "--search", "-W", "--show",
                 "-C", "--audit"},
    "rpm":      {"-q", "--query", "-V", "--verify"},
}

# Commands that are always read-only by name
_READ_ONLY_COMMANDS = {"apt-cache"}


def _is_read_only_invocation(comm: str, cmdline: str) -> bool:
    """Return True if this package manager invocation is a read-only query."""
    if comm in _READ_ONLY_COMMANDS:
        return True
    parts = cmdline.split()
    if len(parts) < 2:
        return False
    tool_flags = _READ_ONLY_FLAGS.get(comm, set())
    # Build set of short single-letter read-only flags for this tool,
    # so combined forms like `dpkg -li` or `rpm -qa` still match.
    short_letters = {f[1] for f in tool_flags
                     if len(f) == 2 and f.startswith("-") and f[1].isalpha()}
    for arg in parts[1:]:
        if arg.startswith("--"):
            if arg in _READ_ONLY_SUBCMDS or arg in tool_flags:
                return True
            continue
        if arg.startswith("-") and len(arg) > 1:
            if arg in _READ_ONLY_SUBCMDS or arg in tool_flags:
                return True
            # Combined short flags like `rpm -qa` (query-all), `rpm -ql`
            # (query-list). rpm/dpkg convention: the FIRST letter is the
            # operation, the rest are modifiers. If the operation letter
            # is a known read-only op, treat the whole combo as read-only.
            first = arg[1]
            if first.isalpha() and first in short_letters:
                return True
            continue
        # First positional argument = subcommand
        return arg in _READ_ONLY_SUBCMDS
    return False

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

        # Initial scan to catch processes that opened camera before we started
        self._known = get_camera_pids()

        poll_counter = 0
        while self._running:
            events = inotify.read_events(timeout=1.0)
            poll_counter += 1

            # Re-scan on inotify events, or every 5s as a safety net
            if events or poll_counter >= 5:
                poll_counter = 0
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
        # Pipe used to wake the inotify loop when paths change at runtime
        self._wake_r, self._wake_w = os.pipe()
        os.set_blocking(self._wake_r, False)

    def set_paths(self, paths: list[str]):
        self._paths = paths
        # Wake the inotify select() so it re-initializes watches
        try:
            os.write(self._wake_w, b"\x01")
        except OSError:
            pass

    def _setup_watches(self, inotify: _Inotify):
        """(Re)create inotify watches for the current path list."""
        # Remove old watches
        for wd in list(inotify._wd_path):
            try:
                inotify._inotify_rm_watch(inotify._fd, wd)
            except Exception:
                pass
        inotify._wd_path.clear()
        # Add new watches
        for p in self._paths:
            if os.path.isdir(p):
                inotify.watch(p, _Inotify.IN_OPEN | _Inotify.IN_CREATE)

    def run(self):
        from .system import proc_name, proc_cmdline
        self._running = True

        try:
            inotify = _Inotify()
        except Exception:
            self._run_fallback()
            return

        self._setup_watches(inotify)

        while self._running:
            # Wait on both inotify fd and wake pipe
            r, _, _ = select.select([inotify._fd, self._wake_r], [], [], 1.0)

            if self._wake_r in r:
                # Drain the wake pipe and re-setup watches
                try:
                    os.read(self._wake_r, 256)
                except OSError:
                    pass
                self._setup_watches(inotify)
                continue

            if inotify._fd in r:
                # Read and discard event details — we scan /proc anyway
                try:
                    os.read(inotify._fd, 4096)
                except OSError:
                    pass
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
        os.close(self._wake_r)
        os.close(self._wake_w)

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
        own_pid = str(os.getpid())
        own_ppid = str(os.getppid())
        for pid in os.listdir("/proc"):
            if not pid.isdigit() or pid in (own_pid, own_ppid):
                continue
            # Skip children of PermGuard (pkexec helpers, etc.)
            try:
                status = Path(f"/proc/{pid}/status").read_text()
                ppid_m = re.search(r"^PPid:\s+(\d+)", status, re.M)
                if ppid_m and ppid_m.group(1) == own_pid:
                    continue
            except OSError:
                pass
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
                # Map of pid → (comm, cmdline) for pids we *announce*.
                # Read-only invocations (e.g. `apt list`) are skipped entirely
                # so they never enter _known — otherwise access_gone would
                # fire later for a process we never announced.
                for pid in os.listdir("/proc"):
                    if not pid.isdigit():
                        continue
                    try:
                        comm = Path(f"/proc/{pid}/comm").read_text().strip()
                    except OSError:
                        continue
                    if comm not in PACKAGE_MANAGERS:
                        continue
                    cmdline = proc_cmdline(pid)
                    if _is_read_only_invocation(comm, cmdline):
                        continue
                    current.add(pid)
                    if pid not in self._known:
                        self.new_access.emit(AccessEvent(
                            pid=pid,
                            app_name=comm,
                            cmdline=cmdline,
                            resource="package_install",
                        ))

                for pid in self._known - current:
                    self.access_gone.emit(pid)
                self._known = current
            except Exception:
                pass
            time.sleep(self.INTERVAL)

    def stop(self):
        self._running = False
        self.wait(2000)
