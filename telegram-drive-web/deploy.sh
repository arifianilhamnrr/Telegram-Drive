#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

USE_SSL=false
for arg in "$@"; do
  case "$arg" in
    --ssl) USE_SSL=true ;;
    -h|--help)
      echo "Usage: $0 [--ssl]"
      echo "  --ssl  Aktifkan Caddy (HTTPS) dengan profile proxy"
      exit 0
      ;;
  esac
done

if [[ ! -f .env ]]; then
  echo "Membuat .env dari .env.example ..."
  cp .env.example .env
  if command -v openssl >/dev/null 2>&1; then
    sk="$(openssl rand -hex 32)"
    if grep -q '^SECRET_KEY=$' .env 2>/dev/null; then
      sed -i "s/^SECRET_KEY=$/SECRET_KEY=${sk}/" .env
    else
      echo "SECRET_KEY=${sk}" >> .env
    fi
    echo "SECRET_KEY di-generate otomatis."
  else
    echo "Peringatan: openssl tidak ada — isi SECRET_KEY di .env secara manual."
  fi
  echo "Edit .env (WEB_ACCESS_PASSWORD, DOMAIN, dll) lalu jalankan lagi jika perlu."
fi

# shellcheck disable=SC1091
set -a
source .env
set +a

APP_PORT="${APP_PORT:-8080}"
BIND_HOST="${BIND_HOST:-127.0.0.1}"
DOMAIN="${DOMAIN:-telegram-drive.example.com}"
ACME_EMAIL="${ACME_EMAIL:-admin@example.com}"

if $USE_SSL; then
  echo "Menyiapkan Caddyfile untuk domain: ${DOMAIN}"
  sed -e "s/\${DOMAIN}/${DOMAIN}/g" \
      -e "s/\{\$DOMAIN\}/${DOMAIN}/g" \
      Caddyfile.template > Caddyfile.tmp
  {
    echo "${DOMAIN} {"
    echo "	email ${ACME_EMAIL}"
    echo "	encode gzip"
    echo "	reverse_proxy app:8080"
    echo "}"
  } > Caddyfile
  rm -f Caddyfile.tmp
  echo "Building & starting (app + Caddy)..."
  docker compose build
  docker compose --profile proxy up -d
  echo ""
  echo "=== Telegram Drive Web (HTTPS) ==="
  echo "  https://${DOMAIN}/"
  echo "  Health: curl -s https://${DOMAIN}/health"
else
  echo "Building & starting (app saja)..."
  docker compose build
  docker compose up -d
  echo ""
  echo "=== Telegram Drive Web ==="
  echo "  http://${BIND_HOST}:${APP_PORT}/"
  echo "  Health: curl -s http://${BIND_HOST}:${APP_PORT}/health"
  echo ""
  echo "HTTPS: set DOMAIN & ACME_EMAIL di .env lalu: ./deploy.sh --ssl"
fi

echo ""
docker compose ps