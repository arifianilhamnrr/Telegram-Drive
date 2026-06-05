#!/usr/bin/env bash
# Dipanggil cron sekali sampai native install sukses.
set -euo pipefail
APP_DIR="/www/wwwroot/telegram-drive.argtgbgt.tech/telegram-drive-web"
MARKER="${APP_DIR}/.native-install-done"
CRON_TAG="telegram-drive-native-install"

cd "$APP_DIR"
if [[ -f "$MARKER" ]]; then
  exit 0
fi

chmod +x install-vps.sh update.sh run.sh switch-to-native.sh 2>/dev/null || true
bash "${APP_DIR}/install-vps.sh" >> "${APP_DIR}/cron-install.log" 2>&1 || true

if systemctl is-active --quiet telegram-drive-web 2>/dev/null; then
  touch "$MARKER"
  if [[ -f /var/spool/cron/crontabs/root ]]; then
    sed -i "/${CRON_TAG}/d" /var/spool/cron/crontabs/root 2>/dev/null || true
  fi
  rm -f /etc/cron.d/telegram-drive-native-install 2>/dev/null || true
fi