#!/usr/bin/env bash
# Sekali jalan di VPS — install native (systemd), tanpa Docker.
set -euo pipefail
cd "$(dirname "$0")"

APP_DIR="$(pwd)"
SERVICE_NAME="telegram-drive-web"
PORT="${APP_PORT:-14202}"
HOST="${HOST:-127.0.0.1}"
LOG_FILE="${APP_DIR}/install.log"
RESULT_FILE="${APP_DIR}/install-result.txt"

exec > >(tee -a "$LOG_FILE") 2>&1

echo "=== Telegram Drive Web — install VPS (native) ==="
echo "Log: ${LOG_FILE}"

find_python() {
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return
  fi
  for candidate in \
    /www/server/panel/pyenv/bin/python3.12 \
    /usr/bin/python3 \
    /usr/local/bin/python3; do
    if [[ -x "$candidate" ]]; then
      echo "$candidate"
      return
    fi
  done
  echo "python3 tidak ditemukan — install python3 atau aaPanel pyenv" >&2
  exit 1
}

PYTHON="$(find_python)"
echo "Python: ${PYTHON}"

# Stop container lama
docker rm -f telegram-drive-web telegram-drive-app tg-web-drive 2>/dev/null || true
docker compose down 2>/dev/null || true

# .env (pertahankan SECRET_KEY dari web-app lama jika ada)
if [[ ! -f .env ]]; then
  cp .env.example .env
  if [[ -f ../web-app/.env ]] && grep -q '^SECRET_KEY=' ../web-app/.env; then
    old_sk="$(grep '^SECRET_KEY=' ../web-app/.env | cut -d= -f2-)"
    sed -i "s/^SECRET_KEY=.*/SECRET_KEY=${old_sk}/" .env
    echo "SECRET_KEY diambil dari web-app/.env"
  fi
fi
if ! grep -q '^SECRET_KEY=.\+' .env 2>/dev/null; then
  sk="$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | xxd -p)"
  if grep -q '^SECRET_KEY=' .env; then
    sed -i "s/^SECRET_KEY=.*/SECRET_KEY=${sk}/" .env
  else
    echo "SECRET_KEY=${sk}" >> .env
  fi
fi
grep -q '^APP_PORT=' .env || echo "APP_PORT=${PORT}" >> .env
grep -q '^HOST=' .env || echo "HOST=${HOST}" >> .env
sed -i "s/^APP_PORT=.*/APP_PORT=${PORT}/" .env
sed -i "s/^HOST=.*/HOST=${HOST}/" .env

# Migrasi session dari web-app lama
mkdir -p data/sessions
if [[ -d ../web-app/data/sessions ]]; then
  echo "Migrasi session dari web-app..."
  cp -an ../web-app/data/sessions/. data/sessions/ 2>/dev/null || true
fi

# Python venv
if [[ ! -d .venv ]]; then
  "$PYTHON" -m venv .venv
fi
if command -v apt-get >/dev/null 2>&1; then
  apt-get install -y -qq ffmpeg 2>/dev/null || true
fi

.venv/bin/pip install -q -U pip
.venv/bin/pip install -q -r requirements.txt

# systemd
UNIT="/etc/systemd/system/${SERVICE_NAME}.service"
cat > "$UNIT" <<EOF
[Unit]
Description=Telegram Drive Web (native)
After=network.target

[Service]
Type=simple
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
Environment=DATA_DIR=${APP_DIR}/data
ExecStart=${APP_DIR}/.venv/bin/uvicorn backend.main:app --host ${HOST} --port ${PORT}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

sleep 2
{
  echo "=== install-result $(date -Iseconds) ==="
  echo "systemctl: $(systemctl is-active "${SERVICE_NAME}" 2>&1 || true)"
  echo "health:"
  curl -s "http://${HOST}:${PORT}/health" || echo "(curl health gagal)"
  echo ""
  echo "docker (harus kosong untuk telegram-drive):"
  docker ps --format '{{.Names}}' 2>/dev/null | grep -E 'telegram-drive-web|telegram-drive-app' || echo "(tidak ada container telegram-drive)"
} | tee "$RESULT_FILE"

if systemctl is-active --quiet "${SERVICE_NAME}"; then
  echo ""
  echo "OK — service aktif: ${SERVICE_NAME}"
  echo "  Health: curl -s http://${HOST}:${PORT}/health"
  curl -sf "http://${HOST}:${PORT}/health" && echo ""
  echo "  URL: https://telegram-drive.argtgbgt.tech (via nginx)"
  echo "  Hasil lengkap: ${RESULT_FILE}"
  echo ""
  echo "Update nanti: bash update.sh"
  echo "Restart cepat: bash restart.sh"
else
  echo "GAGAL — cek: journalctl -u ${SERVICE_NAME} -n 40 --no-pager"
  exit 1
fi