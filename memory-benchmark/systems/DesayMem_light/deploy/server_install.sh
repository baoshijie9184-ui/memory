#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${1:-/opt/desaymem-light}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this installer as root." >&2
  exit 1
fi
test -f "$APP_DIR/pyproject.toml"
test -f /etc/desaymem-light/desaymem-light.env
id -u desaymem >/dev/null 2>&1 || useradd --system --home /var/lib/desaymem-light --shell /usr/sbin/nologin desaymem
install -d -o desaymem -g desaymem /var/lib/desaymem-light/sqlite /var/lib/desaymem-light/json
"$PYTHON_BIN" -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install "$APP_DIR[tokenizer]"
install -m 0644 "$APP_DIR/deploy/systemd/"*.service /etc/systemd/system/
systemctl daemon-reload

echo "Install complete. Next run deploy/server_validate.sh; do not start services before it passes."
