#!/bin/bash
# Sync backend to KVM and restart the Flask service
set -e
KVM_HOST="194.164.148.145"
KVM_USER="root"
KVM_PASS="Federer7roger@"
KVM_BACKEND="/opt/merged/backend"
SERVICE="merged"

cd "$(dirname "$0")/.."

echo "=== Syncing backend/ to KVM ==="
sshpass -p "$KVM_PASS" rsync -avz \
  --exclude '__pycache__' --exclude '*.pyc' --exclude '*.pyc' \
  backend/ ${KVM_USER}@${KVM_HOST}:${KVM_BACKEND}/

echo "=== Restarting $SERVICE on KVM ==="
sshpass -p "$KVM_PASS" ssh -o StrictHostKeyChecking=no \
  ${KVM_USER}@${KVM_HOST} "systemctl restart ${SERVICE} && systemctl status ${SERVICE} --no-pager -l | head -15"

echo "=== Backend deployed ==="
