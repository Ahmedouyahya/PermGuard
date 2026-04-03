#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# PermGuard Installer — works on Debian/Ubuntu, Fedora/RHEL, Arch, openSUSE
# Usage:
#   bash install.sh              # install for current user
#   bash install.sh --uninstall  # remove PermGuard
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

INSTALL_DIR="$HOME/.local/share/permguard"
BIN_DIR="$HOME/.local/bin"
APPS_DIR="$HOME/.local/share/applications"

G='\033[0;32m'; B='\033[0;34m'; Y='\033[1;33m'; R='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${G}[✓]${NC} $*"; }
info() { echo -e "${B}[→]${NC} $*"; }
warn() { echo -e "${Y}[!]${NC} $*"; }
fail() { echo -e "${R}[✗]${NC} $*" >&2; exit 1; }

# ── Uninstall ─────────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--uninstall" ]]; then
    info "Removing PermGuard..."
    systemctl --user stop    permguard.service 2>/dev/null || true
    systemctl --user disable permguard.service 2>/dev/null || true
    rm -f   "$HOME/.config/systemd/user/permguard.service"
    systemctl --user daemon-reload 2>/dev/null || true
    rm -rf  "$INSTALL_DIR"
    rm -f   "$BIN_DIR/permguard"
    rm -f   "$APPS_DIR/permguard.desktop"
    rm -f   "$HOME/.config/autostart/permguard.desktop"
    update-desktop-database "$APPS_DIR" 2>/dev/null || true
    ok "PermGuard removed."
    exit 0
fi

# ── Banner ────────────────────────────────────────────────────────────────────
echo -e "${B}"
echo "  ╔══════════════════════════════════════╗"
echo "  ║   PermGuard — Privacy Manager        ║"
echo "  ║   Android-like permissions for Linux ║"
echo "  ╚══════════════════════════════════════╝"
echo -e "${NC}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Pull latest from GitHub (if this is a git repo) ──────────────────────────
if [[ -d "$SCRIPT_DIR/.git" ]]; then
    info "Pulling latest version from GitHub..."
    if git -C "$SCRIPT_DIR" pull --ff-only 2>&1; then
        ok "Up to date"
    else
        warn "Git pull failed — installing from local files"
    fi
fi

# ── Detect package manager ────────────────────────────────────────────────────
detect_pm() {
    if   command -v apt-get &>/dev/null; then echo "apt"
    elif command -v dnf     &>/dev/null; then echo "dnf"
    elif command -v pacman  &>/dev/null; then echo "pacman"
    elif command -v zypper  &>/dev/null; then echo "zypper"
    else echo "unknown"
    fi
}

install_pkg() {
    # $1 = apt name, $2 = dnf name, $3 = pacman name, $4 = zypper name
    local apt_n="$1" dnf_n="$2" pac_n="$3" zyp_n="$4"
    case "$PM" in
        apt)    sudo apt-get install -y "$apt_n" ;;
        dnf)    sudo dnf install -y "$dnf_n" ;;
        pacman) sudo pacman -S --noconfirm "$pac_n" ;;
        zypper) sudo zypper install -y "$zyp_n" ;;
        *)      warn "Unknown package manager — install $apt_n manually"; return 1 ;;
    esac
}

PM=$(detect_pm)
ok "Package manager: $PM"

# ── Python check ──────────────────────────────────────────────────────────────
info "Checking Python 3.10+..."
PYTHON=$(command -v python3 || true)
if [[ -z "$PYTHON" ]]; then
    warn "python3 not found — trying to install..."
    install_pkg python3 python3 python python3 || fail "Cannot install python3"
    PYTHON=$(command -v python3)
fi
PY_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
IFS='.' read -r MAJ MIN <<< "$PY_VER"
[[ "$MAJ" -lt 3 || ( "$MAJ" -eq 3 && "$MIN" -lt 10 ) ]] && \
    fail "Python 3.10+ required, found $PY_VER"
ok "Python $PY_VER"

# ── System packages ───────────────────────────────────────────────────────────
info "Installing system dependencies..."

install_if_missing() {
    local cmd="$1"; shift   # command to check
    local apt="$1" dnf="$2" pac="$3" zyp="$4"
    if ! command -v "$cmd" &>/dev/null; then
        info "Installing $cmd ($apt / $dnf / $pac / $zyp)..."
        install_pkg "$apt" "$dnf" "$pac" "$zyp" || warn "Could not install $cmd — some features may be unavailable"
    fi
}

install_if_missing ss        iproute2          iproute          iproute2          iproute2
install_if_missing pactl     pulseaudio-utils  pulseaudio-utils pulseaudio        pulseaudio-utils
install_if_missing lsusb     usbutils          usbutils         usbutils          usbutils
install_if_missing notify-send libnotify-bin   libnotify        libnotify         libnotify-tools

# PyQt6
if ! python3 -c "import PyQt6" 2>/dev/null; then
    info "Installing PyQt6..."
    case "$PM" in
        apt)    sudo apt-get install -y python3-pyqt6 python3-pyqt6.qtsvg 2>/dev/null || \
                    warn "PyQt6 not in apt — try: pip install PyQt6 --break-system-packages" ;;
        dnf)    sudo dnf install -y python3-pyqt6 2>/dev/null || \
                    warn "PyQt6 not in dnf — try: pip install PyQt6" ;;
        pacman) sudo pacman -S --noconfirm python-pyqt6 2>/dev/null || \
                    warn "PyQt6 install failed" ;;
        zypper) sudo zypper install -y python3-PyQt6 2>/dev/null || \
                    warn "PyQt6 install failed" ;;
        *)      warn "Install PyQt6 manually: pip install PyQt6" ;;
    esac
fi

# iptables or nftables (for firewall tab)
if ! command -v iptables &>/dev/null && ! command -v nft &>/dev/null; then
    install_if_missing iptables iptables iptables iptables iptables || true
fi

ok "System dependencies ready"

# ── Copy app files ────────────────────────────────────────────────────────────
info "Installing PermGuard to $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"
rsync -a --delete \
    --exclude="__pycache__" --exclude="*.pyc" --exclude="*.pyo" \
    "$SCRIPT_DIR/permguard/" "$INSTALL_DIR/permguard/"
cp "$SCRIPT_DIR/run.py" "$INSTALL_DIR/"
rsync -a "$SCRIPT_DIR/assets/" "$INSTALL_DIR/assets/"
ok "Files copied"

# ── Create launcher script ────────────────────────────────────────────────────
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/permguard" <<EOF
#!/usr/bin/env python3
import sys
sys.path.insert(0, "$INSTALL_DIR")
from permguard.main import main
main()
EOF
chmod +x "$BIN_DIR/permguard"
ok "Command created: permguard"

# ── Ensure ~/.local/bin is in PATH ────────────────────────────────────────────
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    warn "$BIN_DIR is not in your PATH yet."
    for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile"; do
        if [[ -f "$rc" ]]; then
            echo '' >> "$rc"
            echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$rc"
            warn "Added PATH export to $rc — run: source $rc"
            break
        fi
    done
fi

# ── Desktop entry ─────────────────────────────────────────────────────────────
info "Creating app menu entry..."
mkdir -p "$APPS_DIR"
cat > "$APPS_DIR/permguard.desktop" <<EOF
[Desktop Entry]
Name=PermGuard
GenericName=Privacy Manager
Comment=Android-like permission manager — control camera, mic, and network access
Exec=$BIN_DIR/permguard
Icon=$INSTALL_DIR/assets/icon.svg
Terminal=false
Type=Application
Categories=System;Security;Settings;
Keywords=privacy;camera;microphone;permissions;security;
StartupWMClass=permguard
StartupNotify=true
EOF
update-desktop-database "$APPS_DIR" 2>/dev/null || true
ok "App menu entry created"

# ── Systemd user service (auto-start + auto-restart on crash) ─────────────────
info "Installing systemd user service..."
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
mkdir -p "$SYSTEMD_USER_DIR"
sed "s|%h|$HOME|g" "$SCRIPT_DIR/permguard.service" > "$SYSTEMD_USER_DIR/permguard.service"

if systemctl --user daemon-reload 2>/dev/null; then
    systemctl --user enable permguard.service 2>/dev/null || true
    ok "Systemd service installed and enabled (auto-starts at login)"
    info "Control: systemctl --user start|stop|restart|status permguard"
else
    warn "systemd user session not available — falling back to .desktop autostart"
    AUTOSTART_DIR="$HOME/.config/autostart"
    mkdir -p "$AUTOSTART_DIR"
    cat > "$AUTOSTART_DIR/permguard.desktop" <<EOF
[Desktop Entry]
Name=PermGuard
Comment=PermGuard privacy monitor
Exec=$BIN_DIR/permguard
Icon=$INSTALL_DIR/assets/icon.svg
Terminal=false
Type=Application
X-KDE-autostart-after=panel
X-GNOME-Autostart-enabled=true
EOF
    ok "Autostart enabled via .desktop fallback"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${G}  ╔══════════════════════════════════════╗"
echo    "  ║       Installation complete!         ║"
echo    "  ╚══════════════════════════════════════╝${NC}"
echo ""
echo "  Launch from terminal :  permguard"
echo "  Launch from app menu :  search 'PermGuard'"
echo "  Uninstall            :  bash $SCRIPT_DIR/install.sh --uninstall"
echo ""
