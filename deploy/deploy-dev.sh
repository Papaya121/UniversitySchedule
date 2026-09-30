#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_DIR="${GITHUB_WORKSPACE:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
APP_DIR="/home/papaya/UniversitySchedule-dev"
SERVICE_NAME="university-schedule-dev.service"
SERVICE_DIR="${HOME}/.config/systemd/user"

# This deployment always uses a fresh, separate location and database.
if [[ ! "${TELEGRAM_BOT_TOKEN_DEV:-}" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]]; then
  echo "Deployment stopped: TELEGRAM_BOT_TOKEN_DEV is missing or invalid" >&2
  exit 1
fi
if [[ "$(realpath "${SOURCE_DIR}")" == "$(realpath /home/papaya/UniversitySchedule)" ]]; then
  echo "Deployment stopped: source is the production directory" >&2
  exit 1
fi
mkdir -p "${APP_DIR}"
if [[ "$(realpath "${APP_DIR}")" == "$(realpath /home/papaya/UniversitySchedule)" ]]; then
  echo "Deployment stopped: dev directory points to production" >&2
  exit 1
fi
if [[ ! -e "${APP_DIR}/.env" ]]; then
  install -m 0600 "${SOURCE_DIR}/.env.example" "${APP_DIR}/.env"
fi
python3 "${SOURCE_DIR}/deploy/sync_token.py" "${APP_DIR}/.env" TELEGRAM_BOT_TOKEN_DEV
for data_dir in data backups; do
  if [[ -L "${APP_DIR}/${data_dir}" ]]; then
    echo "Deployment stopped: dev storage must not be a symlink" >&2
    exit 1
  fi
done

if [[ "$(realpath "${SOURCE_DIR}")" != "$(realpath "${APP_DIR}")" ]]; then
  rsync -a --delete \
    --exclude '.git/' \
    --exclude '.env' \
    --exclude '.venv/' \
    --exclude '.ci-venv/' \
    --exclude 'data/' \
    --exclude 'backups/' \
    "${SOURCE_DIR}/" "${APP_DIR}/"
fi

if [[ ! -x "${APP_DIR}/.venv/bin/python" ]]; then
  python3 -m venv "${APP_DIR}/.venv"
fi
"${APP_DIR}/.venv/bin/python" -m pip install \
  --disable-pip-version-check \
  -r "${APP_DIR}/requirements.txt"

mkdir -p "${SERVICE_DIR}"
install -m 0644 "${APP_DIR}/deploy/${SERVICE_NAME}" "${SERVICE_DIR}/${SERVICE_NAME}"
systemctl --user daemon-reload
systemctl --user enable "${SERVICE_NAME}"
systemctl --user restart "${SERVICE_NAME}"
sleep 5
systemctl --user is-active --quiet "${SERVICE_NAME}"
MAIN_PID="$(systemctl --user show -P MainPID "${SERVICE_NAME}")"
if ! bash "${APP_DIR}/deploy/check_instance.sh" "${APP_DIR}" "${MAIN_PID}"; then
  systemctl --user stop "${SERVICE_NAME}"
  echo "Deployment stopped: dev bot process did not start as expected" >&2
  exit 1
fi
echo "Development bot deployed successfully"
