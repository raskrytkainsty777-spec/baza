"""Статусы клиента по вебхуку → контакты → компании.

Эндпоинт /api/cab/hook/{token} только пишет в cab_inbox; здесь номер ищется среди
купленных контактов клиента и получает статус. Один номер мог прийти от нескольких
поставщиков — статус ставим всем его строкам, в статистике компаний он считается один раз.
"""
import asyncio
import logging

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal
from ..models import CabContact, CabInbox
from .common import heartbeat, utcnow

log = logging.getLogger("cab_inbox")

POLL = 5


async def run():
    while True:
        try:
            async with SessionLocal() as db:
                await _pass(db)
                await heartbeat(db, "cab_inbox")
        except Exception:
            log.exception("проход не удался")
        await asyncio.sleep(POLL)


async def _pass(db: AsyncSession) -> None:
    rows = (await db.execute(select(CabInbox).where(CabInbox.state == "pending").order_by(CabInbox.id).limit(200))).scalars().all()
    for row in rows:
        if not row.phone or not row.status:
            row.state, row.error = "error", "нет номера или статуса"
            continue
        res = await db.execute(update(CabContact).where(CabContact.client_id == row.client_id, CabContact.phone == row.phone)
                               .values(hook_status=row.status, hook_status_at=utcnow()))
        if res.rowcount:
            row.state, row.processed_at = "done", utcnow()
        else:
            row.state, row.error, row.processed_at = "error", "номер не найден среди купленных контактов", utcnow()
    if rows:
        await db.commit()
