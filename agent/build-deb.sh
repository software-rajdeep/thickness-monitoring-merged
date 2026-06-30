#!/usr/bin/env bash
#
# Build a SHIPPABLE Thickness Agent .deb in one command.
#
#   ./build-deb.sh                 # version 1.0.0, arch = this machine's arch
#   ./build-deb.sh 1.2.0           # custom version
#
# Run this ON the target architecture (PyInstaller does not cross-compile):
#   - amd64  -> run on a Linux x86_64 PC   (e.g. the Ubuntu dev PC)
#   - arm64  -> run on a 64-bit Raspberry Pi
#   - armhf  -> run on a 32-bit Raspberry Pi
#
# Output:  thickness-agent_<version>_<arch>.deb
# Install: sudo dpkg -i thickness-agent_<version>_<arch>.deb   (service auto-starts)
#
set -euo pipefail
cd "$(dirname "$0")"

VERSION="${1:-1.0.0}"
ARCH="$(dpkg --print-architecture)"
echo ">> Building thickness-agent ${VERSION} for ${ARCH}"

# 1. Compile the single-file binary in an isolated venv ----------------------
if [ ! -d .build-venv ]; then
  python3 -m venv .build-venv
fi
./.build-venv/bin/pip install --quiet --upgrade pip pyinstaller requests
rm -rf build dist
./.build-venv/bin/pyinstaller --onefile --name thickness-agent \
  --hidden-import requests thickness_agent.py >/dev/null
echo ">> Binary built: dist/thickness-agent"

# 2. Assemble the package tree ------------------------------------------------
PKG="$(mktemp -d)"
trap 'rm -rf "$PKG"' EXIT
chmod 0755 "$PKG"
mkdir -p "$PKG/DEBIAN" \
         "$PKG/opt/thickness-agent" \
         "$PKG/etc/systemd/system" \
         "$PKG/etc/thickness-agent"

cp packaging/DEBIAN/control  "$PKG/DEBIAN/control"
cp packaging/DEBIAN/postinst "$PKG/DEBIAN/postinst"
cp packaging/DEBIAN/prerm    "$PKG/DEBIAN/prerm"
# stamp the requested version + the architecture we're actually building for
sed -i "s/^Version:.*/Version: ${VERSION}/" "$PKG/DEBIAN/control"
sed -i "s/^Architecture:.*/Architecture: ${ARCH}/" "$PKG/DEBIAN/control"
chmod 0755 "$PKG/DEBIAN/postinst" "$PKG/DEBIAN/prerm"

install -m 0755 dist/thickness-agent      "$PKG/opt/thickness-agent/thickness-agent"
install -m 0644 thickness-agent.service   "$PKG/etc/systemd/system/thickness-agent.service"

# 3. Build the .deb -----------------------------------------------------------
OUT="thickness-agent_${VERSION}_${ARCH}.deb"
dpkg-deb --build --root-owner-group "$PKG" "$OUT" >/dev/null
echo ""
echo ">> Built: $OUT"
echo ">> Install on the customer machine:"
echo "     sudo dpkg -i $OUT"
echo "   then either open http://localhost:7000 (wizard) or pre-seed"
echo "   /etc/thickness-agent/agent.env (see agent.env.example) and restart."
