"""Точка входа API.

nginx отдаёт статику фронта и проксирует /api сюда. Всё под /api,
чтобы SPA-роутинг фронта и API не пересекались. Кабинет закупки (ГЦК)
живёт под /api/gck (админ), /api/cab (клиент) и /api/agent (агент) —
у клиентов и агентов своя авторизация.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import (
    auth, cab, cities, dashboard, donors, gck, inbound, jobs, ops, posts, search, settings as settings_api,
)
from .config import settings

app = FastAPI(
    title="baza",
    version="0.4.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

# для `npm run dev` на 5173 — в бою фронт и API на одном origin, CORS не нужен
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (auth, cities, dashboard, donors, posts, search, jobs, settings_api, ops, inbound, gck, cab):
    app.include_router(r.router)

try:
    from .api import agent as agent_api
    app.include_router(agent_api.router)
except ImportError:
    pass


@app.get("/api/health", include_in_schema=False)
async def health():
    return {"ok": True, "tz": settings.tz_display, "version": app.version}
