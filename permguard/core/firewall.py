"""
firewall.py — Per-app network blocking using iptables.

How it works:
  1. Kill all existing connections of the process with `ss --kill`
  2. Add an iptables OUTPUT rule keyed by the process's UID + cgroup
  3. Store the rule in a JSON file so it survives restarts

Limitation: iptables owner module blocks by UID, not PID.
If multiple apps run as the same user, they are all affected.
Root-owned processes require a different approach (cgroup-based).
"""
import os, json, socket, struct, tempfile
from pathlib import Path
from .system import run, run_privileged

RULES_FILE = Path.home() / ".local/share/permguard/firewall_rules.json"


# ── Persistence ────────────────────────────────────────────────────────────────

def _load() -> dict:
    try:
        return json.loads(RULES_FILE.read_text()) if RULES_FILE.exists() else {}
    except Exception:
        return {}

def _save(rules: dict):
    RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", dir=str(RULES_FILE.parent))
    try:
        os.write(fd, json.dumps(rules, indent=2).encode())
        os.close(fd)
        os.chmod(tmp, 0o600)
        os.replace(tmp, RULES_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── Helpers ────────────────────────────────────────────────────────────────────

def _pid_uid(pid: str) -> int | None:
    try:
        return os.stat(f"/proc/{pid}").st_uid
    except Exception:
        return None

def _parse_hex_v4(hex_addr: str) -> str:
    ip_hex, port_hex = hex_addr.split(":")
    ip = socket.inet_ntoa(struct.pack("<I", int(ip_hex, 16)))
    return f"{ip}:{int(port_hex, 16)}"


def _parse_hex_v6(hex_addr: str) -> str:
    ip_hex, port_hex = hex_addr.split(":")
    words = [struct.pack("<I", int(ip_hex[i:i+8], 16)) for i in range(0, 32, 8)]
    ip = socket.inet_ntop(socket.AF_INET6, b"".join(words))
    return f"[{ip}]:{int(port_hex, 16)}"


def _pid_socket_inodes(pid: str) -> set[str]:
    inodes: set[str] = set()
    try:
        for fd in os.listdir(f"/proc/{pid}/fd"):
            try:
                lnk = os.readlink(f"/proc/{pid}/fd/{fd}")
                if lnk.startswith("socket:["):
                    inodes.add(lnk[8:-1])
            except OSError:
                pass
    except OSError:
        pass
    return inodes


def _build_socket_kill_script(pid: str) -> str:
    """Return a shell script that closes the pid's active TCP sockets.
    ss has no `pid` filter term, so we read /proc/<pid>/fd to find the
    process's socket inodes, look them up in /proc/net/tcp[6], and emit
    one `ss -K src X dst Y` command per socket. All commands are batched
    into a single script so they can run in one privileged invocation."""
    inodes = _pid_socket_inodes(pid)
    if not inodes:
        return ""
    lines: list[str] = []
    for path in ("/proc/net/tcp", "/proc/net/tcp6"):
        is_v6 = path.endswith("6")
        try:
            rows = Path(path).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in rows:
            p = line.split()
            if len(p) < 10 or p[9] not in inodes:
                continue
            try:
                src = _parse_hex_v6(p[1]) if is_v6 else _parse_hex_v4(p[1])
                dst = _parse_hex_v6(p[2]) if is_v6 else _parse_hex_v4(p[2])
            except Exception:
                continue
            # src/dst come from kernel hex; only digits, dots, colons, brackets
            lines.append(f"ss -K src {src} dst {dst} >/dev/null 2>&1 || true")
    return "\n".join(lines)

def _iptables_rule(action: str, uid: int) -> tuple[bool, str]:
    """Add (-I) or remove (-D) an iptables OUTPUT rule for a UID.
    Note: -m owner only works on OUTPUT (locally generated packets),
    not INPUT. Blocking OUTPUT is sufficient to cut off network access."""
    ok, err = run_privileged([
        "iptables", action, "OUTPUT",
        "-m", "owner", "--uid-owner", str(uid), "-j", "DROP"
    ])
    return ok, err


def _iptables_rule_exists(uid: int) -> bool:
    """Return True if a DROP rule for this uid is already in OUTPUT."""
    ok, _ = run_privileged([
        "iptables", "-C", "OUTPUT",
        "-m", "owner", "--uid-owner", str(uid), "-j", "DROP"
    ])
    return ok


def iptables_available() -> bool:
    import shutil
    # iptables (or iptables-nft wrapper used on modern Fedora/Debian)
    if shutil.which("iptables"):
        ok, _ = run_privileged(["iptables", "-L", "OUTPUT", "-n"])
        return ok
    return False


# ── Public API ─────────────────────────────────────────────────────────────────

def block_app(pid: str, app_name: str) -> tuple[bool, str]:
    """
    Block all network traffic for the process.
    Returns (success, error_message).
    Uses a single pkexec invocation to add the iptables rule and close
    existing sockets, so the user is prompted for auth at most once.
    """
    uid = _pid_uid(pid)
    if uid is None:
        return False, f"Process {pid} not found"

    script_lines: list[str] = []
    if not _iptables_rule_exists(uid):
        script_lines.append(
            f"iptables -I OUTPUT -m owner --uid-owner {uid} -j DROP"
        )
    kill_script = _build_socket_kill_script(pid)
    if kill_script:
        script_lines.append(kill_script)

    if script_lines:
        ok, err = run_privileged(["sh"], stdin_data="\n".join(script_lines))
        if not ok:
            return False, f"iptables failed: {err}"

    # Persist
    rules = _load()
    rules[app_name] = {"uid": uid, "pid": pid}
    _save(rules)
    return True, ""


def unblock_app(app_name: str) -> tuple[bool, str]:
    """Remove the network block for an app."""
    rules = _load()
    if app_name not in rules:
        return False, "No rule found"
    uid = rules[app_name]["uid"]
    ok, err = _iptables_rule("-D", uid)
    if ok:
        del rules[app_name]
        _save(rules)
    return ok, err


def is_blocked(app_name: str) -> bool:
    return app_name in _load()


def get_blocked_apps() -> list[dict]:
    """Return list of dicts with name, uid, pid for all blocked apps."""
    return [{"name": k, **v} for k, v in _load().items()]


def clear_all_blocks() -> tuple[bool, str]:
    """Remove all PermGuard iptables rules."""
    rules = _load()
    errors = []
    for name, info in rules.items():
        ok, err = _iptables_rule("-D", info["uid"])
        if not ok:
            errors.append(err)
    _save({})
    return (len(errors) == 0), "\n".join(errors)


def restore_rules_on_startup():
    """Re-apply persisted rules after reboot (called at app start).
    Skips UIDs that already have a matching DROP rule, so restarts
    without a reboot don't accumulate duplicate rules."""
    for info in get_blocked_apps():
        uid = info["uid"]
        if not _iptables_rule_exists(uid):
            _iptables_rule("-I", uid)
