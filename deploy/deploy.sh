#!/usr/bin/env bash
# Деплой на сервере: git pull → зависимости → миграции → сборка фронта → рестарт.
# Запуск: ssh root@95.81.103.196 /opt/baza/deploy/deploy.sh
set -euo pipefail

APP=/opt/baza
cd "$APP"

echo "▶ git pull"
git pull --ff-only

echo "▶ python deps"
backend/.venv/bin/pip install -q -r backend/requirements.txt

echo "▶ migrations"
(cd backend && .venv/bin/alembic upgrade head)

echo "▶ frontend build"
if [ -f frontend/package.json ]; then
  (cd frontend && npm ci --silent --no-audit --no-fund && npm run build --silent)
fi

echo "▶ restart"
systemctl restart baza-api baza-worker
sleep 2
systemctl --no-pager status baza-api baza-worker | grep -E '●|Active' || true
echo "✓ deployed $(git rev-parse --short HEAD)"
