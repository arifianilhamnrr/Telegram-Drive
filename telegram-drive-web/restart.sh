#!/usr/bin/env bash
# Restart cepat tanpa pip install.
set -euo pipefail
SERVICE_NAME="telegram-drive-web"
APP_PORT=14202
HOST=127.0.0.1

cd "$(dirname "$0")"
[[ -f .env ]] && source .env 2>/dev/null || true

systemctl daemon-reload
systemctl restart "${SERVICE_NAME}"
sleep 2
systemctl is-active --quiet "${SERVICE_NAME}" && echo "OK — ${SERVICE_NAME} active"
curl -sf "http://${HOST:-127.0.0.1}:${APP_PORT:-14202}/health" && echo ""