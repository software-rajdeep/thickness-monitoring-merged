#!/bin/bash
# Build frontend and push dist/ to KVM so http://194.164.148.145:8082 updates
set -e
KVM_HOST="194.164.148.145"
KVM_USER="root"
KVM_PASS="Federer7roger@"
KVM_DIST="/opt/merged/dist"

cd "$(dirname "$0")/.."

echo "=== Building frontend ==="
npm run build

echo "=== Uploading dist/ to KVM ==="
sshpass -p "$KVM_PASS" rsync -avz --delete dist/ ${KVM_USER}@${KVM_HOST}:${KVM_DIST}/

echo "=== Done: http://194.164.148.145:8082 ==="
