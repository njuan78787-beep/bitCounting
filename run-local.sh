#!/usr/bin/env bash
# Arranque local sin Docker (para desarrollo o pruebas rápidas).
# Para producción usa Docker (ver README).
set -e
cd "$(dirname "$0")"

export KIDSAFE_DNS_PORT="${KIDSAFE_DNS_PORT:-53}"
export KIDSAFE_WEB_PORT="${KIDSAFE_WEB_PORT:-8080}"

python3 -m venv .venv 2>/dev/null || true
source .venv/bin/activate
pip install -q -r requirements.txt

echo "KidSafe corriendo:"
echo "  Panel:  http://localhost:${KIDSAFE_WEB_PORT}"
echo "  DNS:    puerto ${KIDSAFE_DNS_PORT}"
exec uvicorn app.main:app --host 0.0.0.0 --port "${KIDSAFE_WEB_PORT}"
