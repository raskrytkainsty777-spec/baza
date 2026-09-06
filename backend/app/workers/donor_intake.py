"""Разовая петля нового донора: посты → разметка ИИ → первый сбор → на монитор.

Сами задания создают другие: p1 — здесь, разметку делает ai_posts, сбор
комментариев — comments_collect. Этот воркер только двигает intake_stage,
когда очередной этап у донора закончился.
"""
import asyncio
import logging
from datetime import timedelta

from sqlalchemy import func, or_, select, update
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
                await _pause_silent(db)
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
        await _city_from_posts(db, d)
        d.intake_stage = "comments"
        await log_event(db, "donor.intake", f"Донор #{d.id}: постов {total}, продающих {selling} — к первому сбору",
                        entity="donor", entity_id=d.id)
    if donors:
        await db.commit()


CITY_SHARE = 0.8   # доля продающих постов в одном городе, чтобы закрепить город за донором


async def _city_from_posts(db: AsyncSession, d: LgDonor) -> None:
    """Город донора по его продающим постам (ИИ ставит город каждому). Правило заказчика:
    ≥ 80 % постов в одном городе → донор в этом городе; без города остаётся где был;
    с городом — переезжает, если ИИ уверенно видит другой. Посты идут за донором."""
    rows = (await db.execute(
        select(LgPost.city_id, func.count()).where(LgPost.donor_id == d.id, LgPost.is_selling.is_(True),
                                                   LgPost.city_id.isnot(None), LgPost.city_source == "ai")
        .group_by(LgPost.city_id).order_by(func.count().desc()))).all()
    if not rows:
        return
    top_city, top_n = rows[0]
    labelled = sum(n for _, n in rows)
    if top_n < 2 or top_n / labelled < CITY_SHARE or top_city == d.city_id:
        return
    city = await db.get(LgCity, top_city)
    was = await db.get(LgCity, d.city_id) if d.city_id else None
    d.city_id, d.status_changed_at = top_city, utcnow()
    if d.status == "unclassified":
        d.status = "new"
    d.status_reason = f"город по постам: {top_n} из {labelled} продающих" + (f", был {was.name}" if was else "")
    await db.execute(update(LgPost).where(LgPost.donor_id == d.id, LgPost.city_source != "ai")
                     .values(city_id=top_city, city_source="donor"))
    await log_event(db, "donor.city", f"Донор #{d.id}: город {city.name if city else top_city} по постам "
                    f"({top_n} из {labelled}){(' вместо ' + was.name) if was else ''}", entity="donor", entity_id=d.id)


SILENT_REASON = "нет постов"


async def _pause_silent(db: AsyncSession) -> None:
    """Донор на мониторе без постов дольше donor_pause_days города → пауза (замер 06.09.2026:
    после 14 дней молчания лидов нет вовсе). Обратно на монитор — imports, когда придёт новый пост."""
    rows = (await db.execute(
        select(LgDonor, LgCity.name, LgCity.donor_pause_days,
               select(func.max(LgPost.published_at)).where(LgPost.donor_id == LgDonor.id).scalar_subquery().label("lp"))
        .join(LgCity, LgCity.id == LgDonor.city_id)
        .where(LgDonor.status == "monitored", LgCity.donor_pause_days > 0))).all()
    now = utcnow()
    n = 0
    for d, city, days, lp in rows:
        if lp is None or lp >= now - timedelta(days=int(days)):
            continue
        d.status, d.status_changed_at = "paused", now
        d.status_reason = f"{SILENT_REASON} {int(days)} дн (последний {lp.date().isoformat()})"
        n += 1
    if n:
        await log_event(db, "donor.paused", f"На паузу за молчание: {n} доноров")
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
