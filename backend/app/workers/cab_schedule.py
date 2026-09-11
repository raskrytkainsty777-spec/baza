"""Расписание закупки по дням недели — наша логика, у LF такого нет.

Их цикл применяет настройки после 20:00 МСК, поэтому каждый вечер в 19:40 смотрим на
завтрашний день: не отмечен — ставим проекту статус «пауза», отмечен — возвращаем «активный»
(решение заказчика 11.09.2026: один запрос на проект вместо тысячи вызовов по источникам).

Запасной путь — если CRM-контур LF недоступен (у них бывает 502), гасим источники флагом
enabled_by_schedule, как делали раньше; источники, выключенные самим клиентом, не трогаем.
"""
import asyncio
import logging
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal
from ..models import CabClient, CabSource
from ..services.leadsfactory.client import LF, LFError, MSK, get_token
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
    if not clients:
        return
    token = await get_token(db)
    lf = LF(token) if token else None
    for c in clients:
        if c.schedule_applied_day == today:
            continue
        days = c.weekdays if c.weekdays and len(c.weekdays) == 7 else [True] * 7
        tomorrow_on = bool(days[(now.weekday() + 1) % 7])
        want = "active" if tomorrow_on else "pause"
        done = False
        if lf and c.lf_crm_id:
            try:
                await lf.set_status(c.lf_crm_id, want)
                c.lf_status = want
                done = True
            except LFError as e:
                log.warning("клиент %s: статус проекта не сменился (%s) — гасим источниками", c.login, e)
        if done:
            # статус проекта решает всё: вернём источники, если их гасило расписание раньше
            back = await db.execute(update(CabSource).where(
                CabSource.client_id == c.id, CabSource.enabled_by_schedule.is_(False))
                .values(enabled_by_schedule=True, lf_dirty=True))
            await log_event(db, "cab.schedule",
                            f"Клиент {c.login}: завтра закупка {'включена' if tomorrow_on else 'выключена'} — проект переведён в «{want}»"
                            + (f", источников возвращено {back.rowcount}" if back.rowcount else ""),
                            entity="cab_client", entity_id=c.id)
        else:
            res = await db.execute(update(CabSource).where(
                CabSource.client_id == c.id, CabSource.enabled_by_schedule.is_(not tomorrow_on))
                .values(enabled_by_schedule=tomorrow_on, lf_dirty=True))
            if res.rowcount:
                await log_event(db, "cab.schedule",
                                f"Клиент {c.login}: завтра закупка {'включена' if tomorrow_on else 'выключена'} расписанием "
                                f"(через источники, CRM LF недоступен), затронуто {res.rowcount}",
                                entity="cab_client", entity_id=c.id, level="warn")
        c.schedule_applied_day = today
    await db.commit()
