"""Точка входа API.

nginx отдаёт статику фронта и проксирует /api сюда. Всё под /api,
чтобы SPA-роутинг фронта и API не пересекались.
"""
from fastapi import FastAPI

from .config import settings

app = FastAPI(
    title="baza",
    version="0.1.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)


@app.get("/api/health", include_in_schema=False)
async def health():
    return {"ok": True, "tz": settings.tz_display}
