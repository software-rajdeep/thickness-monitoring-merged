#!/bin/bash
# Deploy frontend to Vercel (finalwebapp.vercel.app)
set -e
cd "$(dirname "$0")/.."

echo "=== Building frontend ==="
NODE_TLS_REJECT_UNAUTHORIZED=0 vercel build --prod --yes

echo "=== Deploying to Vercel ==="
NODE_TLS_REJECT_UNAUTHORIZED=0 vercel deploy --prebuilt --prod --yes

echo "=== Done: https://finalwebapp.vercel.app ==="
