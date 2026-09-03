# baza — комментарии в лиды

Сервис ищет риелторов по городам, следит за их продающими постами в Instagram
и вытаскивает из комментариев людей с реальным интересом — уже с номером телефона.

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — решения, модель данных, воркеры, контракты, фазы
- [docs/DECISIONS.md](docs/DECISIONS.md) — журнал решений: что, почему, что отвергли
- [docs/PARSERIM.md](docs/PARSERIM.md) — квирки API parser.im, из-за которых код такой

## Стек

Python 3.12 · FastAPI · SQLAlchemy 2 async · PostgreSQL 16 · Alembic — бэкенд и воркеры.
React 18 · TypeScript · Vite · Mantine 7 · TanStack Query 5 — фронт.
Без Docker, Redis и Celery — см. DECISIONS.

## Структура

```
backend/
  app/
    main.py           API
    config.py  db.py
    models/           по файлу на группу таблиц
    api/              роутеры по разделам
    services/
      parserim/       клиент + типы заданий p1/p2/p3/p5/f1
      apify/          клиент
      ai/             вызовы моделей; промпты в backend/prompts/
      probe.py        связь со старым сервером пробива
    workers/          по файлу на воркер, планировщик в scheduler.py
  migrations/         alembic
  prompts/            промпты ИИ отдельными файлами
frontend/             vite + react
deploy/               systemd-юниты, nginx, deploy.sh
```

## Локально

```bash
cd backend
python -m venv .venv && . .venv/Scripts/activate      # Windows
pip install -r requirements.txt
cp ../.env.example ../.env                              # заполнить
alembic upgrade head
uvicorn app.main:app --reload
```

```bash
cd frontend
npm ci && npm run dev
```

## Как войти

Интерфейс: `http://95.81.103.196/`. Пользователей нет — один токен доступа, как в старом сервисе.
Токен лежит на сервере в `/opt/baza/.env`, строка `ADMIN_TOKEN=…`:

```bash
ssh root@95.81.103.196 "grep ^ADMIN_TOKEN= /opt/baza/.env"
```

Вставить в форму входа — браузер запомнит. Сменить токен: поправить строку в `.env` и
`systemctl restart baza-api`; все, кто вошёл со старым, вылетят на форму входа.

## Сервер

`95.81.103.196`, Ubuntu 24.04. Код в `/opt/baza` под пользователем `baza`. Две службы: `baza-api`
(uvicorn :8000), `baza-worker` (планировщик). nginx отдаёт `frontend/dist` и проксирует `/api`.
PostgreSQL 16, база и роль `baza`. Секреты — `/opt/baza/.env`.

Деплой идёт через bare-репозиторий `/opt/baza.git` на самом сервере, не через GitHub —
чтобы не зависеть от deploy-ключей. Пушим в оба remote, потом запускаем скрипт:

```bash
git push origin main && git push server main && ssh root@95.81.103.196 /opt/baza/deploy/deploy.sh
```

Один раз добавить remote: `git remote add server root@95.81.103.196:/opt/baza.git`.

Скрипт: checkout из `local` → `pip install` → `alembic upgrade head` → `npm ci && npm run build` → `systemctl restart`.
Логи: `journalctl -u baza-api -f`, `journalctl -u baza-worker -f`.
