#!/usr/bin/env bash
# Деплой на сервере: pull → зависимости → миграции → сборка фронта → рестарт.
#
# Источник — bare-репозиторий /opt/baza.git на этой же машине: в него пушим
# с рабочей машины (`git push server main`) параллельно с GitHub. Так деплой
# не зависит от deploy-ключа GitHub и работает, даже если GitHub недоступен.
# GitHub остаётся каноничной копией для людей.
#
# Запуск: ssh root@95.81.103.196 /opt/baza/deploy/deploy.sh
set -euo pipefail

APP=/opt/baza
cd "$APP"

# Всё под /opt/baza принадлежит baza. Раз запущенный от root git оставил там
# root-овые объекты — и baza потом не смог ни fetch, ни checkout.
chown -R baza:baza "$APP"

echo "▶ pull (local bare)"
sudo -u baza git fetch -q local main
sudo -u baza git checkout -q -f -B main local/main

echo "▶ python deps"
sudo -u baza backend/.venv/bin/pip install -q -r backend/requirements.txt

echo "▶ migrations"
(cd backend && sudo -u baza .venv/bin/alembic upgrade head)

echo "▶ frontend build"
if [ -f frontend/package.json ]; then
  (cd frontend && sudo -u baza npm ci --silent --no-audit --no-fund && sudo -u baza npm run build --silent)
fi

echo "▶ units + restart"
cp deploy/baza-api.service deploy/baza-worker.service /etc/systemd/system/
systemctl daemon-reload
systemctl restart baza-api baza-worker
sleep 2
for s in baza-api baza-worker; do printf "  %s: " "$s"; systemctl is-active "$s"; done
echo "✓ deployed $(sudo -u baza git rev-parse --short HEAD)"
