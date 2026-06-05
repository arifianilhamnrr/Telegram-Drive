#!/usr/bin/env bash
# Jalankan di laptop/PC — tanpa Docker. Buka http://127.0.0.1:8080
set -euo pipefail
cd "$(dirname "$0")"

PORT="${APP_PORT:-8080}"
HOST="${HOST:-127.0.0.1}"

if [[ ! -d .venv ]]; then
  echo "Membuat virtualenv..."
  python3 -m venv .venv
  .venv/bin/pip install -q -U pip
  .venv/bin/pip install -q -r requirements.txt
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  if command -v openssl >/dev/null; then
    sk="$(openssl rand -hex 32)"
    sed -i "s/^SECRET_KEY=$/SECRET_KEY=${sk}/" .env 2>/dev/null || echo "SECRET_KEY=${sk}" >> .env
  fi
  echo "File .env dibuat — edit jika perlu."
fi

mkdir -p data/sessions
export DATA_DIR="${DATA_DIR:-./data}"

echo "Telegram Drive Web → http://${HOST}:${PORT}/"
echo "Stop: Ctrl+C"
exec .venv/bin/uvicorn backend.main:app --host "$HOST" --port "$PORT"