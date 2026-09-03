"""Квалификация комментариев: лид или мусор, с сутью.

Комментарий оценивается вместе с разбором поста — кодовое слово из поста и
«плюс» имеют смысл только в его контексте. Лид сразу получает строку в lg_leads
со снимком цен города; если номер этого человека уже есть в базе — пробив
не нужен, лид готов к отдаче.
"""
import asyncio
import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal
from ..models import IgAccount, LgCity, LgComment, LgLead, LgPost
from ..services.ai.client import chat_json, prompt
from ..services.outbound import queue_crm
from .common import add_ai_cost, ai_on, heartbeat, log_event, settings_all, utcnow

log = logging.getLogger("ai_comments")

POLL = 15
BATCH = 24
CONCURRENCY = 6
FORMAT = "\n\nФормат ответа: {\"is_lead\": true, \"summary\": \"…\", \"reason\": \"…\"}"


async def run():
    while True:
        try:
            async with SessionLocal() as db:
                if await ai_on(db):
                    await _pass(db)
                await _leads_for_late_cities(db)
                await heartbeat(db, "ai_comments")
        except Exception:
            log.exception("проход не удался")
        await asyncio.sleep(POLL)


async def _pass(db: AsyncSession) -> None:
    rows = (await db.execute(
        select(LgComment, LgPost).join(LgPost, LgPost.id == LgComment.post_id)
        .where(LgComment.qualification == "pending", LgComment.is_donor_reply.is_(False))
        .order_by(LgComment.id).limit(BATCH))).all()
    if not rows:
        return
    values = await settings_all(db)
    system = prompt("comment", values) + FORMAT
    sem = asyncio.Semaphore(CONCURRENCY)

    async def ask(c: LgComment, p: LgPost):
        user = json.dumps({
            "post": {"offer": p.offer, "hook": p.hook, "category": p.category, "cta": p.cta_type,
                     "code_word": p.code_word, "summary": p.ai_summary},
            "comment": {"author": c.author_username, "text": (c.text or "")[:1500],
                        "days_after_post": c.age_distance_days},
        }, ensure_ascii=False)
        async with sem:
            return await chat_json(system, user, max_tokens=300)

    results = await asyncio.gather(*(ask(c, p) for c, p in rows), return_exceptions=True)
    cost, failed, leads = 0.0, 0, 0
    for (c, p), r in zip(rows, results):
        if isinstance(r, BaseException):
            failed += 1
            log.warning("комментарий %s: %s", c.ig_comment_id, r)
            continue
        cost += getattr(r, "cost", 0.0)
        c.qualification = "lead" if r.get("is_lead") else "ignore"
        c.ai_summary = (str(r.get("summary") or ""))[:300] or None
        c.ai_reason = (str(r.get("reason") or ""))[:1000] or None
        c.ai_at = utcnow()
        if c.qualification == "lead" and await make_lead(db, c, p):
            leads += 1
    await add_ai_cost(db, cost)
    await db.commit()
    if leads:
        log.info("лидов: %d из %d комментариев", leads, len(rows))
    if failed == len(rows):
        await log_event(db, "ai.error", f"Квалификация: ИИ не ответила по {failed} комментариям подряд", level="error")
        await db.commit()
        await asyncio.sleep(60)


async def make_lead(db: AsyncSession, c: LgComment, p: LgPost) -> bool:
    """Лид из комментария. Без города — не создаём (вернётся позже, когда пост получит город)."""
    city_id = c.city_id or p.city_id
    if not city_id:
        return False
    if (await db.execute(select(LgLead.id).where(LgLead.comment_id == c.id))).scalar():
        return False
    city = await db.get(LgCity, city_id)
    acc = None
    if c.author_account_id:
        acc = await db.get(IgAccount, c.author_account_id)
    if acc is None and c.author_username:
        acc = (await db.execute(select(IgAccount).where(IgAccount.username == c.author_username))).scalar_one_or_none()
        if acc is None:
            acc = IgAccount(username=c.author_username, roles="commenter")
            db.add(acc)
            await db.flush()
        c.author_account_id = acc.id
    if acc is None:
        return False
    lead = LgLead(comment_id=c.id, post_id=p.id, account_id=acc.id, city_id=city_id,
                  cost_contact=city.cost_per_contact if city else 0,
                  cost_handling=city.cost_per_handling if city else 0)
    if acc.phone:
        lead.phone, lead.phone_from, lead.probe_status, lead.probed_at = acc.phone, "base", "skipped", utcnow()
    db.add(lead)
    await db.flush()
    if lead.phone and city:
        await queue_crm(db, lead, city)
    return True


async def _leads_for_late_cities(db: AsyncSession) -> None:
    """Комментарии-лиды, оставшиеся без лида (пост тогда не имел города)."""
    rows = (await db.execute(
        select(LgComment, LgPost).join(LgPost, LgPost.id == LgComment.post_id)
        .outerjoin(LgLead, LgLead.comment_id == LgComment.id)
        .where(LgComment.qualification == "lead", LgLead.id.is_(None), LgPost.city_id.isnot(None))
        .limit(200))).all()
    made = 0
    for c, p in rows:
        c.city_id = c.city_id or p.city_id
        if await make_lead(db, c, p):
            made += 1
    if rows:
        await db.commit()
