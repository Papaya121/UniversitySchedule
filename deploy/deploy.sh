#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/home/papaya/UniversitySchedule}"
SOURCE_DIR="${GITHUB_WORKSPACE:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SERVICE_NAME="university-schedule.service"
SERVICE_DIR="${HOME}/.config/systemd/user"
DATABASE_PATH="${APP_DIR}/data/bot.sqlite3"
DEPLOY_BACKUP_DIR="${APP_DIR}/backups/deploy"

if [[ ! -f "${APP_DIR}/.env" ]]; then
  echo "Deployment stopped: ${APP_DIR}/.env does not exist" >&2
  exit 1
fi

if [[ -f "${DATABASE_PATH}" ]]; then
  mkdir -p "${DEPLOY_BACKUP_DIR}"
  BACKUP_PATH="${DEPLOY_BACKUP_DIR}/bot-$(date +%Y%m%d-%H%M%S).sqlite3"
  DATABASE_PATH="${DATABASE_PATH}" BACKUP_PATH="${BACKUP_PATH}" python3 - <<'PY'
import os
import sqlite3

source = sqlite3.connect(os.environ["DATABASE_PATH"])
destination = sqlite3.connect(os.environ["BACKUP_PATH"])
try:
    source.backup(destination)
    result = destination.execute("PRAGMA integrity_check").fetchone()
    if not result or result[0] != "ok":
        raise RuntimeError(f"backup integrity check failed: {result}")
finally:
    destination.close()
    source.close()
PY
  echo "SQLite backup created: ${BACKUP_PATH}"
fi

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
install -m 0644 \
  "${APP_DIR}/deploy/${SERVICE_NAME}" \
  "${SERVICE_DIR}/${SERVICE_NAME}"

systemctl --user daemon-reload
systemctl --user enable "${SERVICE_NAME}"
systemctl --user restart "${SERVICE_NAME}"
systemctl --user is-active --quiet "${SERVICE_NAME}"

echo "Deployment completed successfully"
