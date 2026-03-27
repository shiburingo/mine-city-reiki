#!/usr/bin/env bash
set -euo pipefail
APP_DIR="${APP_DIR:-/opt/mine-city-reiki}"
cd "${APP_DIR}"
if git rev-parse --is-inside-work-tree >/dev/null 2>&1 && git remote get-url origin >/dev/null 2>&1; then
  git pull --ff-only
else
  echo '[mine-city-reiki] git pull skipped (no origin configured)'
fi
npm ci
npm run build
if command -v rsync >/dev/null 2>&1 && [ -d /var/www/mine-city-reiki ]; then
  sudo rsync -a --delete "${APP_DIR}/dist/" /var/www/mine-city-reiki/
fi
cd "${APP_DIR}/server"
source venv/bin/activate
pip install -q -r requirements.txt
sudo systemctl restart mine-city-reiki-api.service
