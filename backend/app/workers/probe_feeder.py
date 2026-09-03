"""Подача лидов на пробив.

Проверка базы — до отправки: номер уже есть у аккаунта → лид получает его
без платного запроса. Авто-режим города шлёт всё новое сам, ручной — только то,
что оператор отметил в отсеке «непробитые» (probe_status = queued_manual).
Дважды один аккаунт не пробиваем; «не найден» не повторяем полгода.
"""
import asyncio
import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal
from ..models import IgAccount, LgCity, LgLead
from ..services.outbound import probe_url, queue_crm, queue_probe
from .common import heartbeat, log_event, utcnow

log = logging.getLogger("probe_feeder")

POLL = 30
BATCH = 100
NOT_FOUND_RETRY_DAYS = 180


async def run():
    while True:
        try:
            async with SessionLocal() as db:
                await _pass(db)
                await heartbeat(db, "probe_feeder")
        except Exception:
            log.exception("проход не удался")
        await asyncio.sleep(POLL)


async def _pass(db: AsyncSession) -> None:
    cities = (await db.execute(select(LgCity).where(LgCity.is_active.is_(True), LgCity.probe_enabled.is_(True)))).scalars().all()
    for city in cities:
        if not probe_url(city):
            continue
        statuses = ["manual"] if city.probe_mode == "manual" else ["manual", "pending"]
        leads = (await db.execute(select(LgLead).where(
            LgLead.city_id == city.id, LgLead.phone.is_(None), LgLead.probe_status.in_(statuses))
            .order_by(LgLead.id).limit(BATCH))).scalars().all()
        if not leads:
            continue
        to_send = []
        for lead in leads:
            acc = await db.get(IgAccount, lead.account_id)
            if acc and acc.phone:
                lead.phone, lead.phone_from, lead.probe_status, lead.probed_at = acc.phone, "base", "skipped", utcnow()
                await queue_crm(db, lead, city)
                continue
            if acc and acc.probe_status == "not_found" and acc.probed_at and acc.probed_at > utcnow() - timedelta(days=NOT_FOUND_RETRY_DAYS):
                lead.probe_status, lead.probed_at = "not_found", utcnow()
                continue
            if acc and acc.probe_status == "sent":
                continue   # уже в очереди пробива по другому лиду — ответ разложится по всем
            to_send.append(lead)
        if to_send:
            out = await queue_probe(db, city, to_send)
            if out:
                for lead in to_send:
                    acc = await db.get(IgAccount, lead.account_id)
                    if acc:
                        acc.probe_status = "sent"
                await log_event(db, "probe.queued", f"{city.name}: на пробив {len(to_send)} лидов",
                                entity="city", entity_id=city.id)
        await db.commit()
