"""Общие зависимости API.

Вход — один токен из .env (ADMIN_TOKEN), как в старом сервисе: фронт хранит его
в localStorage и шлёт заголовком Authorization: Bearer. Пользователей нет и не
планируется — сервисом пользуется один человек.
"""
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..config import settings

_bearer = HTTPBearer(auto_error=False)


async def require_token(cred: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str:
    if not cred or cred.credentials != settings.admin_token:
        raise HTTPException(status_code=401, detail="Неверный токен")
    return cred.credentials
