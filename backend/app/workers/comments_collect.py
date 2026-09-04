"""Сбор комментариев: первый — целиком, потом по приросту.

Обычные посты уходят в parser.im (p2, web=1) пачками по 10 ссылок — это и есть
«строки» тарифа. Крупные посты с приростом (≥ big_post_threshold) досбираем
через Apify: актор отдаёт свежие первыми, лимит = прирост × 2, секунды и копейки
вместо 45 минут строки. Посты без города не собираем: лид без города некуда девать.
"""
import asyncio
import logging
from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal
from ..models import IgAccount, LgCity, LgDonor, LgJob, LgPost
from ..services.apify import client as apify
from . import imports
from .common import (
    PRIORITY, as_float, as_int, chunks, enqueue_job, heartbeat, log_event, settings_all, utcnow,
)
from .posts_sync import apify_spent_today, run_collect

log = logging.getLogger("comments_collect")

POLL = 60
MAX_NEW_JOBS = 30       # заданий parser.im за проход
MAX_APIFY_POSTS = 20    # крупных постов через Apify за проход


async def run():
    while True:
        try:
            async with SessionLocal() as db:
                values = await settings_all(db)
                if values.get("comments_enabled") == "1":
                    await _pass(db, values)
                await heartbeat(db, "comments_collect")
        except Exception:
            log.exception("проход не удался")
        await asyncio.sleep(POLL)


async def _busy_post_ids(db: AsyncSession) -> set[int]:
    busy: set[int] = set()
    for j in (await db.execute(select(LgJob).where(
            LgJob.kind.in_(["comments", "apify_comments"]), LgJob.state.in_(["queued", "running"])))).scalars().all():
        busy.update((j.payload or {}).get("post_ids") or [])
    return busy


async def _pass(db: AsyncSession, values: dict) -> None:
    intake_days = as_int(values, "intake_days", 45)
    threshold = as_int(values, "big_post_threshold", 1000)
    min_first = max(1, as_int(values, "min_comments_first", 1))
    since = utcnow() - timedelta(days=intake_days)
    busy = await _busy_post_ids(db)

    rows = (await db.execute(
        select(LgPost, IgAccount.username)
        .join(LgDonor, LgDonor.id == LgPost.donor_id)
        .join(IgAccount, IgAccount.id == LgPost.account_id)
        .join(LgCity, LgCity.id == LgPost.city_id)
        .where(LgCity.is_active.is_(True), LgCity.collect_comments.is_(True),
               LgDonor.status.in_(["new", "unclassified", "monitored"]),
               LgPost.monitor_status.in_(["active", "forced"]),
               or_(LgPost.is_selling.is_(True), LgPost.monitor_status == "forced"),
               or_(LgPost.last_collected_at.is_(None) & (LgPost.published_at >= since),
                   LgPost.comments_delta > 0))
        .order_by(LgPost.donor_id, LgPost.published_at.desc()))).all()

    first, growth_small, growth_big = [], [], []
    now = utcnow()
    for p, username in rows:
        if p.id in busy:
            continue
        if p.last_collected_at is None:
            if (p.comments_count or 0) < min_first:
                p.last_collected_at, p.collected_comments = now, 0     # мало или нечего собирать; вырастет — возьмёт прирост
                continue
            first.append((p, username))
        elif p.comments_count >= threshold:
            growth_big.append((p, username))
        else:
            growth_small.append((p, username))
    await db.commit()

    made = 0
    for group, kind_prio in ((growth_small, PRIORITY["comments_growth"]), (first, PRIORITY["comments"])):
        for chunk in chunks(group, 10):
            if made >= MAX_NEW_JOBS:
                break
            donors = sorted({u for _, u in chunk})
            await enqueue_job(db, provider="parserim", kind="comments",
                              purpose=f"Комментарии: {len(chunk)} постов · @" + ", @".join(donors[:2]) + (f" +{len(donors) - 2}" if len(donors) > 2 else ""),
                              payload={"post_ids": [p.id for p, _ in chunk], "urls": [p.url for p, _ in chunk]},
                              lines=len(chunk), priority=kind_prio,
                              city_id=chunk[0][0].city_id if len({p.city_id for p, _ in chunk}) == 1 else None,
                              donor_id=chunk[0][0].donor_id if len({p.donor_id for p, _ in chunk}) == 1 else None)
            made += 1
    if made:
        await db.commit()
        log.info("поставлено заданий p2: %d (первый сбор %d постов, прирост %d)", made, len(first), len(growth_small))

    if growth_big:
        await _apify_big(db, values, growth_big[:MAX_APIFY_POSTS])


async def _apify_big(db: AsyncSession, values: dict, posts: list) -> None:
    cap = as_float(values, "apify_daily_cap_usd", 10.0)
    fresh_days = as_int(values, "comment_fresh_days_default", 30)
    for p, username in posts:
        spent = await apify_spent_today(db)
        if spent >= cap:
            await log_event(db, "apify.cap", f"Apify: потолок ${cap:.2f} в сутки достигнут (${spent:.2f}) — досбор крупных постов отложен",
                            level="warn")
            await db.commit()
            return
        limit = max(50, int(p.comments_delta or 0) * 2)
        job = await enqueue_job(db, provider="apify", kind="apify_comments", state="running",
                                purpose=f"Apify: свежие комментарии @{username} ({p.comments_count}, +{p.comments_delta}) · {p.shortcode}",
                                payload={"post_ids": [p.id], "limit": limit}, city_id=p.city_id, donor_id=p.donor_id)
        await db.commit()
        try:
            items, cost = await run_collect(apify.ACTOR_COMMENTS, {"directUrls": [p.url], "resultsLimit": limit})
            entries = imports.apify_comment_entries(p, items)
            n = await imports.insert_comments(db, job, entries, fresh_days, "apify")
            await imports.mark_posts_collected(db, [p.id])
            job.state, job.count, job.rows_imported, job.cost_usd = "done", len(entries), n, cost
            job.finished_at = job.imported_at = utcnow()
        except Exception as e:
            job.state, job.error, job.finished_at = "error", str(e)[:500], utcnow()
            await log_event(db, "job.error", f"Apify комментарии {p.shortcode}: {e}", entity="job", entity_id=job.id, level="error")
        await db.commit()
