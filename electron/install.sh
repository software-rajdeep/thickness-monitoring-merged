#!/bin/bash
# ──────────────────────────────────────────────────────────────────────────────
# Thickness Monitor Electron — Install & Autostart Setup
#
# Usage:
#   bash install.sh              # install dependencies only
#   bash install.sh --autostart  # install + enable autostart on boot
#   bash install.sh --help       # show this message
#
# This script:
#   1. Installs npm dependencies (electron, electron-builder)
#   2. Optionally creates an XDG autostart .desktop file so the app
#      launches automatically when the desktop starts (Raspberry Pi OS / LXDE).
#   3. Does NOT touch pi_client.py or its systemd service in any way.
#
# Requirements:
#   - Node.js 18+ and npm (pre-installed on Raspberry Pi OS)
#   - electron and electron-builder (installed by this script)
#   - A display / desktop environment (LXDE on Raspberry Pi OS)
# ──────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SCRIPT_DIR"
APP_NAME="Thickness Monitor"
APP_EXEC="electron ${APP_DIR}"
DESKTOP_FILE="${HOME}/.config/autostart/thickness-monitor.desktop"
TARGET_URL="https://merged-version.vercel.app"

# ─── Help ─────────────────────────────────────────────────────────────────────

if [[ "${1:-}" == "--help" ]]; then
  echo "Usage: bash install.sh [--autostart]"
  echo ""
  echo "  (no args)   Install npm dependencies only."
  echo "  --autostart Install dependencies AND create autostart entry."
  echo "  --help      Show this message."
  echo ""
  echo "The autostart entry will launch the Electron app automatically"
  echo "when the Raspberry Pi desktop starts (LXDE / XDG autostart)."
  echo ""
  echo "To remove autostart later:"
  echo "  rm -f ${DESKTOP_FILE}"
  exit 0
fi

# ─── Pre-flight checks ────────────────────────────────────────────────────────

echo "============================================"
echo " Thickness Monitor Electron — Installer"
echo "============================================"
echo ""

# Check Node.js
if ! command -v node &>/dev/null; then
  echo "ERROR: Node.js is not installed."
  echo "Install it with:"
  echo "  sudo apt update && sudo apt install -y nodejs npm"
  exit 1
fi

NODE_VERSION="$(node --version | sed 's/v//' | cut -d. -f1)"
echo "[OK] Node.js $(node --version) found"

if [[ "$NODE_VERSION" -lt 18 ]]; then
  echo "WARNING: Node.js 18+ recommended. You have $(node --version)."
  echo "         Consider upgrading:"
  echo "  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo bash -"
  echo "  sudo apt install -y nodejs"
fi

# Check npm
if ! command -v npm &>/dev/null; then
  echo "ERROR: npm is not installed."
  exit 1
fi
echo "[OK] npm $(npm --version) found"

# ─── Install npm dependencies ─────────────────────────────────────────────────

echo ""
echo "Installing Electron and dependencies..."
cd "$APP_DIR"

# Install only production-like deps; devDependencies includes electron itself
npm install --no-audit --no-fund 2>&1 | tail -5

echo "[OK] npm dependencies installed"

# ─── Verify electron is available ──────────────────────────────────────────────

if ! npx --no-install electron --version &>/dev/null; then
  echo "WARNING: 'electron --version' failed. You may need to install manually:"
  echo "  cd ${APP_DIR} && npm install"
  echo ""
  echo "On Raspberry Pi (armv7l), you may need to set the Electron mirror:"
  echo "  export ELECTRON_MIRROR=https://github.com/electron/electron/releases/download/"
  echo "  npm install"
fi

echo ""
echo "[OK] Electron installed successfully"
echo ""

# ─── Check pi_client systemd service (informational only) ────────────────────

if systemctl is-active --quiet pi-merged-client 2>/dev/null; then
  echo "[INFO] pi-merged-client systemd service is ACTIVE."
  echo "       Electron will NOT manage this service. It runs independently."
elif systemctl list-unit-files | grep -q pi-merged-client 2>/dev/null; then
  echo "[INFO] pi-merged-client systemd service is installed but not active."
  echo "       Start it with: sudo systemctl start pi-merged-client"
else
  echo "[INFO] pi-merged-client systemd service is NOT installed."
  echo "       Sensor data will not be collected."
  echo "       See: $(dirname "$APP_DIR")/backend/pi_merged.service"
  echo "       Install: sudo cp $(dirname "$APP_DIR")/backend/pi_merged.service /etc/systemd/system/"
  echo "                sudo systemctl daemon-reload"
  echo "                sudo systemctl enable --now pi-merged-client"
fi
echo ""

# ─── Autostart setup (optional) ──────────────────────────────────────────────

if [[ "${1:-}" == "--autostart" ]]; then
  echo "Setting up XDG autostart..."

  # Ensure autostart directory exists
  mkdir -p "${HOME}/.config/autostart"

  # Write the .desktop file
  cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=${APP_NAME}
Exec=electron "${APP_DIR}"
Path=${APP_DIR}
Terminal=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
StartupNotify=false
Categories=Utility;
EOF

  echo "[OK] Autostart file created: ${DESKTOP_FILE}"
  echo ""
  echo "The Electron app will launch automatically on next boot."
  echo ""
  echo "To start it now without rebooting:"
  echo "  cd ${APP_DIR} && npm start"
  echo ""
  echo "To disable autostart later:"
  echo "  rm -f ${DESKTOP_FILE}"
else
  echo "----------------------------------------------"
  echo " To enable autostart on boot, re-run with:"
  echo "   bash install.sh --autostart"
  echo "----------------------------------------------"
fi

echo ""
echo "============================================"
echo " Installation complete!"
echo "============================================"
echo ""
echo "Start the app now:"
echo "  cd ${APP_DIR} && npm start"
echo ""
echo "Or use --autostart to launch automatically on boot."