"""
updater.py — Self-update helper.

Clones or fast-forwards the GitHub repo into ~/.cache/permguard/source,
then runs install.sh to redeploy into ~/.local/share/permguard.
Streams git/install output back to the UI via Qt signals so the user
sees progress instead of a frozen button.
"""
import os, shutil, subprocess
from pathlib import Path
from PyQt6.QtCore import QThread, pyqtSignal

REPO_URL  = "https://github.com/Ahmedouyahya/PermGuard.git"
CACHE_DIR = Path.home() / ".cache" / "permguard" / "source"


class UpdateWorker(QThread):
    line        = pyqtSignal(str)           # one line of streamed output
    finished_ok = pyqtSignal(bool, str)     # (success, summary)

    def run(self):
        if not shutil.which("git"):
            self.finished_ok.emit(False, "git is not installed.")
            return
        try:
            CACHE_DIR.parent.mkdir(parents=True, exist_ok=True)
            if (CACHE_DIR / ".git").exists():
                self.line.emit(f"→ Fetching {REPO_URL}")
                self._stream(["git", "-C", str(CACHE_DIR),
                              "fetch", "--depth", "1", "origin", "HEAD"])
                self._stream(["git", "-C", str(CACHE_DIR),
                              "reset", "--hard", "FETCH_HEAD"])
            else:
                if CACHE_DIR.exists():
                    shutil.rmtree(CACHE_DIR)
                self.line.emit(f"→ Cloning {REPO_URL}")
                self._stream(["git", "clone", "--depth", "1",
                              REPO_URL, str(CACHE_DIR)])

            installer = CACHE_DIR / "install.sh"
            if not installer.exists():
                self.finished_ok.emit(False, "install.sh missing in fetched source.")
                return

            self.line.emit("→ Running install.sh")
            self._stream(["bash", str(installer)])
            self.finished_ok.emit(
                True,
                "Update complete. Restart PermGuard to load the new code.",
            )
        except subprocess.CalledProcessError as e:
            self.finished_ok.emit(False, f"Command failed (exit {e.returncode}).")
        except Exception as e:
            self.finished_ok.emit(False, f"Update failed: {e}")

    def _stream(self, cmd: list[str]):
        env = os.environ.copy()
        env.setdefault("GIT_TERMINAL_PROMPT", "0")  # fail fast, never hang
        p = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, env=env,
        )
        assert p.stdout is not None
        for raw in p.stdout:
            self.line.emit(raw.rstrip())
        p.wait()
        if p.returncode != 0:
            raise subprocess.CalledProcessError(p.returncode, cmd)


def restart_permguard() -> bool:
    """Try to restart the user's permguard systemd service.
    Returns True if the restart was dispatched (the current process will
    be killed by systemd shortly after). False means the caller should
    fall back to just quitting and letting the user relaunch."""
    if not shutil.which("systemctl"):
        return False
    try:
        # Detach so we survive long enough to quit cleanly
        subprocess.Popen(
            ["sh", "-c",
             "sleep 1 && systemctl --user restart permguard.service"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except Exception:
        return False
