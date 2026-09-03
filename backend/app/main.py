"""Точка входа API.

nginx отдаёт статику фронта и проксирует /api сюда. Всё под /api,
чтобы SPA-роутинг фронта и API не пересекались.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import auth, cities, dashboard, donors, inbound, jobs, ops, posts, search, settings as settings_api
from .config import settings

app = FastAPI(
    title="baza",
    version="0.3.0",
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

for r in (auth, cities, dashboard, donors, posts, search, jobs, settings_api, ops, inbound):
    app.include_router(r.router)


@app.get("/api/health", include_in_schema=False)
async def health():
    return {"ok": True, "tz": settings.tz_display, "version": app.version}
