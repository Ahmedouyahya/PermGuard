"""
data.py — System data sources.

Optimized to read kernel interfaces directly instead of spawning subprocesses:
  - Camera:   /proc/<pid>/fd  symlinks  (was: fuser  — 57ms → 9ms)
  - Network:  /proc/net/tcp + tcp6      (was: ss     — 18ms → 10ms)
  - Ports:    /proc/net/tcp  (LISTEN)   (was: ss     — 28ms → 10ms)
  - Mic:      pactl (4.8ms, kept as-is — already fast)
  - USB:      sysfs (1ms, already optimal)
  - Procs:    ps aux (kept — /proc/stat not faster for 290+ processes)
"""
import os, re, socket, struct
from pathlib import Path
from .system import run, proc_name, proc_cmdline, proc_user


# ── Shared /proc helpers ──────────────────────────────────────────────────────

def _inode_to_pid() -> dict[str, str]:
    """Map socket inode → pid by scanning /proc/*/fd symlinks."""
    mapping: dict[str, str] = {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        fd_dir = f"/proc/{pid}/fd"
        try:
            for fd in os.listdir(fd_dir):
                try:
                    lnk = os.readlink(f"{fd_dir}/{fd}")
                    if lnk.startswith("socket:["):
                        mapping[lnk[8:-1]] = pid
                except OSError:
                    pass
        except OSError:
            pass
    return mapping


def _parse_hex_addr_v4(hex_addr: str) -> str:
    ip_hex, port_hex = hex_addr.split(":")
    ip = socket.inet_ntoa(struct.pack("<I", int(ip_hex, 16)))
    return f"{ip}:{int(port_hex, 16)}"


def _parse_hex_addr_v6(hex_addr: str) -> str:
    ip_hex, port_hex = hex_addr.split(":")
    # 4 little-endian 32-bit words
    words = [struct.pack("<I", int(ip_hex[i:i+8], 16)) for i in range(0, 32, 8)]
    ip = socket.inet_ntop(socket.AF_INET6, b"".join(words))
    return f"[{ip}]:{int(port_hex, 16)}"


_TCP_STATES = {
    "01": "ESTABLISHED", "02": "SYN_SENT",  "03": "SYN_RECV",
    "04": "FIN_WAIT1",   "05": "FIN_WAIT2", "06": "TIME_WAIT",
    "07": "CLOSE",       "08": "CLOSE_WAIT","09": "LAST_ACK",
    "0A": "LISTEN",      "0B": "CLOSING",
}


def _read_proc_net(path: str, inode_pid: dict, filter_state: str | None = None) -> list[tuple]:
    rows = []
    try:
        lines = Path(path).read_text().splitlines()[1:]
    except OSError:
        return rows
    is_v6 = "6" in path
    for line in lines:
        p = line.split()
        if len(p) < 10:
            continue
        state_hex = p[3].upper()
        if filter_state and state_hex != filter_state:
            continue
        state = _TCP_STATES.get(state_hex, state_hex)
        inode = p[9]
        pid   = inode_pid.get(inode, "—")
        try:
            if is_v6:
                local  = _parse_hex_addr_v6(p[1])
                remote = _parse_hex_addr_v6(p[2])
            else:
                local  = _parse_hex_addr_v4(p[1])
                remote = _parse_hex_addr_v4(p[2])
        except Exception:
            local, remote = p[1], p[2]
        rows.append((pid, state, local, remote, inode))
    return rows


# ── Camera ────────────────────────────────────────────────────────────────────

_VIDEO_RE = re.compile(r"/dev/video\d+$")


def get_camera_pids() -> set[str]:
    """Find PIDs with an open file descriptor to any /dev/video* device.
    Reads /proc/<pid>/fd symlinks directly — no subprocess."""
    pids: set[str] = set()
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            for fd in os.listdir(f"/proc/{pid}/fd"):
                try:
                    if _VIDEO_RE.match(os.readlink(f"/proc/{pid}/fd/{fd}")):
                        pids.add(pid)
                        break
                except OSError:
                    pass
        except OSError:
            pass
    return pids


def get_camera_users() -> list[tuple]:
    return [(p, proc_name(p), proc_user(p), proc_cmdline(p)) for p in get_camera_pids()]


# ── Microphone ────────────────────────────────────────────────────────────────

def get_mic_streams() -> list[dict]:
    """PipeWire/PulseAudio source-output streams (via pactl)."""
    import shutil
    results: list[dict] = []
    if not shutil.which("pactl"):
        return results
    out = run(["pactl", "list", "source-outputs"])
    for block in out.split("Source Output #")[1:]:
        idx_m  = re.match(r"(\d+)", block)
        pid_m  = re.search(r'application\.process\.id\s*=\s*"(\d+)"', block)
        name_m = re.search(r'application\.name\s*=\s*"([^"]+)"', block)
        if not pid_m:
            continue
        pid = pid_m.group(1)
        results.append({
            "stream_index": idx_m.group(1) if idx_m else "?",
            "pid":          pid,
            "app_name":     name_m.group(1) if name_m else proc_name(pid),
            "cmdline":      proc_cmdline(pid),
            "user":         proc_user(pid),
        })
    return results


def get_mic_pids() -> set[str]:
    return {s["pid"] for s in get_mic_streams()}


def get_mic_users() -> list[tuple]:
    return [(s["pid"], s["app_name"], s["user"], s["cmdline"]) for s in get_mic_streams()]


# ── Screen share ──────────────────────────────────────────────────────────────

def get_screen_share() -> list[tuple]:
    import shutil
    results = []
    if shutil.which("pw-cli"):
        out = run(["pw-cli", "list-objects", "PipeWire:Interface:Node"])
        for line in out.splitlines():
            if any(k in line.lower() for k in ("screencast", "screen-cast", "xdg-desktop-portal")):
                results.append(("—", "Screen Recording Session (PipeWire)", line.strip()[:70]))
    # Fallback: check /proc for xdg-desktop-portal processes with screen access
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            comm = Path(f"/proc/{pid}/comm").read_text().strip()
            if "xdg-desktop-portal" in comm:
                cmdline = Path(f"/proc/{pid}/cmdline").read_text().replace("\x00", " ").strip()
                if "screencast" in cmdline.lower() or "screen" in cmdline.lower():
                    results.append((pid, comm, cmdline[:70]))
        except OSError:
            pass
    return results


# ── Network connections ───────────────────────────────────────────────────────

def get_network_conns() -> list[tuple]:
    """Active TCP/UDP connections mapped to processes.
    Reads /proc/net/tcp + tcp6 directly — no subprocess."""
    inode_pid = _inode_to_pid()
    rows: list[tuple] = []
    seen: set[tuple] = set()

    for path in ("/proc/net/tcp", "/proc/net/tcp6"):
        for pid, state, local, remote, _ in _read_proc_net(path, inode_pid):
            name = proc_name(pid) if pid != "—" else "—"
            key  = (pid, local, remote)
            if key not in seen:
                seen.add(key)
                rows.append((pid, name, state, local, remote))

    # Also UDP
    for path in ("/proc/net/udp", "/proc/net/udp6"):
        for pid, state, local, remote, _ in _read_proc_net(path, inode_pid):
            name = proc_name(pid) if pid != "—" else "—"
            key  = (pid, local, remote)
            if key not in seen:
                seen.add(key)
                rows.append((pid, name, "UDP", local, remote))

    return rows


# ── Open ports ────────────────────────────────────────────────────────────────

def get_open_ports() -> list[tuple]:
    """Listening ports — reads /proc/net/tcp directly, filters for LISTEN state."""
    inode_pid = _inode_to_pid()
    rows: list[tuple] = []
    seen: set[tuple] = set()

    for path, proto in (("/proc/net/tcp", "TCP"), ("/proc/net/tcp6", "TCP6"),
                        ("/proc/net/udp", "UDP"), ("/proc/net/udp6", "UDP6")):
        for pid, state, local, remote, _ in _read_proc_net(
                path, inode_pid,
                filter_state="0A" if "tcp" in path else None):
            name = proc_name(pid) if pid != "—" else "—"
            key  = (proto, local)
            if key not in seen:
                seen.add(key)
                rows.append((proto, local, name, pid))

    return rows


# ── USB devices ───────────────────────────────────────────────────────────────

def get_usb_devices() -> list[tuple]:
    """USB devices with authorization status — reads sysfs directly (1ms)."""
    from .usb_control import get_usb_ports
    rows = []
    for p in get_usb_ports():
        status = "✓ enabled" if p["authorized"] else "✗ DISABLED"
        rows.append((
            f"Bus {p['bus']}",
            f"Dev {p['devnum']}",
            f"{p['vendor_id']}:{p['product_id']}",
            p["product"],
            status,
        ))
    return rows


# ── Top processes ─────────────────────────────────────────────────────────────

def get_top_procs(limit: int = 15) -> list[tuple]:
    """Top processes by CPU — uses ps aux (subprocess is not slower here)."""
    out = run(["ps", "aux", "--sort=-%cpu"])
    rows = []
    for line in out.splitlines()[1:limit + 1]:
        parts = line.split(None, 10)
        if len(parts) >= 11:
            rows.append((parts[1], parts[0], parts[2], parts[3], parts[10][:50]))
    return rows
