# 🛡 PermGuard

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Linux-lightgrey.svg)]()
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)]()
[![KDE Plasma](https://img.shields.io/badge/KDE-Plasma%206-blue.svg)]()

**Android-like privacy & permission manager for Linux/KDE**

PermGuard watches your system in real-time. The moment an app tries to use your **camera** or **microphone**, a popup appears — just like on Android — asking you what to do. You choose, PermGuard remembers. Every app, every resource, under your control.

---

## Install

```bash
git clone https://github.com/Ahmedouyahya/PermGuard.git && cd PermGuard && bash install.sh
```

That's it. The installer handles everything automatically. Then run:

```bash
permguard
```

Or search **PermGuard** in your app menu.

**Uninstall:**
```bash
bash install.sh --uninstall
```

---

## How it works

### The permission flow

```
App opens /dev/video0          App starts capturing mic
         │                              │
         ▼                              ▼
  CameraMonitor thread          MicMonitor thread
  polls fuser every 1s          polls pactl every 1s
         │                              │
         └──────────────┬───────────────┘
                        ▼
              New PID detected?
                        │
         ┌──────────────┼───────────────┐
         ▼              ▼               ▼
   Rule = ALLOW    Rule = DENY     No rule yet
   (let it run)   (kill it now)         │
                                        ▼
                              Show permission dialog
                              ┌─────────────────────┐
                              │  Firefox            │
                              │  wants your camera  │
                              │                     │
                              │ [Deny][Once][Allow] │
                              └─────────────────────┘
                                        │
                  ┌─────────────────────┼──────────────────────┐
                  ▼                     ▼                      ▼
                 Deny                 Once                  Allow
             Kill process          Let it run             Let it run
            Save rule?             Don't save             Save rule?
            (if checked)                                 (if checked)
```

### Where data lives

| Path | What's stored |
|---|---|
| `~/.local/share/permguard/permissions.json` | Per-app permission rules |
| `~/.local/share/permguard/events.log` | Full audit log of every access and decision |
| `~/.config/autostart/permguard.desktop` | Auto-start entry (optional) |

### Architecture

```
permguard/
├── main.py                    ← Starts app, wires monitors to UI
│
├── core/
│   ├── monitor.py             ← Two QThreads running in background
│   │     CameraMonitor        ← polls fuser /dev/video* every second
│   │     MicMonitor           ← polls pactl list source-outputs every second
│   │
│   ├── permissions.py         ← JSON rule database (allow/deny/ask per app+resource)
│   ├── data.py                ← System queries (camera, mic, net, USB, ports, procs)
│   └── system.py              ← Low-level ops (kill PID, block device, proc info)
│
└── ui/
    ├── permission_dialog.py   ← The Android-style popup (PyQt6 QDialog)
    ├── main_window.py         ← Main window + all 9 tabs
    ├── widgets.py             ← Shared table/card components
    └── styles.py              ← Dark theme (Nord palette)
```

### The permission dialog

When an unknown app accesses a resource, PermGuard shows this floating dialog (always on top, appears near the top of the screen like Android):

```
╔══════════════════════════════════════╗
║  📷  Camera Access Request           ║
║                                      ║
║  Firefox                             ║
║  wants to access your camera         ║
║  /usr/lib/firefox/firefox            ║
║  PID 12345                           ║
║                                      ║
║  ☑ Remember my choice for this app   ║
║                                      ║
║  [Deny]  [Allow this time]  [Allow]  ║
╚══════════════════════════════════════╝
       Auto-deny in 30s if no response
```

- **Allow** — lets the app use the resource, saves rule if "Remember" is checked
- **Allow this time** — lets it through once, never saves, asks again next time
- **Deny** — kills the process / cuts the mic stream, saves rule if "Remember" is checked
- **Auto-deny** — if you don't respond in 30 seconds, access is automatically denied

### How blocking works

| Resource | Detection method | How access is revoked |
|---|---|---|
| Camera | `fuser /dev/video*` or `lsof` | `kill -15 <PID>` (SIGTERM) |
| Microphone | `pactl list source-outputs` | `pactl kill-source-output <index>` |
| Camera (global block) | — | `chmod 000 /dev/video*` via pkexec |
| Mic (global block) | — | `pactl suspend-source <index>` |

> **Note:** Detection happens within ~1 second of access starting. This is the practical limit on Linux without kernel-level hooks (eBPF/fanotify). Future versions will explore pre-emptive blocking.

---

## Features

| Tab | What it shows |
|---|---|
| 🏠 Dashboard | Live cards for all 6 categories + one-click camera/mic block |
| 📷 Camera | Apps currently capturing from webcam, kill button |
| 🎤 Mic | Apps capturing audio via PipeWire/PulseAudio, kill button |
| 🖥 Screen Share | Active screen recording/share sessions |
| 🌐 Network | All active TCP/UDP connections per process |
| 🔌 USB | Connected USB devices (bus, ID, name) |
| 🔒 Ports | Listening ports and which process owns each |
| ⚙️ Processes | Top 15 by CPU usage, kill button |
| 🔑 Permissions | All saved rules — revoke individual or reset all |
| ⚙ Settings | Auto-start, refresh interval, notifications, event log |

---

## Installation

### Requirements

- Linux (Debian / Ubuntu / Parrot OS or any derivative)
- Python 3.10+
- KDE Plasma 5/6 or GNOME (any desktop with a system tray)

Everything else is installed automatically.

### Step by step

**1. Clone the repo**
```bash
git clone https://github.com/Ahmedouyahya/PermGuard.git
cd PermGuard
```

**2. Run the installer**
```bash
bash install.sh
```

The installer will:
- Install missing system packages via `apt` — PyQt6, fuser, pactl, lsusb, notify-send
- Copy app files to `~/.local/share/permguard/`
- Create a `permguard` command at `~/.local/bin/permguard`
- Add PermGuard to your app menu

**3. Launch**
```bash
permguard
```
Or search **PermGuard** in your app menu / launcher.

> If the `permguard` command is not found right after install, close and reopen your terminal — the PATH update needs a new shell session.

### Uninstall

```bash
bash install.sh --uninstall
```

Removes all files, the command, and the app menu entry. Your saved permission rules in `~/.local/share/permguard/` are also deleted.

---

## Requirements

Handled automatically by the installer.

| Dependency | Used for |
|---|---|
| Python 3.10+ | Runtime |
| PyQt6 | GUI |
| `pactl` (pulseaudio-utils) | Microphone monitoring and control |
| `fuser` (psmisc) | Camera device detection |
| `lsof` | Camera detection fallback |
| `ss` (iproute2) | Network connections and open ports |
| `lsusb` (usbutils) | USB device listing |
| `notify-send` (libnotify-bin) | Desktop notifications |
| `pkexec` | Elevated camera blocking (optional) |

**Tested on:** Parrot OS 7.1 (KDE Plasma 6.3, Wayland)
**Should work on:** Any Debian/Ubuntu-based distro with KDE or GNOME

---

## Contributing

1. Fork the repo
2. Create a branch: `git checkout -b feature/my-feature`
3. Commit your changes
4. Open a Pull Request

Bug reports and feature requests welcome via [GitHub Issues](https://github.com/Ahmedouyahya/PermGuard/issues).

---

## Roadmap

- [ ] eBPF pre-emptive blocking (intercept before first frame)
- [ ] Per-app network firewall (allow/block internet per process)
- [ ] Clipboard access monitoring
- [ ] Flatpak sandbox integration
- [ ] GNOME / GTK theme support
- [ ] Notification history viewer

---

## License

**MIT License** — free to use, modify, and distribute. See [LICENSE](LICENSE) for the full text.


---

<div align="center">

**α ≈ 1/137**

*The fine-structure constant — the number that governs how light and matter interact.*
*Some things in the universe just control everything quietly in the background.*

</div>
