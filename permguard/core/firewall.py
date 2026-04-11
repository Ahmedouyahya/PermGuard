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
import os, json, re, tempfile
from pathlib import Path
from .system import run, run_privileged, proc_name

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

def _kill_connections(pid: str):
    """Kill all active TCP/UDP connections of a process."""
    run(["ss", "--kill", f"( sport > 0 )", "pid", f"({pid})"])
    # Also try by process name in case ss version differs
    name = proc_name(pid)
    out = run(["ss", "-tunp"])
    for line in out.splitlines():
        if f'"{name}"' in line or f"pid={pid}" in line:
            parts = line.split()
            if len(parts) > 4:
                dst = parts[4]
                run(["ss", "--kill", "dst", dst])

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
    """
    uid = _pid_uid(pid)
    if uid is None:
        return False, f"Process {pid} not found"

    # Kill existing connections immediately
    _kill_connections(pid)

    # Add iptables rule (skip if already present to avoid duplicates)
    if not _iptables_rule_exists(uid):
        ok, err = _iptables_rule("-I", uid)
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
