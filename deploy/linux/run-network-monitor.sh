#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/network_monitor"
VENV_DIR="${APP_DIR}/.venv"
ENV_FILE="${APP_DIR}/.env"

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    echo "Python virtual environment not found at ${VENV_DIR}" >&2
    echo "Create it first with: python3 -m venv /opt/network_monitor/.venv" >&2
    exit 1
fi

cd "${APP_DIR}"
if [[ ! -f "${ENV_FILE}" ]]; then
    echo "Production .env not found at ${ENV_FILE}" >&2
    echo "Create it from one of the supported templates before starting the service:" >&2
    echo "  cp ${APP_DIR}/deploy/linux/.env.linux.example ${ENV_FILE}     # nginx + reverse proxy" >&2
    echo "  cp ${APP_DIR}/deploy/linux/.env.linux.ip.example ${ENV_FILE}  # direct IP / LAN access" >&2
    exit 1
fi

exec "${VENV_DIR}/bin/python" main.py
