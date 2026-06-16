#!/bin/bash
# Run once on a fresh Ubuntu install to set up the workspace
set -e
DIR="$(dirname "$0")/.."

echo "=== Installing dependencies ==="
sudo apt-get update -qq
sudo apt-get install -y sshpass rsync nodejs npm

echo "=== Installing Node packages ==="
cd "$DIR" && npm install

echo "=== Installing Vercel CLI ==="
npm install -g vercel

echo "=== Linking Vercel project ==="
cd "$DIR"
# Copy the project.json from the previous deploy which already has the correct IDs
if [ -f /home/linux/merged/final_webapp/.vercel/project.json ]; then
  mkdir -p .vercel
  cp /home/linux/merged/final_webapp/.vercel/project.json .vercel/project.json
  echo "  Copied .vercel/project.json from existing deploy"
else
  echo "  WARNING: Run 'vercel link' manually to link this project"
fi

echo "=== Making scripts executable ==="
chmod +x "$DIR"/scripts/*.sh

echo ""
echo "Setup complete. Next steps:"
echo "  1. Run: vercel whoami   (log in if needed)"
echo "  2. Run: scripts/deploy-frontend-vercel.sh"
echo "  3. Run: scripts/deploy-backend-kvm.sh"
