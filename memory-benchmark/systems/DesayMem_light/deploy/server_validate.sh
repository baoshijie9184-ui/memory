#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${1:-/opt/desaymem-light}"
ENV_FILE="${ENV_FILE:-/etc/desaymem-light/desaymem-light.env}"

set -a
source "$ENV_FILE"
set +a

"$APP_DIR/.venv/bin/python" -m compileall -q "$APP_DIR/src"
"$APP_DIR/.venv/bin/python" -c "from desaymem_light.cli import migrate_main, preflight_main"
"$APP_DIR/.venv/bin/desaymem-migrate"
"$APP_DIR/.venv/bin/desaymem-preflight"

echo "Server validation passed. Services may now be started."
