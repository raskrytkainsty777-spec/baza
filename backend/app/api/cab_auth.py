"""Авторизация кабинета: клиенты и агенты — логин + пароль, сессии в cab_sessions.

Отдельно от админского токена baza: у клиента и агента свои таблицы и свои зависимости,
до /api/* админки они не дотягиваются, а админ может открыть кабинет клиента, выпустив
ему сессию (impersonate) с экрана ГЦК.
"""
import hashlib
import secrets
from datetime import timedelta

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import CabAgent, CabClient, CabSession
from ..workers.common import utcnow

SESSION_DAYS = 30


def hash_password(pw: str) -> str:
    salt = secrets.token_hex(8)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 120_000).hex()
    return f"pbkdf2${salt}${h}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$", 2)
    except ValueError:
        return False
    return secrets.compare_digest(hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 120_000).hex(), h)


async def create_session(db: AsyncSession, kind: str, subject_id: int) -> str:
    token = secrets.token_urlsafe(32)
    db.add(CabSession(token=token, kind=kind, subject_id=subject_id,
                      expires_at=utcnow() + timedelta(days=SESSION_DAYS)))
    await db.flush()
    return token


def _bearer(request: Request) -> str:
    auth = request.headers.get("Authorization") or ""
    return auth[7:].strip() if auth.lower().startswith("bearer ") else ""


async def _session(db: AsyncSession, request: Request, kind: str) -> CabSession:
    token = _bearer(request)
    if not token:
        raise HTTPException(401, "Нужен вход")
    s = await db.get(CabSession, token)
    if not s or s.kind != kind or s.expires_at < utcnow():
        raise HTTPException(401, "Сессия истекла — войдите заново")
    return s


async def require_client(request: Request, db: AsyncSession = Depends(get_db)) -> CabClient:
    s = await _session(db, request, "client")
    c = await db.get(CabClient, s.subject_id)
    if not c or not c.is_active:
        raise HTTPException(401, "Клиент отключён")
    return c


async def require_agent(request: Request, db: AsyncSession = Depends(get_db)) -> CabAgent:
    s = await _session(db, request, "agent")
    a = await db.get(CabAgent, s.subject_id)
    if not a or not a.is_active:
        raise HTTPException(401, "Агент отключён")
    return a


async def login_client(db: AsyncSession, login: str, password: str) -> str:
    c = (await db.execute(select(CabClient).where(CabClient.login == login.strip().lower()))).scalar_one_or_none()
    if not c or not c.is_active or not verify_password(password, c.password_hash):
        raise HTTPException(401, "Неверный логин или пароль")
    token = await create_session(db, "client", c.id)
    await db.commit()
    return token


async def login_agent(db: AsyncSession, login: str, password: str) -> str:
    a = (await db.execute(select(CabAgent).where(CabAgent.login == login.strip().lower()))).scalar_one_or_none()
    if not a or not a.is_active or not verify_password(password, a.password_hash):
        raise HTTPException(401, "Неверный логин или пароль")
    token = await create_session(db, "agent", a.id)
    await db.commit()
    return token
