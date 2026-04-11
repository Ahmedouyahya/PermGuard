# 🛡 PermGuard

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Linux-lightgrey.svg)]()
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)]()
[![Version](https://img.shields.io/badge/version-0.4.0-green.svg)]()

**Android-like privacy & permission manager for Linux**

PermGuard watches your system in real-time. The moment an unknown app tries to use your **camera**, **microphone**, **files**, or **install software**, it is immediately **frozen** and a popup asks you what to do — just like on Android. You choose, PermGuard remembers. Your rules survive reboots.

---

## Install

```bash
git clone https://github.com/Ahmedouyahya/PermGuard.git && cd PermGuard && bash install.sh
```

The installer detects your distro (Debian/Ubuntu, Fedora, Arch, openSUSE), installs all dependencies, and sets up a systemd user service so PermGuard starts automatically at every login.

```bash
permguard             # launch manually
python -m permguard   # equivalent — useful when the wrapper isn't on PATH
permguard --version
```

**Update to latest version:**
```bash
bash install.sh    # always pulls from GitHub first
```

**Uninstall:**
```bash
bash install.sh --uninstall
```

---

## How it works

When an unknown app accesses a protected resource:

```
App opens /dev/video0  ──▶  CameraMonitor detects it
                                     │
                            SIGSTOP (freeze app)
                                     │
                            ┌────────▼────────┐
                            │  📷 Firefox      │
                            │  wants your      │
                            │  camera          │
                            │  Frozen...       │
                            │                  │
                            │  [Allow]         │
                            │  [Allow once]    │
                            │  [Deny]          │
                            └──────────────────┘
                                     │
                    ┌────────────────┼──────────────────┐
                    ▼                ▼                   ▼
                 Allow           Allow once            Deny
               SIGCONT           SIGCONT             SIGKILL
             (save rule)       (no rule)            (save rule)
```

The app is **completely frozen** while you decide — it cannot read a single frame, audio sample, or file byte until you respond. If you don't respond within 30 seconds, access is automatically denied.

---

## Features

| Tab | What it does |
|---|---|
| 🏠 Dashboard | Live overview of all categories + one-click camera/mic block |
| 📷 Camera | Apps currently accessing the webcam, kill button |
| 🎤 Mic | Apps capturing audio via PipeWire/PulseAudio, kill button |
| 🖥 Screen | Active screen recording/sharing sessions |
| 🌐 Network | All active TCP/UDP connections per process, block button |
| 🔌 USB | Connected USB devices with enable/disable control |
| 🔒 Ports | Listening ports and owning processes |
| ⚙️ Processes | Top processes by CPU, kill button |
| 🔥 Firewall | Active network blocks per app (iptables) |
| 📂 Files | Protected directories — add paths, view access events |
| 🔑 Permissions | All saved allow/deny rules for camera, mic, screen, **filesystem**, and **package installs** — add rules manually, revoke any |
| ⚙ Settings | Autostart, refresh interval, event log |

### Permission flow

- **Camera & Mic** — detected via `/proc/<pid>/fd` symlinks and `pactl`. Frozen with SIGSTOP, resumed with SIGCONT or killed with SIGKILL.
- **File access** — inotify watches on protected directories (`~/.ssh`, `~/.gnupg` by default, user-configurable). Access detected via `/proc` fd scan.
- **Package installs** — `/proc` polled every 2s for `apt`, `pip`, `npm`, `snap`, `flatpak`, `pacman`, `dnf`, `cargo`, and 15+ other package managers.
- **Network blocking** — `iptables OUTPUT` rules keyed by UID. Persisted across reboots.
- **USB control** — reads/writes `/sys/bus/usb/devices/<id>/authorized` via pkexec.

### Persistence

All decisions survive reboots:

| File | What's stored |
|---|---|
| `~/.local/share/permguard/permissions.json` | Per-app allow/deny rules + notification/interval settings |
| `~/.local/share/permguard/firewall_rules.json` | Network blocks (re-applied at startup, de-duplicated against live iptables) |
| `~/.local/share/permguard/device_state.json` | Camera/mic block state (re-applied at startup) |
| `~/.local/share/permguard/timeline.json` | Last 7 days of access events for the Privacy Dashboard |
| `~/.local/share/permguard/events.log` | Full audit log (rotated at 5000 lines) |

Every state file is written **atomically** (temp file + `os.replace`) with `0o600` permissions, and the data directory itself is `0o700` — no other user on the machine can read your rules, log, or timeline.

### Reliability & security hardening

- **Zombie-proof dialogs** — if an app exits while its permission prompt is still on screen, PermGuard dismisses the stale dialog and thaws the frozen process automatically instead of leaving an orphaned popup.
- **Self-exclusion** — the file monitor skips its own PID and child processes so PermGuard can never freeze itself while reading its own config.
- **Firewall idempotency** — on restart, existing `iptables` DROP rules are detected with `iptables -C` and skipped, so restarting the service can no longer pile up duplicate rules.
- **Shell-injection safe** — privileged writes to sysfs (`/sys/bus/usb/.../authorized`) go through `pkexec tee` with the value on stdin; no untrusted device ID ever touches a shell.
- **Thread-safe timeline** — concurrent monitor threads write to the event timeline through an in-memory cache guarded by a lock, so events from simultaneous camera/mic/file accesses can't clobber each other.

### Systemd service

PermGuard runs as a proper systemd user service:

```bash
systemctl --user status permguard
systemctl --user stop permguard
systemctl --user restart permguard
```

---

## Performance

PermGuard is designed to consume as close to zero resources as possible when nothing is happening.

### Event-driven camera monitoring (inotify)

The camera monitor uses the Linux **inotify** kernel API instead of polling. It registers `IN_OPEN` watches on `/dev/video*` devices and blocks in `select()` until the kernel wakes it up — literally 0% CPU when no camera is in use. When a device is opened, it wakes up in under a millisecond, scans `/proc/<pid>/fd` to identify the process, and goes back to sleep.

### Direct kernel interface reads (no subprocesses)

Earlier versions spawned subprocesses for every data query. These were replaced with direct reads from Linux kernel interfaces:

| Data | Old method | New method | Latency |
|---|---|---|---|
| Camera PIDs | `fuser /dev/video*` | `/proc/<pid>/fd` symlink scan | 57ms → 9ms |
| Network connections | `ss -tunp` | `/proc/net/tcp` + `/proc/net/tcp6` | 18ms → 10ms |
| Open ports | `ss -tlnp` | `/proc/net/tcp` (filter LISTEN) | 28ms → 10ms |
| USB devices | — | `/sys/bus/usb/devices/*/authorized` | ~1ms |
| Microphone streams | `pactl` | `pactl` (kept — already 4ms) | 4ms |

Network parsing reads raw hex addresses directly from the kernel's TCP table and maps socket inodes to PIDs via `/proc/<pid>/fd` — no external tools needed.

### CPU profile at idle

| Monitor | Method | CPU when idle |
|---|---|---|
| Camera | inotify `IN_OPEN` | ~0% (blocked in select) |
| Microphone | pactl poll every 1s | ~0.1% |
| File access | inotify `IN_OPEN` | ~0% (blocked in select) |
| Package install | /proc poll every 2s | ~0.05% |
| UI refresh | 5s timer | ~0% |

---

## Known limitations

### File access — notification vs. enforcement

The file access monitor uses Linux `inotify`, which **notifies** after a file is opened, not before. This means:

- For most apps (slow reads, document files) SIGSTOP arrives fast enough to prevent meaningful access.
- For fast-reading apps, the first read may complete before the freeze lands.

**Why not enforce it like Android?**
Android enforces file access at the kernel level because every app runs under a unique UID and the kernel rejects the `open()` syscall before it completes. On Linux desktop, all your apps share the same UID — the kernel has no app-level boundary to enforce.

**The right solution is `fanotify FAN_OPEN_PERM`** — a Linux kernel API that holds the `open()` syscall suspended until a privileged daemon responds with allow or deny. This would give true pre-emptive blocking identical to Android. It requires `CAP_SYS_ADMIN` (root privileges) and a dedicated C helper process.

This is not implemented yet because:
1. It requires a privileged C daemon — significant complexity
2. It has a real performance cost: every `open()` in watched directories blocks on a round-trip to the daemon
3. On a busy system this could noticeably slow down file operations

It may be added in a future version as an opt-in feature for high-security use cases.

---

## Installation

### Supported distros

| Distro | Package manager | Status |
|---|---|---|
| Debian / Ubuntu / Parrot OS | apt | Fully tested |
| Fedora / RHEL | dnf | Supported |
| Arch Linux / Manjaro | pacman | Supported |
| openSUSE | zypper | Supported |

### Requirements

- Python 3.10+
- PyQt6
- `pactl` (pulseaudio-utils) — microphone monitoring
- `ss` (iproute2) — network connections
- `lsusb` (usbutils) — USB device listing
- `pkexec` — camera/USB privilege escalation (optional)

All installed automatically by `install.sh`.

---

## Architecture

```
permguard/
├── __main__.py                 ← `python -m permguard` entry point
├── main.py                     ← Wires monitors → UI, handles --version/--help
│
├── core/
│   ├── monitor.py              ← Background QThreads
│   │     CameraMonitor         ← inotify on /dev/video*, /proc scan
│   │     MicMonitor            ← pactl polling (1s)
│   │     FileMonitor           ← inotify on sensitive dirs, /proc scan
│   │     PackageInstallMonitor ← /proc polling for package managers
│   │
│   ├── permissions.py          ← JSON rule database (allow/deny/ask)
│   ├── data.py                 ← Kernel interface reads (/proc, /sys)
│   ├── system.py               ← kill_pid, camera/mic block, state persist
│   ├── firewall.py             ← iptables per-app network blocking
│   └── usb_control.py          ← sysfs USB authorized control
│
└── ui/
    ├── permission_dialog.py    ← Floating Android-style popup
    ├── main_window.py          ← Main window + 12 tabs
    ├── widgets.py              ← StatCard, PermTab, build_table
    └── styles.py               ← Nord dark theme
```

---

## Roadmap

- [ ] `fanotify FAN_OPEN_PERM` — true pre-emptive file blocking (opt-in, high-security mode)
- [ ] Clipboard access monitoring
- [ ] Per-app network rules (allow specific hosts, block others)
- [ ] Flatpak sandbox integration
- [ ] Notification history viewer
- [ ] App icon recognition in permission dialogs

---

## Contributing

Bug reports and feature requests: [GitHub Issues](https://github.com/Ahmedouyahya/PermGuard/issues)

1. Fork → branch → commit → Pull Request

---

## License

**MIT** — free to use, modify, and distribute. See [LICENSE](LICENSE).

---

<div align="center">

**α ≈ 1/137**

*The fine-structure constant — the number that governs how light and matter interact.*
*Some things in the universe just control everything quietly in the background.*

</div>
