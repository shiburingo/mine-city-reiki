#!/usr/bin/env bash
set -euo pipefail
APP_DIR="${APP_DIR:-/opt/mine-city-reiki}"
cd "${APP_DIR}"
if git rev-parse --is-inside-work-tree >/dev/null 2>&1 && git remote get-url origin >/dev/null 2>&1; then
  git pull --ff-only
else
  echo '[mine-city-reiki] git pull skipped (no origin configured)'
fi
if [ -n "${EXPECTED_REVISION:-}" ] && [ "$(git rev-parse HEAD)" != "${EXPECTED_REVISION}" ]; then
  echo '[mine-city-reiki] unexpected revision; deployment stopped before build/restart' >&2
  exit 1
fi
for generated_dir in node_modules dist; do
  if [ -d "${generated_dir}" ]; then
    legacy_owner="$(find "${generated_dir}" \( ! -uid "$(id -u)" -o ! -gid "$(id -g)" \) -print -quit)"
    if [ -n "${legacy_owner}" ]; then
      echo "[mine-city-reiki] repairing legacy ${generated_dir} ownership (${legacy_owner})"
      sudo chown -R "$(id -u):$(id -g)" "${generated_dir}"
    fi
  fi
done
npm ci
npm run build
if command -v rsync >/dev/null 2>&1 && [ -d /var/www/mine-city-reiki ]; then
  sudo rsync -a --delete "${APP_DIR}/dist/" /var/www/mine-city-reiki/
fi
cd "${APP_DIR}/server"
source venv/bin/activate
pip install -q -r requirements.txt
# Prepare the read-only minutes accelerator before new API workers are started.
service_user="$(systemctl show mine-city-reiki-api.service --property=User --value)"
service_group="$(systemctl show mine-city-reiki-api.service --property=Group --value)"
compile_properties=(--property=EnvironmentFile=/etc/mine-city-reiki-api.env --property="WorkingDirectory=${APP_DIR}/server")
if [ -n "${service_user}" ]; then
  compile_properties+=(--property="User=${service_user}")
fi
if [ -n "${service_group}" ]; then
  compile_properties+=(--property="Group=${service_group}")
fi
sudo systemd-run --wait --pipe --collect "${compile_properties[@]}" \
  /usr/bin/env DB_AUTO_INIT=0 "${APP_DIR}/server/venv/bin/python" rebuild_minutes_search.py --if-needed --allow-empty
if [ -f "${APP_DIR}/deploy/systemd/mine-city-reiki-dictionary.service" ] && [ -f "${APP_DIR}/deploy/systemd/mine-city-reiki-dictionary.timer" ]; then
  sudo install -m 0644 "${APP_DIR}/deploy/systemd/mine-city-reiki-dictionary.service" /etc/systemd/system/mine-city-reiki-dictionary.service
  sudo install -m 0644 "${APP_DIR}/deploy/systemd/mine-city-reiki-dictionary.timer" /etc/systemd/system/mine-city-reiki-dictionary.timer
  sudo systemctl daemon-reload
  sudo systemctl enable --now mine-city-reiki-dictionary.timer
fi
sudo systemctl restart mine-city-reiki-api.service
