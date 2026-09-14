"""Расписание закупки по дням недели — наша логика, у LF такого нет.

Вечером в 19:40 МСК смотрим на завтра и переводим проект в «паузу» либо «активный»
(их цикл применяет настройки после 20:00 МСК, поэтому именно вечером). Днём, до 19:40,
держим состояние текущего дня: сняли галочку с сегодняшнего дня — закупка встаёт сразу,
вернули — продолжается. Раньше день правился только накануне вечером, и снятая утром
галочка не срабатывала вовсе (разбор 14.09.2026).

Запасной путь — если CRM-контур LF недоступен (у них бывает 502), гасим источники флагом
enabled_by_schedule; источники, выключенные самим клиентом, не трогаем.
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
                    await _apply_tomorrow(db, now)
                else:
                    await _hold_today(db, now)
                await heartbeat(db, "cab_schedule")
        except Exception:
            log.exception("проход не удался")
        await asyncio.sleep(POLL)


def _days(c: CabClient) -> list:
    return c.weekdays if c.weekdays and len(c.weekdays) == 7 else [True] * 7


async def _set(db: AsyncSession, lf: LF | None, c: CabClient, on: bool, note: str) -> None:
    """Перевести закупку клиента в нужное состояние: статусом проекта, иначе источниками."""
    want = "active" if on else "pause"
    if lf and c.lf_crm_id and c.lf_status != want:
        try:
            await lf.set_status(c.lf_crm_id, want)
            c.lf_status = want
            back = await db.execute(update(CabSource).where(
                CabSource.client_id == c.id, CabSource.enabled_by_schedule.is_(False))
                .values(enabled_by_schedule=True, lf_dirty=True))
            await log_event(db, "cab.schedule",
                            f"Клиент {c.login}: {note} — проект переведён в «{want}»"
                            + (f", источников возвращено {back.rowcount}" if back.rowcount else ""),
                            entity="cab_client", entity_id=c.id)
            return
        except LFError as e:
            log.warning("клиент %s: статус проекта не сменился (%s) — гасим источниками", c.login, e)
    res = await db.execute(update(CabSource).where(
        CabSource.client_id == c.id, CabSource.enabled_by_schedule.is_(not on))
        .values(enabled_by_schedule=on, lf_dirty=True))
    if res.rowcount:
        await log_event(db, "cab.schedule",
                        f"Клиент {c.login}: {note} — через источники (CRM LF недоступен), затронуто {res.rowcount}",
                        entity="cab_client", entity_id=c.id, level="warn")


async def _clients(db: AsyncSession) -> tuple[list[CabClient], LF | None]:
    clients = (await db.execute(select(CabClient).where(CabClient.is_active.is_(True)))).scalars().all()
    if not clients:
        return [], None
    token = await get_token(db)
    return clients, (LF(token) if token else None)


async def _apply_tomorrow(db: AsyncSession, now: datetime) -> None:
    today = now.date()
    clients, lf = await _clients(db)
    for c in clients:
        if c.schedule_applied_day == today:
            continue
        on = bool(_days(c)[(now.weekday() + 1) % 7])
        await _set(db, lf, c, on, f"завтра закупка {'включена' if on else 'выключена'} расписанием")
        c.schedule_applied_day = today
    await db.commit()


async def _hold_today(db: AsyncSession, now: datetime) -> None:
    """Днём держим состояние текущего дня: правка расписания срабатывает сразу, а не назавтра."""
    clients, lf = await _clients(db)
    for c in clients:
        on = bool(_days(c)[now.weekday()])
        want = "active" if on else "pause"
        need_sources = (await db.execute(select(CabSource.id).where(
            CabSource.client_id == c.id, CabSource.enabled_by_schedule.is_(not on)).limit(1))).first()
        if c.lf_status == want and not need_sources:
            continue
        await _set(db, lf, c, on, f"сегодня закупка {'включена' if on else 'выключена'} расписанием")
    await db.commit()
