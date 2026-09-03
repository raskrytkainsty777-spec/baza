"""Отправка наружу с повторами: пробив и CRM.

Ответ 2xx — отправлено; иначе следующая попытка через растущую паузу, после
десятой — «мёртвая» запись и событие уровня error, чтобы оператор увидел.
"""
import asyncio
import json
import logging
from datetime import timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import SessionLocal
from ..models import LgCity, LgLead, LgOutbox
from .common import heartbeat, log_event, utcnow

log = logging.getLogger("outbox")

POLL = 5
MAX_ATTEMPTS = 10
TIMEOUT = 30.0


async def run():
    while True:
        try:
            async with SessionLocal() as db:
                await _pass(db)
                await heartbeat(db, "outbox")
        except Exception:
            log.exception("проход не удался")
        await asyncio.sleep(POLL)


async def _headers(db: AsyncSession, row: LgOutbox) -> dict:
    h = {"Content-Type": "application/json"}
    if row.target == "crm":
        secret = settings.crm_webhook_secret
        if row.lead_id:
            lead = await db.get(LgLead, row.lead_id)
            city = await db.get(LgCity, lead.city_id) if lead else None
            secret = (city.crm_secret if city and city.crm_secret else secret)
        if secret:
            h["X-Baza-Secret"] = secret
    return h


async def _after_sent(db: AsyncSession, row: LgOutbox) -> None:
    if row.target == "probe":
        ids = (row.payload or {}).get("lead_ids") or []
        for lead in (await db.execute(select(LgLead).where(LgLead.id.in_(ids)))).scalars().all():
            if lead.probe_status in ("queued", "manual"):
                lead.probe_status = "sent"
    elif row.target == "crm" and row.lead_id:
        lead = await db.get(LgLead, row.lead_id)
        if lead:
            lead.outbound_status, lead.sent_at = "sent", utcnow()


async def _pass(db: AsyncSession) -> None:
    rows = (await db.execute(select(LgOutbox).where(
        LgOutbox.state.in_(["pending", "failed"]), LgOutbox.next_try_at <= utcnow())
        .order_by(LgOutbox.id).limit(20))).scalars().all()
    for row in rows:
        headers = await _headers(db, row)
        code, err = None, None
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as cl:
                r = await cl.post(row.url, content=json.dumps(row.payload, ensure_ascii=False).encode("utf-8"), headers=headers)
            code = r.status_code
            if code >= 400:
                err = r.text[:300]
        except httpx.HTTPError as e:
            err = str(e)[:300]
        row.attempts = (row.attempts or 0) + 1
        row.last_code, row.last_error = code, err
        if code and code < 400:
            row.state, row.sent_at = "sent", utcnow()
            await _after_sent(db, row)
        elif row.attempts >= MAX_ATTEMPTS:
            row.state = "dead"
            await log_event(db, "outbox.dead", f"{row.target}: не доставлено после {row.attempts} попыток ({code or err})",
                            entity="lead", entity_id=row.lead_id, level="error")
        else:
            row.state = "failed"
            row.next_try_at = utcnow() + timedelta(seconds=min(30 * 2 ** row.attempts, 3600))
        await db.commit()
