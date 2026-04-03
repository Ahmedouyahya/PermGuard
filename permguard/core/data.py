"""
data.py — System data sources: who is using camera, mic, ports, etc.
"""
import os, re
from pathlib import Path
from .system import run, proc_name, proc_cmdline, proc_user


# ── Camera ────────────────────────────────────────────────────────────────────

def get_camera_pids() -> set[str]:
    """Return set of PIDs currently opening any /dev/video* device."""
    devices = sorted(f"/dev/{f}" for f in os.listdir("/dev") if re.match(r"video\d+$", f))
    if not devices:
        return set()
    out = run(["fuser"] + devices)
    pids = set(re.findall(r"\d+", out))
    if not pids:
        # fallback: lsof
        out2 = run(["lsof"] + devices)
        for line in out2.splitlines()[1:]:
            parts = line.split()
            if len(parts) > 1:
                pids.add(parts[1])
    return pids


def get_camera_users() -> list[tuple]:
    return [(p, proc_name(p), proc_user(p), proc_cmdline(p)) for p in get_camera_pids()]


# ── Microphone ────────────────────────────────────────────────────────────────

def get_mic_streams() -> list[dict]:
    """Return list of dicts with stream_index, pid, app_name, cmdline."""
    results = []
    out = run(["pactl", "list", "source-outputs"])
    for block in out.split("Source Output #")[1:]:
        idx_m  = re.match(r"(\d+)", block)
        pid_m  = re.search(r'application\.process\.id\s*=\s*"(\d+)"', block)
        name_m = re.search(r'application\.name\s*=\s*"([^"]+)"', block)
        if not pid_m:
            continue
        results.append({
            "stream_index": idx_m.group(1) if idx_m else "?",
            "pid":          pid_m.group(1),
            "app_name":     name_m.group(1) if name_m else proc_name(pid_m.group(1)),
            "cmdline":      proc_cmdline(pid_m.group(1)),
            "user":         proc_user(pid_m.group(1)),
        })
    return results


def get_mic_pids() -> set[str]:
    return {s["pid"] for s in get_mic_streams()}


def get_mic_users() -> list[tuple]:
    return [(s["pid"], s["app_name"], s["user"], s["cmdline"]) for s in get_mic_streams()]


# ── Screen share ──────────────────────────────────────────────────────────────

def get_screen_share() -> list[tuple]:
    results = []
    out = run(["pw-cli", "list-objects", "PipeWire:Interface:Node"])
    for line in out.splitlines():
        if any(k in line.lower() for k in ("screencast", "screen-cast", "xdg-desktop-portal")):
            results.append(("—", "Screen Recording Session", line.strip()[:70]))
    return results


# ── Network ───────────────────────────────────────────────────────────────────

def get_network_conns() -> list[tuple]:
    out = run(["ss", "-tunp"])
    rows, seen = [], set()
    for line in out.splitlines()[1:]:
        pid_m  = re.search(r'pid=(\d+)', line)
        name_m = re.search(r'"([^"]+)"', line)
        if not pid_m:
            continue
        pid   = pid_m.group(1)
        name  = name_m.group(1) if name_m else proc_name(pid)
        parts = line.split()
        state  = parts[1] if len(parts) > 1 else "?"
        local  = parts[4] if len(parts) > 4 else "?"
        remote = parts[5] if len(parts) > 5 else "?"
        key = (pid, local, remote)
        if key not in seen:
            seen.add(key)
            rows.append((pid, name, state, local, remote))
    return rows


# ── Open ports ────────────────────────────────────────────────────────────────

def get_open_ports() -> list[tuple]:
    rows, seen = [], set()
    for proto, flag in [("TCP", "-tlnp"), ("UDP", "-ulnp")]:
        out = run(["ss", flag])
        for line in out.splitlines()[1:]:
            pid_m  = re.search(r'pid=(\d+)', line)
            name_m = re.search(r'"([^"]+)"', line)
            parts  = line.split()
            local  = parts[3] if len(parts) > 3 else "?"
            pid    = pid_m.group(1) if pid_m else "—"
            name   = name_m.group(1) if name_m else (proc_name(pid) if pid != "—" else "—")
            key    = (proto, local)
            if key not in seen:
                seen.add(key)
                rows.append((proto, local, name, pid))
    return rows


# ── USB devices ───────────────────────────────────────────────────────────────

def get_usb_devices() -> list[tuple]:
    rows = []
    for line in run(["lsusb"]).splitlines():
        m = re.match(r"Bus (\d+) Device (\d+): ID ([0-9a-f:]+)\s+(.*)", line)
        if m:
            rows.append((f"Bus {m.group(1)}", f"Dev {m.group(2)}", m.group(3), m.group(4).strip()))
    return rows


# ── Top processes ─────────────────────────────────────────────────────────────

def get_top_procs(limit: int = 15) -> list[tuple]:
    out = run(["ps", "aux", "--sort=-%cpu"])
    rows = []
    for line in out.splitlines()[1:limit + 1]:
        parts = line.split(None, 10)
        if len(parts) >= 11:
            rows.append((parts[1], parts[0], parts[2], parts[3], parts[10][:50]))
    return rows
