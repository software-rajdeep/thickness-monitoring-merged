#!/usr/bin/env bash
# Build the Thickness Agent into a single self-contained binary.
# Run this ON the target OS/arch you want a binary for:
#   - Raspberry Pi (ARM): run on a Pi (or use an ARM builder)
#   - Linux x86_64: run on a Linux PC
#   - macOS: run on a Mac
# Produces dist/thickness-agent
set -euo pipefail
cd "$(dirname "$0")"

python3 -m pip install --upgrade pyinstaller requests

pyinstaller --onefile --name thickness-agent \
  --hidden-import requests \
  thickness_agent.py

echo
echo "Built: dist/thickness-agent"
echo "Install on the target machine:"
echo "  sudo mkdir -p /opt/thickness-agent /etc/thickness-agent"
echo "  sudo cp dist/thickness-agent /opt/thickness-agent/"
echo "  sudo cp thickness-agent.service /etc/systemd/system/"
echo "  sudo systemctl daemon-reload && sudo systemctl enable --now thickness-agent"
echo "  # then open http://localhost:7000 to run the setup wizard"
