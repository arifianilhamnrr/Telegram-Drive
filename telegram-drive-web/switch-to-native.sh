#!/usr/bin/env bash
# Jalankan sekali: bash switch-to-native.sh
set -euo pipefail
cd "$(dirname "$0")"
chmod +x install-vps.sh update.sh run.sh
bash ./install-vps.sh
echo ""
echo "=== Verifikasi ==="
systemctl is-active telegram-drive-web || true
curl -s http://127.0.0.1:14202/health || true
echo ""
docker ps --format 'table {{.Names}}\t{{.Status}}' 2>/dev/null | grep -E 'telegram-drive|NAMES' || docker ps 2>/dev/null | head -5