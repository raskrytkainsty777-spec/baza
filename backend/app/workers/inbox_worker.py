"""Разбор входящих: ответы пробива и статусы из CRM.

Эндпоинты только пишут в lg_inbox; здесь — применение к лидам и аккаунтам.
Номер из пробива ложится в аккаунт, и все ждущие лиды этого человека получают
его без повторного запроса. Лид с номером — сразу в CRM (если авто-режим).
"""
import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal
from ..models import IgAccount, LgCity, LgInbox, LgLead
from ..services.outbound import queue_crm
from .common import heartbeat, log_event, utcnow

log = logging.getLogger("inbox")

POLL = 5
MAX_ATTEMPTS = 5
CRM_STATUSES = ("new", "negative", "application", "qual", "deal")


async def run():
    while True:
        try:
            async with SessionLocal() as db:
                await _pass(db)
                await heartbeat(db, "inbox")
        except Exception:
            log.exception("проход не удался")
        await asyncio.sleep(POLL)


async def _pass(db: AsyncSession) -> None:
    rows = (await db.execute(select(LgInbox).where(LgInbox.state == "pending").order_by(LgInbox.id).limit(50))).scalars().all()
    for row in rows:
        try:
            body = row.raw_body or {}
            if row.source == "probe":
                await _probe(db, body)
            elif row.source == "crm":
                await _crm(db, body)
            row.state, row.processed_at = "done", utcnow()
        except Exception as e:
            row.attempts = (row.attempts or 0) + 1
            row.error = str(e)[:500]
            if row.attempts >= MAX_ATTEMPTS:
                row.state = "error"
                await log_event(db, "inbox.error", f"{row.source}: не разобрано: {e}", level="error", payload=body)
        await db.commit()


def _lead_id(body: dict) -> int | None:
    for k in ("ref", "lead_id"):
        v = body.get(k)
        if v is None and isinstance(body.get("payload"), dict):
            v = body["payload"].get(k)
        try:
            return int(str(v).strip()) if v not in (None, "") else None
        except ValueError:
            continue
    return None


async def _probe(db: AsyncSession, body: dict) -> None:
    lead_id = _lead_id(body)
    lead = await db.get(LgLead, lead_id) if lead_id else None
    if not lead:
        raise ValueError(f"лид не найден: ref={body.get('ref')!r}")
    parsed = body.get("parsed") or {}
    phone = (str(parsed.get("phone") or "").strip()) or None
    status = str(body.get("status") or "").lower()
    acc = await db.get(IgAccount, lead.account_id)
    now = utcnow()
    if phone:
        lead.phone, lead.phone_from, lead.probe_status, lead.probed_at = phone, "probe", "done", now
        if acc:
            acc.phone, acc.phone_source, acc.probe_status, acc.probed_at, acc.probe_raw = phone, "probe", "done", now, parsed
            # остальные ждущие лиды этого человека — из базы, без запроса
            others = (await db.execute(select(LgLead).where(
                LgLead.account_id == acc.id, LgLead.id != lead.id, LgLead.phone.is_(None)))).scalars().all()
            for o in others:
                o.phone, o.phone_from, o.probe_status, o.probed_at = phone, "base", "skipped", now
                city_o = await db.get(LgCity, o.city_id)
                if city_o:
                    await queue_crm(db, o, city_o)
        city = await db.get(LgCity, lead.city_id)
        if city:
            await queue_crm(db, lead, city)
    elif status in ("not_found", "success"):
        lead.probe_status, lead.probed_at = "not_found", now
        if acc:
            acc.probe_status, acc.probed_at, acc.probe_raw = "not_found", now, parsed or {"response": body.get("response")}
    else:
        lead.probe_status, lead.probed_at = "error", now
        if acc:
            acc.probe_status = "error"
        await log_event(db, "probe.error", f"Пробив лида #{lead.id}: {status or 'без статуса'}", entity="lead", entity_id=lead.id, level="warn")


async def _crm(db: AsyncSession, body: dict) -> None:
    lead_id = _lead_id(body)
    lead = await db.get(LgLead, lead_id) if lead_id else None
    if not lead:
        raise ValueError(f"лид не найден: lead_id={body.get('lead_id')!r}")
    status = str(body.get("status") or "").strip().lower()
    if status not in CRM_STATUSES:
        raise ValueError(f"неизвестный статус {status!r}; ожидается один из {CRM_STATUSES}")
    lead.crm_status, lead.crm_updated_at = status, utcnow()
    if body.get("comment") is not None:
        lead.crm_comment = str(body["comment"])[:2000]
    await log_event(db, "crm.status", f"Лид #{lead.id}: статус {status}", entity="lead", entity_id=lead.id)
