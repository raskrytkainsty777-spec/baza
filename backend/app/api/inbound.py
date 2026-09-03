"""Входящие снаружи — без админского токена, со своими секретами.

Оба эндпоинта делают ровно одно: записали в lg_inbox и ответили 200.
Разбор — inbox_worker, чтобы ошибка разбора не потеряла событие и не
заставила отправителя ретраить.
"""
import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_db
from ..models import LgCity, LgInbox

router = APIRouter(prefix="/api", tags=["inbound"])


async def _store(db: AsyncSession, request: Request, source: str, event_id: str | None = None) -> dict:
    raw = await request.body()
    text = raw.decode("utf-8", "replace")
    try:
        body = json.loads(text) if text.strip() else {}
    except ValueError:
        body = None
    if not event_id:
        event_id = f"{source}:{hashlib.sha256(raw).hexdigest()[:40]}"
    ip = request.headers.get("X-Real-IP") or (request.client.host if request.client else None)
    res = await db.execute(insert(LgInbox).values(
        event_id=event_id[:120], source=source, raw_body=body if isinstance(body, dict) else None,
        raw_text=None if isinstance(body, dict) else text[:20000], remote_ip=(ip or "")[:64] or None,
    ).on_conflict_do_nothing(index_elements=["event_id"]).returning(LgInbox.id))
    inbox_id = res.scalar()
    await db.commit()
    return {"ok": True, "id": inbox_id, "duplicate": inbox_id is None}


@router.post("/probe/callback")
async def probe_callback(request: Request, db: AsyncSession = Depends(get_db)):
    """Ответ сервиса пробива: {"ref", "query", "status", "parsed": {"phone"}, "processed_at"}."""
    secret = settings.probe_callback_secret
    if secret and request.headers.get("X-Hook-Secret") != secret:
        raise HTTPException(403, "Неверный секрет")
    raw = await request.body()
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        body = {}
    ref = body.get("ref") if isinstance(body, dict) else None
    eid = f"probe:{ref}:{body.get('processed_at')}" if (ref and isinstance(body, dict) and body.get("processed_at")) else None
    request._body = raw
    return await _store(db, request, "probe", eid)


@router.post("/crm/status")
async def crm_status(request: Request, db: AsyncSession = Depends(get_db)):
    """Статус лида из CRM: {"lead_id": 123, "status": "application|qual|deal|negative|new", "comment"?}."""
    given = request.headers.get("X-Baza-Secret") or request.query_params.get("secret") or ""
    allowed = {s for s in [settings.inbox_secret] if s}
    allowed |= {s for s in (await db.execute(select(LgCity.crm_secret))).scalars().all() if s}
    if allowed and given not in allowed:
        raise HTTPException(403, "Неверный секрет")
    raw = await request.body()
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        body = {}
    eid = body.get("event_id") if isinstance(body, dict) else None
    request._body = raw
    return await _store(db, request, "crm", f"crm:{eid}" if eid else None)
