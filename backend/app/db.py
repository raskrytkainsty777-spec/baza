"""Подключение к PostgreSQL.

Один движок на процесс. pool_pre_ping — чтобы после рестарта Postgres
воркер не падал на первом же протухшем соединении из пула.
"""
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import settings

engine = create_async_engine(
    settings.database_url,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=1800,
)

SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    """Зависимость FastAPI: сессия на запрос."""
    async with SessionLocal() as session:
        yield session
