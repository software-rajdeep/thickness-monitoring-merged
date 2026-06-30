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
#   3. Detects pi-merged-client systemd service (does NOT manage it).
#   4. Pins electron to a version known to work on Raspberry Pi OS.
# ──────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SCRIPT_DIR"
APP_NAME="Thickness Monitor"
DESKTOP_FILE="${HOME}/.config/autostart/thickness-monitor.desktop"

# ─── Help ─────────────────────────────────────────────────────────────────────

if [[ "${1:-}" == "--help" ]]; then
  echo "Usage: bash install.sh [--autostart]"
  echo ""
  echo "  (no args)   Install npm dependencies only."
  echo "  --autostart Install dependencies AND create autostart entry."
  echo "  --help      Show this message."
  exit 0
fi

echo "============================================"
echo " Thickness Monitor Electron — Installer"
echo "============================================"
echo ""

# Check Node.js
command -v node &>/dev/null || { echo "ERROR: Node.js not found"; exit 1; }
echo "[OK] Node.js $(node --version)"
command -v npm &>/dev/null || { echo "ERROR: npm not found"; exit 1; }
echo "[OK] npm $(npm --version)"

# Install dependencies (electron-builder is optional on Pi)
echo ""
echo "Installing Electron..."
cd "$APP_DIR"
npm install --no-audit --no-fund 2>&1 | tail -3

# Verify electron binary exists
if [ -f "node_modules/electron/dist/electron" ]; then
  echo "[OK] Electron binary installed"
else
  echo "WARNING: Electron binary not found. On Raspberry Pi, try:"
  echo "  export ELECTRON_MIRROR=https://github.com/electron/electron/releases/download/"
  echo "  npm install"
fi

# Check pi_client systemd service (informational only)
echo ""
if systemctl is-active --quiet pi-merged-client 2>/dev/null; then
  echo "[INFO] pi-merged-client service is ACTIVE (running independently)"
  echo "       Electron will NOT manage this service."
elif systemctl list-unit-files 2>/dev/null | grep -q pi-merged-client; then
  echo "[INFO] pi-merged-client service installed but not active"
  echo "       Start: sudo systemctl start pi-merged-client"
else
  echo "[INFO] pi-merged-client service NOT installed"
  echo "       See ../backend/pi_merged.service for setup instructions"
fi

# Autostart setup
if [[ "${1:-}" == "--autostart" ]]; then
  echo ""
  echo "Setting up XDG autostart..."
  mkdir -p "${HOME}/.config/autostart"
  cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=${APP_NAME}
Exec=${APP_DIR}/node_modules/.bin/electron ${APP_DIR}
Path=${APP_DIR}
Terminal=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
StartupNotify=false
Categories=Utility;
EOF
  echo "[OK] Autostart created: ${DESKTOP_FILE}"
  echo "     App will launch on next boot."
fi

echo ""
echo "============================================"
echo " Installation complete!"
echo "============================================"
echo ""
echo "Start now:"
echo "  cd ${APP_DIR} && npm start"
echo ""
echo "Enable autostart on boot:"
echo "  bash install.sh --autostart"