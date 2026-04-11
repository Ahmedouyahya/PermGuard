"""
permissions.py — Permission database: load, save, and query per-app decisions.

Schema (JSON):
{
  "firefox": {
    "camera":      "allow",   # allow | deny | ask
    "microphone":  "deny"
  },
  "obs": {
    "camera": "allow",
    "microphone": "allow"
  }
}
"""
import json, os, tempfile, threading
from pathlib import Path
from datetime import datetime, timedelta

DATA_DIR  = Path.home() / ".local/share/permguard"
PERM_FILE = DATA_DIR / "permissions.json"
LOG_FILE  = DATA_DIR / "events.log"
TIMELINE_FILE = DATA_DIR / "timeline.json"


def _write_private(path: Path, text: str):
    """Atomically write `text` to `path` with 0o600 permissions.
    Uses a temp file in the same directory + os.replace so readers
    never see a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", dir=str(path.parent))
    try:
        os.write(fd, text.encode())
        os.close(fd)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

ALLOW = "allow"
DENY  = "deny"
ASK   = "ask"

RESOURCES = ("camera", "microphone", "screen")


MAX_LOG_LINES = 5000


class PermissionDB:
    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        # Keep the data dir itself private
        try:
            os.chmod(DATA_DIR, 0o700)
        except OSError:
            pass
        self._db: dict[str, dict[str, str]] = {}
        # Timeline is cached in memory to avoid re-reading on every event.
        # A lock guards concurrent writes from multiple monitor threads.
        self._timeline_cache: list[dict] | None = None
        self._timeline_lock = threading.Lock()
        self.load()
        self._rotate_log()

    # ── Persistence ───────────────────────────────────────────────────────────

    def load(self):
        if PERM_FILE.exists():
            try:
                self._db = json.loads(PERM_FILE.read_text())
            except Exception:
                self._db = {}

    def save(self):
        _write_private(PERM_FILE, json.dumps(self._db, indent=2))

    # ── Query / Update ────────────────────────────────────────────────────────

    def get(self, app: str, resource: str) -> str:
        """Return stored decision for (app, resource), or ASK if unknown."""
        return self._db.get(app, {}).get(resource, ASK)

    def set(self, app: str, resource: str, decision: str):
        """Persist a decision. decision must be ALLOW or DENY."""
        if app not in self._db:
            self._db[app] = {}
        self._db[app][resource] = decision
        self.save()
        self.log(f"Permission set: {app} → {resource} = {decision}")

    def remove(self, app: str, resource: str | None = None):
        """Remove a specific rule, or all rules for an app."""
        if app in self._db:
            if resource:
                self._db[app].pop(resource, None)
                if not self._db[app]:
                    del self._db[app]
            else:
                del self._db[app]
            self.save()

    def all_rules(self) -> list[tuple[str, str, str]]:
        """Return list of (app, resource, decision) for all stored rules."""
        rows = []
        for app, perms in self._db.items():
            if app.startswith("__") and app.endswith("__"):
                continue  # skip internal metadata keys
            for resource, decision in perms.items():
                rows.append((app, resource, decision))
        return sorted(rows)

    def reset_all(self):
        """Clear all user permission rules but preserve internal metadata."""
        internal = {k: v for k, v in self._db.items()
                    if k.startswith("__") and k.endswith("__")}
        self._db = internal
        self.save()

    # ── Logging ───────────────────────────────────────────────────────────────

    def log(self, msg: str):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # Create the log with 0o600 perms on first write
        new_file = not LOG_FILE.exists()
        with open(LOG_FILE, "a") as f:
            f.write(f"[{ts}] {msg}\n")
        if new_file:
            try:
                os.chmod(LOG_FILE, 0o600)
            except OSError:
                pass

    def get_log(self, last_n: int = 100) -> list[str]:
        try:
            lines = LOG_FILE.read_text().splitlines()
            return list(reversed(lines[-last_n:]))
        except Exception:
            return []

    def _rotate_log(self):
        """Trim log to MAX_LOG_LINES on startup to prevent unbounded growth."""
        try:
            if not LOG_FILE.exists():
                return
            lines = LOG_FILE.read_text().splitlines()
            if len(lines) > MAX_LOG_LINES:
                LOG_FILE.write_text("\n".join(lines[-MAX_LOG_LINES:]) + "\n")
        except Exception:
            pass

    # ── Structured timeline (for Privacy Dashboard) ──────────────────────────

    def _load_timeline(self) -> list[dict]:
        """Return the timeline list. Cached in memory after first read."""
        if self._timeline_cache is not None:
            return self._timeline_cache
        try:
            if TIMELINE_FILE.exists():
                self._timeline_cache = json.loads(TIMELINE_FILE.read_text())
                if not isinstance(self._timeline_cache, list):
                    self._timeline_cache = []
                return self._timeline_cache
        except Exception:
            pass
        self._timeline_cache = []
        return self._timeline_cache

    def _save_timeline(self, events: list[dict]):
        self._timeline_cache = events
        _write_private(TIMELINE_FILE, json.dumps(events, indent=1))

    def record_access(self, app: str, resource: str, decision: str, pid: str = ""):
        """Record a structured access event for the privacy dashboard.
        Thread-safe: monitors run on QThread workers and may call this
        concurrently."""
        with self._timeline_lock:
            events = list(self._load_timeline())
            events.append({
                "ts": datetime.now().isoformat(),
                "app": app,
                "resource": resource,
                "decision": decision,
                "pid": pid,
            })
            # Keep only last 7 days of events
            cutoff = (datetime.now() - timedelta(days=7)).isoformat()
            events = [e for e in events if e.get("ts", "") >= cutoff]
            self._save_timeline(events)

    def get_timeline(self, hours: int = 24) -> list[dict]:
        """Return access events from the last N hours, newest first."""
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
        with self._timeline_lock:
            events = list(self._load_timeline())
        recent = [e for e in events if e.get("ts", "") >= cutoff]
        return list(reversed(recent))

    def get_app_last_seen(self) -> dict[str, str]:
        """Return {app_name: last_seen_iso_timestamp} for all known apps."""
        last: dict[str, str] = {}
        with self._timeline_lock:
            events = list(self._load_timeline())
        for e in events:
            app = e.get("app", "")
            ts  = e.get("ts", "")
            if app and ts > last.get(app, ""):
                last[app] = ts
        return last
