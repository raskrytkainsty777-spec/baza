"""Расписание закупки по дням недели — наша логика, у Leads Factory такого нет.

Их цикл применяет настройки после 20:00 МСК, поэтому каждый вечер в 19:40 смотрим на
завтрашний день: не отмечен — гасим у клиента все источники флагом enabled_by_schedule;
отмечен — возвращаем те, что гасило расписание. Источники, выключенные самим клиентом
(enabled_by_user), расписание не трогает. Дальше cab_sync доносит до LF.
"""
import asyncio
import logging
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal
from ..models import CabClient, CabSource
from ..services.leadsfactory.client import MSK
from .common import heartbeat, log_event

log = logging.getLogger("cab_schedule")

POLL = 60
APPLY_AT = (19, 40)


async def run():
    while True:
        try:
            async with SessionLocal() as db:
                now = datetime.now(MSK)
                if (now.hour, now.minute) >= APPLY_AT:
                    await _apply(db, now)
                await heartbeat(db, "cab_schedule")
        except Exception:
            log.exception("проход не удался")
        await asyncio.sleep(POLL)


async def _apply(db: AsyncSession, now: datetime) -> None:
    today = now.date()
    clients = (await db.execute(select(CabClient).where(CabClient.is_active.is_(True)))).scalars().all()
    for c in clients:
        if c.schedule_applied_day == today:
            continue
        days = c.weekdays if c.weekdays and len(c.weekdays) == 7 else [True] * 7
        tomorrow_on = bool(days[(now.weekday() + 1) % 7])
        res = await db.execute(update(CabSource).where(
            CabSource.client_id == c.id, CabSource.enabled_by_schedule.is_(not tomorrow_on))
            .values(enabled_by_schedule=tomorrow_on, lf_dirty=True))
        c.schedule_applied_day = today
        if res.rowcount:
            await log_event(db, "cab.schedule", f"Клиент {c.login}: завтра закупка {'включена' if tomorrow_on else 'выключена'} расписанием, источников затронуто {res.rowcount}",
                            entity="cab_client", entity_id=c.id)
    await db.commit()
