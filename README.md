# 🛡 PermGuard

**Android-like privacy & permission manager for Linux/KDE**

PermGuard monitors your system in real-time and intercepts camera and microphone access attempts — just like Android does. When an app tries to use your camera or mic, a popup appears asking you to **Allow**, **Allow this time**, or **Deny**. Decisions can be saved permanently per app.

---

## Features

- **Real-time permission dialogs** — popup appears the moment an app accesses your camera or mic
- **Allow / Allow this time / Deny** — same three options as Android, with a "remember" toggle
- **Saved rules** — manage all per-app decisions from the Permissions tab
- **Auto-deny countdown** — if you don't respond in 30 seconds, access is denied automatically
- **Dashboard** — at-a-glance overview of all active accesses
- **Block toggles** — instantly block camera or microphone system-wide with one click
- **Network monitor** — see all active connections per process
- **USB devices** — view connected USB hardware
- **Open ports** — see which processes are listening on which ports
- **Process manager** — view top processes by CPU, with kill button
- **Event log** — full audit trail of all access and decisions
- **Auto-start** — optional start at login via KDE autostart
- **System tray** — stays in background, always monitoring

---

## Screenshots

> _Add screenshots here_

---

## Installation

### One command (recommended)

```bash
git clone https://github.com/YOUR_USERNAME/PermGuard.git && cd PermGuard && bash install.sh
```

That's it. The installer handles everything:
- Installs system dependencies (`apt`)
- Installs the Python package (`pip`)
- Creates the `permguard` command in your PATH
- Adds PermGuard to your app menu

Then run it:
```bash
permguard
```

### Uninstall

```bash
bash install.sh --uninstall
```

---

## Requirements

The installer handles these automatically.

| Dependency | Purpose |
|---|---|
| Python 3.10+ | Runtime |
| PyQt6 | GUI framework |
| PipeWire / PulseAudio | Microphone monitoring (`pactl`) |
| `fuser` / `lsof` | Camera device detection |
| `ss` | Network connection info |
| `lsusb` | USB device listing |
| `pkexec` | Privileged device blocking |

---

## How it works

PermGuard runs two background threads:

1. **CameraMonitor** — polls `/dev/video*` every second using `fuser`/`lsof` for new PIDs
2. **MicMonitor** — polls `pactl list source-outputs` every second for new audio capture streams

When a new PID is detected:
1. The app name is looked up via `/proc/<pid>/comm`
2. The permission database is checked for a saved rule
3. If **allow** → let it through silently
4. If **deny** → immediately terminate the stream / kill the process
5. If **unknown** → show the Android-style dialog

> **Note:** Detection happens within ~1 second of access starting. This is the practical limit without kernel-level hooks (eBPF/fanotify). Future versions may add deeper integration.

---

## Permission storage

Rules are saved to `~/.local/share/permguard/permissions.json`:

```json
{
  "firefox": {
    "camera": "allow",
    "microphone": "deny"
  },
  "obs": {
    "camera": "allow",
    "microphone": "allow"
  }
}
```

---

## Project structure

```
permguard/
├── run.py                      ← entry point
├── requirements.txt
├── install.sh
├── permguard/
│   ├── main.py                 ← app bootstrap, monitors wired here
│   ├── core/
│   │   ├── monitor.py          ← CameraMonitor, MicMonitor (QThread)
│   │   ├── permissions.py      ← permission DB (JSON)
│   │   ├── data.py             ← system data sources
│   │   └── system.py           ← low-level helpers (kill, block, proc info)
│   └── ui/
│       ├── main_window.py      ← main QMainWindow + all tabs
│       ├── permission_dialog.py ← Android-style popup
│       ├── widgets.py          ← reusable components
│       └── styles.py           ← colors and Qt stylesheets
└── assets/
    └── icon.svg
```

---

## Roadmap

- [ ] eBPF-based pre-emptive blocking (block before first frame is captured)
- [ ] Per-app network firewall (allow/deny internet access per process)
- [ ] Clipboard access monitoring
- [ ] Flatpak permission management integration
- [ ] Dark/light theme toggle
- [ ] Notification history viewer

---

## Contributing

Pull requests welcome. Please open an issue first to discuss major changes.

1. Fork the repo
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Commit your changes
4. Open a Pull Request

---

## License

MIT License — see [LICENSE](LICENSE)

---

## Author

Built for Parrot OS / KDE Plasma 6, works on any Linux with PipeWire/PulseAudio.
