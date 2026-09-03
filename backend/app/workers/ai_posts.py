"""Разметка постов ИИ: продающий ли, оффер, крючок, категория, призыв, кодовое слово.

Один раз на пост. У неразобранного донора ИИ ещё и определяет город объекта —
пост уезжает в этот город как источник.
"""
import asyncio
import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal
from ..models import IgAccount, LgCity, LgDonor, LgPost
from ..services.ai.client import chat_json, prompt
from .common import add_ai_cost, ai_on, get_or_create_city, heartbeat, log_event, settings_all, utcnow

log = logging.getLogger("ai_posts")

POLL = 15
BATCH = 12
CONCURRENCY = 4
CITY_CONFIDENT = 0.7
FORMAT = ("\n\nФормат ответа: {\"is_selling\": true, \"offer\": \"…\", \"hook\": \"…\", \"category\": \"…\", "
          "\"cta_type\": \"…\", \"code_word\": null, \"summary\": \"…\"%s}")


async def run():
    while True:
        try:
            async with SessionLocal() as db:
                if await ai_on(db):
                    await _pass(db)
                await heartbeat(db, "ai_posts")
        except Exception:
            log.exception("проход не удался")
        await asyncio.sleep(POLL)


def _cut(v, n: int) -> str | None:
    s = ("" if v is None else str(v)).strip()
    return s[:n] or None


async def _pass(db: AsyncSession) -> None:
    rows = (await db.execute(
        select(LgPost, LgDonor, IgAccount.username).join(LgDonor, LgDonor.id == LgPost.donor_id)
        .join(IgAccount, IgAccount.id == LgPost.account_id)
        .where(LgPost.is_selling.is_(None), LgPost.ai_at.is_(None)).order_by(LgPost.id).limit(BATCH))).all()
    if not rows:
        return
    values = await settings_all(db)
    cities = ", ".join((await db.execute(select(LgCity.name).order_by(LgCity.name))).scalars().all())
    base = prompt("post", values)
    with_city = base + "\n\n" + prompt("post_city", values, cities=cities)
    sem = asyncio.Semaphore(CONCURRENCY)

    async def ask(p: LgPost, d: LgDonor, username: str):
        if not (p.caption or "").strip():
            return None
        need_city = d.city_id is None and p.city_id is None
        system = (with_city if need_city else base) + (FORMAT % (", \"city\": null, \"city_confidence\": 0.0" if need_city else ""))
        user = json.dumps({"donor": username, "city": None if need_city else None,
                           "published": p.published_at.isoformat() if p.published_at else None,
                           "type": p.product_type, "caption": p.caption[:6000]}, ensure_ascii=False)
        async with sem:
            return await chat_json(system, user)

    results = await asyncio.gather(*(ask(p, d, u) for p, d, u in rows), return_exceptions=True)
    cost, failed = 0.0, 0
    for (p, d, username), r in zip(rows, results):
        if isinstance(r, BaseException):
            failed += 1
            log.warning("пост %s: %s", p.shortcode, r)
            continue
        p.ai_at = utcnow()
        if r is None:
            p.is_selling, p.ai_summary = False, "без текста"
            continue
        cost += getattr(r, "cost", 0.0)
        p.is_selling = bool(r.get("is_selling"))
        p.offer = _cut(r.get("offer"), 300)
        p.hook = _cut(r.get("hook"), 80)
        p.category = _cut(r.get("category"), 40)
        cta = r.get("cta_type")
        p.cta_type = _cut(", ".join(cta) if isinstance(cta, list) else cta, 60)
        cw = r.get("code_word")
        p.code_word = _cut(cw.upper() if isinstance(cw, str) else None, 60)
        p.ai_summary = _cut(r.get("summary"), 1000)
        if p.is_selling and d.city_id is None and p.city_id is None and r.get("city"):
            try:
                conf = float(r.get("city_confidence") or 0)
            except (TypeError, ValueError):
                conf = 0.0
            if conf >= CITY_CONFIDENT:
                city = await get_or_create_city(db, str(r["city"]))
                if city:
                    p.city_id, p.city_source = city.id, "ai"
    await add_ai_cost(db, cost)
    await db.commit()
    if failed == len(rows):
        await log_event(db, "ai.error", f"Разметка постов: ИИ не ответила по {failed} постам подряд", level="error")
        await db.commit()
        await asyncio.sleep(60)
