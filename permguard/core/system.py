"""
system.py — Low-level system helpers: process info, device control, shell.
"""
import os, re, subprocess, json
from pathlib import Path

_STATE_FILE = Path.home() / ".local/share/permguard/device_state.json"

def _load_state() -> dict:
    try:
        return json.loads(_STATE_FILE.read_text()) if _STATE_FILE.exists() else {}
    except Exception:
        return {}

def _save_state(key: str, value: bool):
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    s = _load_state()
    s[key] = value
    _STATE_FILE.write_text(json.dumps(s))


# ── Shell ─────────────────────────────────────────────────────────────────────

def run(cmd: list, timeout: int = 5) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


def run_privileged(cmd: list) -> tuple[bool, str]:
    """Run a command with pkexec (graphical sudo prompt)."""
    try:
        r = subprocess.run(["pkexec"] + cmd, capture_output=True, text=True, timeout=15)
        return r.returncode == 0, r.stderr.strip()
    except FileNotFoundError:
        # pkexec not available, try sudo
        try:
            r = subprocess.run(["sudo", "-n"] + cmd, capture_output=True, text=True, timeout=10)
            return r.returncode == 0, r.stderr.strip()
        except Exception as e:
            return False, str(e)
    except Exception as e:
        return False, str(e)


# ── Process helpers ───────────────────────────────────────────────────────────

def proc_name(pid: str) -> str:
    try:
        return Path(f"/proc/{pid}/comm").read_text().strip()
    except Exception:
        return "unknown"


def proc_cmdline(pid: str) -> str:
    try:
        return Path(f"/proc/{pid}/cmdline").read_text().replace("\x00", " ").strip()[:80]
    except Exception:
        return ""


def proc_user(pid: str) -> str:
    try:
        status = Path(f"/proc/{pid}/status").read_text()
        uid_m = re.search(r"^Uid:\s+(\d+)", status, re.M)
        if uid_m:
            return run(["id", "-un", uid_m.group(1)]).strip() or uid_m.group(1)
    except Exception:
        pass
    return "?"


def proc_icon_path(app_name: str) -> str | None:
    """Try to find a .desktop file for the app and return its icon name."""
    search_dirs = [
        Path("/usr/share/applications"),
        Path.home() / ".local/share/applications",
        Path("/var/lib/flatpak/exports/share/applications"),
    ]
    name_lower = app_name.lower()
    for d in search_dirs:
        if not d.exists():
            continue
        for f in d.glob("*.desktop"):
            if name_lower in f.stem.lower():
                content = f.read_text(errors="ignore")
                m = re.search(r"^Icon\s*=\s*(.+)$", content, re.M)
                if m:
                    return m.group(1).strip()
    return None


def kill_pid(pid: str) -> tuple[bool, str]:
    """Terminate a process. Tries SIGTERM → SIGKILL → pkexec kill."""
    try:
        ipid = int(pid)
    except ValueError:
        return False, f"Invalid PID: {pid}"

    import signal as _signal, time as _time

    # 1. SIGTERM — polite request
    try:
        os.kill(ipid, _signal.SIGTERM)
        _time.sleep(0.3)
        # Check if it's gone
        os.kill(ipid, 0)   # raises ProcessLookupError if dead
    except ProcessLookupError:
        return True, ""    # gone after SIGTERM
    except PermissionError:
        pass               # we own it but can't check, fall through
    except Exception as e:
        # SIGTERM itself failed — likely PermissionError
        pass

    # 2. SIGKILL — force
    try:
        os.kill(ipid, _signal.SIGKILL)
        return True, ""
    except ProcessLookupError:
        return True, ""    # gone
    except PermissionError:
        pass               # need elevated privileges
    except Exception as e:
        return False, str(e)

    # 3. pkexec kill -9 — root fallback
    return run_privileged(["kill", "-9", str(pid)])


# ── Camera control ────────────────────────────────────────────────────────────

def video_devices() -> list[str]:
    return sorted(f"/dev/{f}" for f in os.listdir("/dev") if re.match(r"video\d+$", f))


def camera_is_blocked() -> bool:
    devs = video_devices()
    if not devs:
        return False
    try:
        return (os.stat(devs[0]).st_mode & 0o777) == 0
    except Exception:
        return False


def set_camera_blocked(block: bool) -> tuple[bool, str]:
    devs = video_devices()
    if not devs:
        return False, "No video devices found"
    perm = "000" if block else "660"
    ok, err = run_privileged(["chmod", perm] + devs)
    if ok:
        _save_state("camera_blocked", block)
    return ok, err


# ── Microphone control ────────────────────────────────────────────────────────

def mic_source_indices() -> list[str]:
    out = run(["pactl", "list", "sources", "short"])
    return [line.split()[0] for line in out.splitlines() if line.strip()]


def mic_is_suspended() -> bool:
    out = run(["pactl", "list", "sources", "short"])
    lines = [l for l in out.splitlines() if l.strip()]
    return bool(lines) and all("SUSPENDED" in l for l in lines)


def set_mic_suspended(suspend: bool) -> tuple[bool, str]:
    val = "1" if suspend else "0"
    indices = mic_source_indices()
    if not indices:
        return False, "No audio sources found"
    for idx in indices:
        try:
            r = subprocess.run(
                ["pactl", "suspend-source", idx, val],
                capture_output=True, text=True, timeout=5
            )
            if r.returncode != 0:
                return False, r.stderr.strip()
        except Exception as e:
            return False, str(e)
    _save_state("mic_suspended", suspend)
    return True, ""


def restore_device_state():
    """Re-apply camera/mic block state saved before last shutdown."""
    s = _load_state()
    if s.get("camera_blocked"):
        set_camera_blocked(True)
    if s.get("mic_suspended"):
        set_mic_suspended(True)


def kill_mic_stream(stream_index: str) -> tuple[bool, str]:
    try:
        r = subprocess.run(
            ["pactl", "kill-source-output", stream_index],
            capture_output=True, text=True, timeout=5
        )
        return r.returncode == 0, r.stderr.strip()
    except Exception as e:
        return False, str(e)
