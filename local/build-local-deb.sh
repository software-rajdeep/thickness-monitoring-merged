#!/usr/bin/env bash
#
# Build the SHIPPABLE thickness-local .deb — the fully offline appliance app.
#
#   ./build-local-deb.sh            # version 1.0.0, arch = this machine
#   ./build-local-deb.sh 1.1.0      # custom version
#
# Run ON the target architecture (PyInstaller does not cross-compile):
#   amd64 -> any Linux x86_64 PC (e.g. the Ubuntu dev PC)
#   arm64 -> a 64-bit Raspberry Pi
#
# What it does:
#   1. Builds the React frontend in "localapp" mode (same-origin API URLs —
#      no cloud server baked in).
#   2. Compiles backend/local_main.py + the whole backend into ONE binary
#      with PyInstaller (frontend dist + license public key bundled inside).
#   3. Assembles thickness-local_<version>_<arch>.deb with the systemd unit.
#
# Output:  thickness-local_<version>_<arch>.deb
# Install: sudo dpkg -i thickness-local_<version>_<arch>.deb
#
# NOTE: this overwrites ../dist with the LOCAL build of the frontend. Run a
# plain `npm run build` again before any CLOUD deploy (deploy.py).
#
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(cd .. && pwd)"

VERSION="${1:-1.0.0}"
ARCH="$(dpkg --print-architecture)"
echo ">> Building thickness-local ${VERSION} for ${ARCH}"

# Vite needs Node >= 20. Non-interactive shells don't source nvm — do it here.
if [ -s "$HOME/.nvm/nvm.sh" ]; then
  export NVM_DIR="$HOME/.nvm"
  . "$NVM_DIR/nvm.sh"
  nvm use default >/dev/null 2>&1 || nvm use node >/dev/null 2>&1 || true
fi
echo ">> Using node $(node --version)"

[ -f "$ROOT/backend/license_pubkey.txt" ] || {
  echo "!! backend/license_pubkey.txt missing — run tools/local_license_tool.py init first."; exit 1; }

# 1. Frontend (same-origin build) ---------------------------------------------
echo ">> Building frontend (localapp mode)…"
( cd "$ROOT" && npx vite build --mode localapp )

# 2. Single-file binary ---------------------------------------------------------
if [ ! -d .build-venv ]; then
  python3 -m venv .build-venv
fi
./.build-venv/bin/pip install --quiet --upgrade pip pyinstaller
./.build-venv/bin/pip install --quiet -r requirements-local.txt
rm -rf build dist
./.build-venv/bin/pyinstaller --onefile --name thickness-local \
  --paths "$ROOT/backend" \
  --add-data "$ROOT/dist:dist" \
  --add-data "$ROOT/backend/license_pubkey.txt:." \
  --hidden-import engineio.async_drivers.threading \
  --collect-all simple_websocket \
  "$ROOT/backend/local_main.py" >/dev/null
echo ">> Binary built: dist/thickness-local"

# 3. Package tree ---------------------------------------------------------------
PKG="$(mktemp -d)"
trap 'rm -rf "$PKG"' EXIT
chmod 0755 "$PKG"
mkdir -p "$PKG/DEBIAN" "$PKG/opt/thickness-local" "$PKG/etc/systemd/system"

cp packaging/DEBIAN/control  "$PKG/DEBIAN/control"
cp packaging/DEBIAN/postinst "$PKG/DEBIAN/postinst"
cp packaging/DEBIAN/prerm    "$PKG/DEBIAN/prerm"
sed -i "s/^Version:.*/Version: ${VERSION}/" "$PKG/DEBIAN/control"
sed -i "s/^Architecture:.*/Architecture: ${ARCH}/" "$PKG/DEBIAN/control"
chmod 0755 "$PKG/DEBIAN/postinst" "$PKG/DEBIAN/prerm"

install -m 0755 dist/thickness-local     "$PKG/opt/thickness-local/thickness-local"
install -m 0644 thickness-local.service  "$PKG/etc/systemd/system/thickness-local.service"

# 4. Build the .deb -------------------------------------------------------------
OUT="thickness-local_${VERSION}_${ARCH}.deb"
dpkg-deb --build --root-owner-group "$PKG" "$OUT" >/dev/null
echo ""
echo ">> Built: local/$OUT"
echo ">> Install on the appliance:  sudo dpkg -i $OUT"
echo ">> Then open http://<appliance-ip>/ to see the machine code and activate."
