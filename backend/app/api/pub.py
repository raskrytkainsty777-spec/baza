"""Публичные файлы для внешних сервисов. parser.im забирает список логинов для f1 по ссылке:
одно задание на всю задачу поиска, хоть 100 тысяч логинов. Ссылка подписана HMAC от id задачи."""
import hashlib
import hmac

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_db
from ..models import LgCandidate

router = APIRouter(prefix="/api/pub", tags=["pub"])


def list_token(task_id: int) -> str:
    return hmac.new(settings.admin_token.encode(), f"filter-list:{task_id}".encode(), hashlib.sha256).hexdigest()[:24]


def list_url(task_id: int) -> str:
    return f"{settings.public_base_url.rstrip('/')}/api/pub/lists/{task_id}-{list_token(task_id)}.txt"


@router.get("/lists/{task_id}-{token}.txt", response_class=PlainTextResponse)
async def filter_list(task_id: int, token: str, db: AsyncSession = Depends(get_db)):
    if not hmac.compare_digest(token, list_token(task_id)):
        raise HTTPException(404)
    logins = (await db.execute(select(LgCandidate.username).where(
        LgCandidate.task_id == task_id, LgCandidate.state == "collected",
        ~LgCandidate.username.like("id:%")).order_by(LgCandidate.id))).scalars().all()
    return "\n".join(logins) + ("\n" if logins else "")
