#!/usr/bin/env bash
# Update kode + restart — satu perintah.
set -euo pipefail
cd "$(dirname "$0")"

SERVICE_NAME="telegram-drive-web"
SERVICE_UNIT="/etc/systemd/system/${SERVICE_NAME}.service"
APP_PORT=14202
HOST=127.0.0.1

if [[ -f .env ]]; then
  # shellcheck disable=SC1091
  set -a
  source .env 2>/dev/null || true
  set +a
  APP_PORT="${APP_PORT:-14202}"
  HOST="${HOST:-127.0.0.1}"
fi

echo "=== Update Telegram Drive Web ==="

if [[ -d .git ]]; then
  git pull --ff-only || git pull
else
  echo "(bukan git repo — lewati pull)"
fi

if [[ -d .venv ]]; then
  .venv/bin/pip install -q -r requirements.txt
  if [[ -f scripts/create_admin.py ]]; then
    .venv/bin/python scripts/create_admin.py || true
  fi
fi

have_service() {
  [[ -f "$SERVICE_UNIT" ]] && return 0
  SYSTEMD_COLORS=0 systemctl --no-pager list-unit-files "${SERVICE_NAME}.service" 2>/dev/null \
    | grep -qF "${SERVICE_NAME}.service"
}

if have_service; then
  systemctl daemon-reload
  systemctl restart "${SERVICE_NAME}"
  sleep 2
  if systemctl is-active --quiet "${SERVICE_NAME}"; then
    echo "OK — ${SERVICE_NAME} restarted (active)"
    curl -sf "http://${HOST}:${APP_PORT}/health" && echo ""
  else
    echo "GAGAL — service tidak active. Cek:"
    echo "  journalctl -u ${SERVICE_NAME} -n 40 --no-pager"
    systemctl status "${SERVICE_NAME}" --no-pager -l || true
    exit 1
  fi
elif systemctl restart "${SERVICE_NAME}" 2>/dev/null; then
  sleep 2
  echo "OK — ${SERVICE_NAME} restarted"
  curl -sf "http://${HOST}:${APP_PORT}/health" && echo ""
else
  echo "Service systemd tidak terdeteksi."
  echo "  Unit file: ${SERVICE_UNIT}"
  echo "  Coba: sudo bash install-vps.sh"
  echo "  Atau manual: systemctl restart ${SERVICE_NAME}"
  exit 1
fi