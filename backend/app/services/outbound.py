"""Что и как отправляем наружу: строки на пробив, лиды в CRM.

Здесь только формирование записей lg_outbox; сам HTTP — в outbox_worker,
с повторами. Контракт пробива — старый сервис: POST /api/hook/{token},
тело {"queries": [{"query": "instagram.com/login", "ref": "<lead_id>", …}]},
лишние поля уезжают в payload и видны сценарию. Ответ приходит на
/api/probe/callback: {"ref", "status", "parsed": {"phone": …}}.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import IgAccount, LgCity, LgComment, LgLead, LgOutbox, LgPost


def probe_url(city: LgCity) -> str | None:
    token = (city.probe_hook_token or settings.probe_hook_token or "").strip()
    if not token:
        return None
    return f"{settings.probe_base_url.rstrip('/')}/api/hook/{token}"


async def lead_row(db: AsyncSession, lead: LgLead) -> dict:
    """Вся строка лида — на пробив и в CRM уходит одно и то же."""
    c = await db.get(LgComment, lead.comment_id)
    p = await db.get(LgPost, lead.post_id)
    a = await db.get(IgAccount, lead.account_id)
    city = await db.get(LgCity, lead.city_id)
    return {
        "lead_id": lead.id,
        "username": a.username if a else (c.author_username if c else None),
        "city": city.name if city else None,
        "comment": (c.text if c else None),
        "comment_summary": (c.ai_summary if c else None),
        "written_at": c.written_at.isoformat() if (c and c.written_at) else None,
        "post_url": p.url if p else None,
        "post_published_at": p.published_at.isoformat() if (p and p.published_at) else None,
        "offer": p.offer if p else None,
        "hook": p.hook if p else None,
        "category": p.category if p else None,
        "cta_type": p.cta_type if p else None,
        "code_word": p.code_word if p else None,
        "post_summary": p.ai_summary if p else None,
        "donor": None,
    }


async def queue_probe(db: AsyncSession, city: LgCity, leads: list[LgLead]) -> LgOutbox | None:
    url = probe_url(city)
    if not url or not leads:
        return None
    queries = []
    for lead in leads:
        row = await lead_row(db, lead)
        if not row["username"]:
            continue
        queries.append({"query": f"instagram.com/{row['username']}", "ref": str(lead.id), **row})
        lead.probe_status = "queued"
    if not queries:
        return None
    out = LgOutbox(target="probe", url=url, payload={"queries": queries, "lead_ids": [l.id for l in leads]})
    db.add(out)
    await db.flush()
    return out


async def queue_crm(db: AsyncSession, lead: LgLead, city: LgCity) -> LgOutbox | None:
    """Лид с номером → CRM города. send_mode=manual — ждёт кнопки оператора."""
    url = (city.crm_webhook_url or settings.crm_webhook_url or "").strip()
    if not url or not lead.phone or city.send_mode != "auto":
        return None
    if lead.outbound_status in ("sent", "queued"):
        return None
    row = await lead_row(db, lead)
    out = LgOutbox(lead_id=lead.id, target="crm", url=url,
                   payload={**row, "phone": lead.phone, "phone_from": lead.phone_from})
    db.add(out)
    lead.outbound_status = "queued"
    await db.flush()
    return out
