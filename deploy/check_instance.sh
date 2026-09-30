#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="$1"
EXPECTED_PID="$2"
if [[ ! "${EXPECTED_PID}" =~ ^[1-9][0-9]*$ ]]; then
  exit 1
fi

count=0
for pid in $(pgrep -f '[p]ython.*-m bot' || true); do
  cwd="$(readlink -f "/proc/${pid}/cwd" 2>/dev/null || true)"
  if [[ "${cwd}" == "$(realpath "${APP_DIR}")" ]]; then
    ((count += 1))
    if [[ "${pid}" != "${EXPECTED_PID}" ]]; then
      exit 1
    fi
  fi
done
[[ "${count}" -eq 1 ]]
