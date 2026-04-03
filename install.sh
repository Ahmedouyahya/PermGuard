#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# PermGuard Installer
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

# ── Python check ──────────────────────────────────────────────────────────────
info "Checking Python 3.10+..."
PYTHON=$(command -v python3 || true)
[[ -z "$PYTHON" ]] && fail "python3 not found. Run: sudo apt install python3"
PY_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
IFS='.' read -r MAJ MIN <<< "$PY_VER"
[[ "$MAJ" -lt 3 || ( "$MAJ" -eq 3 && "$MIN" -lt 10 ) ]] && \
    fail "Python 3.10+ required, found $PY_VER"
ok "Python $PY_VER"

# ── System packages ───────────────────────────────────────────────────────────
info "Installing system dependencies..."
PKGS=()
command -v fuser       &>/dev/null || PKGS+=(psmisc)
command -v lsusb       &>/dev/null || PKGS+=(usbutils)
command -v ss          &>/dev/null || PKGS+=(iproute2)
command -v pactl       &>/dev/null || PKGS+=(pulseaudio-utils)
command -v notify-send &>/dev/null || PKGS+=(libnotify-bin)
python3 -c "import PyQt6" 2>/dev/null || PKGS+=(python3-pyqt6)

if [[ ${#PKGS[@]} -gt 0 ]]; then
    info "sudo apt install -y ${PKGS[*]}"
    sudo apt install -y "${PKGS[@]}" || warn "Some packages failed — continuing"
fi
ok "System dependencies ready"

# ── Copy app files ────────────────────────────────────────────────────────────
info "Installing PermGuard to $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"
# Copy the package (exclude __pycache__ and .pyc files)
rsync -a --delete \
    --exclude="__pycache__" --exclude="*.pyc" --exclude="*.pyo" \
    "$SCRIPT_DIR/permguard/" "$INSTALL_DIR/permguard/"
cp "$SCRIPT_DIR/run.py" "$INSTALL_DIR/"
# Copy assets
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
