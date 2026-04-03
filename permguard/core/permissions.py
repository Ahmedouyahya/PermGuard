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
import json
from pathlib import Path
from datetime import datetime

DATA_DIR  = Path.home() / ".local/share/permguard"
PERM_FILE = DATA_DIR / "permissions.json"
LOG_FILE  = DATA_DIR / "events.log"

ALLOW = "allow"
DENY  = "deny"
ASK   = "ask"

RESOURCES = ("camera", "microphone", "screen")


class PermissionDB:
    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._db: dict[str, dict[str, str]] = {}
        self.load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def load(self):
        if PERM_FILE.exists():
            try:
                self._db = json.loads(PERM_FILE.read_text())
            except Exception:
                self._db = {}

    def save(self):
        PERM_FILE.write_text(json.dumps(self._db, indent=2))

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
            for resource, decision in perms.items():
                rows.append((app, resource, decision))
        return sorted(rows)

    def reset_all(self):
        self._db = {}
        self.save()

    # ── Logging ───────────────────────────────────────────────────────────────

    def log(self, msg: str):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE, "a") as f:
            f.write(f"[{ts}] {msg}\n")

    def get_log(self, last_n: int = 100) -> list[str]:
        try:
            lines = LOG_FILE.read_text().splitlines()
            return list(reversed(lines[-last_n:]))
        except Exception:
            return []
