"""Разовая петля нового донора: посты → разметка ИИ → первый сбор → на монитор.

Сами задания создают другие: p1 — здесь, разметку делает ai_posts, сбор
комментариев — comments_collect. Этот воркер только двигает intake_stage,
когда очередной этап у донора закончился.
"""
import asyncio
import logging
from datetime import timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal
from ..models import IgAccount, LgCity, LgDonor, LgJob, LgPost
from .common import as_int, chunks, collection_on, enqueue_job, heartbeat, log_event, settings_all, utcnow

log = logging.getLogger("donor_intake")

POLL = 30


async def run():
    while True:
        try:
            async with SessionLocal() as db:
                values = await settings_all(db)
                if values.get("collection_enabled") == "1":
                    await _stage_posts(db, values.get("unclassified_collect_posts") == "1")
                await _stage_ai_done(db)
                await _stage_comments_done(db, as_int(values, "intake_days", 45))
                await heartbeat(db, "donor_intake")
        except Exception:
            log.exception("проход не удался")
        await asyncio.sleep(POLL)


async def _stage_posts(db: AsyncSession, unclassified_on: bool) -> None:
    """Посты собираем только там, где это включено: флаг города, а для доноров без города —
    отдельная настройка unclassified_collect_posts (по умолчанию выключена)."""
    allowed = LgCity.collect_posts.is_(True)
    if unclassified_on:
        allowed = or_(LgDonor.city_id.is_(None), allowed)
    rows = (await db.execute(
        select(LgDonor, IgAccount.username).join(IgAccount, IgAccount.id == LgDonor.account_id)
        .outerjoin(LgCity, LgCity.id == LgDonor.city_id)
        .where(LgDonor.status.in_(["new", "unclassified"]), LgDonor.intake_stage == "posts", allowed)
        .order_by(LgDonor.id))).all()
    for chunk in chunks(rows, 10):
        logins = [u for _, u in chunk]
        ids = [d.id for d, _ in chunk]
        await enqueue_job(db, provider="parserim", kind="posts_intake",
                          purpose="Посты донора: " + ", ".join(logins[:3]) + (f" +{len(logins) - 3}" if len(logins) > 3 else ""),
                          payload={"logins": logins, "donor_ids": ids, "limit": 60}, lines=len(logins),
                          donor_id=ids[0] if len(ids) == 1 else None, city_id=chunk[0][0].city_id if len(ids) == 1 else None)
        for d, _ in chunk:
            d.intake_stage = "posts_run"
    if rows:
        await db.commit()


async def _stage_ai_done(db: AsyncSession) -> None:
    donors = (await db.execute(select(LgDonor).where(LgDonor.intake_stage == "ai"))).scalars().all()
    for d in donors:
        pending = (await db.execute(select(func.count()).select_from(LgPost).where(
            LgPost.donor_id == d.id, LgPost.is_selling.is_(None)))).scalar() or 0
        if pending:
            continue
        total = (await db.execute(select(func.count()).select_from(LgPost).where(LgPost.donor_id == d.id))).scalar() or 0
        selling = (await db.execute(select(func.count()).select_from(LgPost).where(
            LgPost.donor_id == d.id, LgPost.is_selling.is_(True)))).scalar() or 0
        d.intake_stage = "comments"
        await log_event(db, "donor.intake", f"Донор #{d.id}: постов {total}, продающих {selling} — к первому сбору",
                        entity="donor", entity_id=d.id)
    if donors:
        await db.commit()


async def _stage_comments_done(db: AsyncSession, intake_days: int) -> None:
    donors = (await db.execute(select(LgDonor).where(LgDonor.intake_stage == "comments"))).scalars().all()
    if not donors:
        return
    since = utcnow() - timedelta(days=intake_days)
    busy: set[int] = set()
    for j in (await db.execute(select(LgJob).where(
            LgJob.kind.in_(["comments", "apify_comments"]), LgJob.state.in_(["queued", "running"])))).scalars().all():
        busy.update((j.payload or {}).get("post_ids") or [])
    for d in donors:
        total = (await db.execute(select(func.count()).select_from(LgPost).where(LgPost.donor_id == d.id))).scalar() or 0
        if total == 0:
            d.intake_stage, d.status, d.status_changed_at = "done", "paused", utcnow()
            d.status_reason = f"нет постов за {intake_days} дн"
            await log_event(db, "donor.paused", f"Донор #{d.id}: постов за окно нет — пауза", entity="donor", entity_id=d.id, level="warn")
            continue
        pending_posts = (await db.execute(select(LgPost.id).where(
            LgPost.donor_id == d.id, LgPost.is_selling.is_(True), LgPost.monitor_status.in_(["active", "forced"]),
            LgPost.city_id.isnot(None), LgPost.published_at >= since, LgPost.last_collected_at.is_(None)))).scalars().all()
        if pending_posts or any(pid in busy for pid in pending_posts):
            continue
        d.intake_stage = "done"
        if d.status == "new":
            d.status, d.status_changed_at, d.status_reason = "monitored", utcnow(), "первый сбор завершён"
        await log_event(db, "donor.monitored", f"Донор #{d.id}: первый сбор завершён, на мониторе", entity="donor", entity_id=d.id)
    await db.commit()
