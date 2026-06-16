#!/bin/bash
# Deploy everything: backend to KVM + frontend to both KVM and Vercel
set -e
DIR="$(dirname "$0")"

echo "=============================="
echo " FULL DEPLOY"
echo "=============================="

bash "$DIR/deploy-backend-kvm.sh"
bash "$DIR/deploy-frontend-kvm.sh"
bash "$DIR/deploy-frontend-vercel.sh"

echo ""
echo "=============================="
echo " All done!"
echo "  KVM:    http://194.164.148.145:8082"
echo "  Vercel: https://finalwebapp.vercel.app"
echo "=============================="
