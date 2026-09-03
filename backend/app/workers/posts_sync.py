"""Суточный цикл Apify: новые посты доноров на мониторе и счётчики известных постов.

Порядок утром задаёт расписание: новые посты → счётчики → (comments_collect
подхватит прирост). Каждый прогон — строка в lg_jobs со стоимостью, суточный
потолок расходов — настройка apify_daily_cap_usd.
"""
import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal
from ..models import IgAccount, LgCity, LgDonor, LgJob, LgPost
from ..services.apify import client as apify
from . import imports
from .common import (
    as_float, chunks, enqueue_job, get_value, heartbeat, log_event, set_value, settings_all,
    shortcode_of, take_flag, to_int, utcnow,
)
from .cron import MSK, due_slot, slot_key

log = logging.getLogger("posts_sync")

POLL = 60
PROFILES_PER_RUN = 25
POSTS_PER_RUN = 60


async def run_collect(actor: str, run_input: dict, max_s: float = 1500) -> tuple[list[dict], float]:
    """Запуск актора с ожиданием и стоимостью прогона."""
    run = await apify.run_async(actor, run_input)
    run = await apify.wait_run(run["id"], max_s=max_s)
    if run.get("status") != "SUCCEEDED":
        raise apify.ApifyError(f"Apify run {run.get('id')}: {run.get('status')}")
    items = await apify.dataset_items(run["defaultDatasetId"])
    return items, apify.run_cost_usd(run)


async def apify_spent_today(db: AsyncSession) -> float:
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    v = (await db.execute(select(func.coalesce(func.sum(LgJob.cost_usd), 0)).where(
        LgJob.provider == "apify", LgJob.created_at >= today))).scalar()
    return float(v or 0)


async def run():
    while True:
        try:
            async with SessionLocal() as db:
                values = await settings_all(db)
                for name, fn in (("new_posts", _new_posts), ("counters", _counters)):
                    forced = await take_flag(db, f"run_now.{name}")
                    last = await get_value(db, f"last_run.{name}")
                    key = due_slot(values.get(f"schedule.{name}", ""), last)
                    if last is None and key and not forced:
                        # первый запуск после включения — не бросаемся в прошедший слот
                        await set_value(db, f"last_run.{name}", key)
                        await db.commit()
                        continue
                    if forced or (key and values.get("collection_enabled") == "1"):
                        await set_value(db, f"last_run.{name}", key or slot_key(datetime.now(MSK), datetime.now(MSK).time()))
                        await db.commit()
                        await fn(db, values)
                await heartbeat(db, "posts_sync")
        except Exception:
            log.exception("проход не удался")
        await asyncio.sleep(POLL)


async def _monitored(db: AsyncSession) -> dict[str, LgDonor]:
    rows = (await db.execute(
        select(LgDonor, IgAccount.username).join(IgAccount, IgAccount.id == LgDonor.account_id)
        .join(LgCity, LgCity.id == LgDonor.city_id)
        .where(LgDonor.status == "monitored", LgCity.is_active.is_(True), LgCity.collect_posts.is_(True)))).all()
    return {u.lower(): d for d, u in rows}


async def _cap_ok(db: AsyncSession, values: dict, what: str) -> bool:
    cap = as_float(values, "apify_daily_cap_usd", 10.0)
    spent = await apify_spent_today(db)
    if spent >= cap:
        await log_event(db, "apify.cap", f"Apify: потолок ${cap:.2f} достигнут (${spent:.2f}) — {what} пропущен", level="warn")
        await db.commit()
        return False
    return True


async def _new_posts(db: AsyncSession, values: dict) -> None:
    donors = await _monitored(db)
    if not donors:
        return
    total_new = 0
    for chunk in chunks(sorted(donors), PROFILES_PER_RUN):
        if not await _cap_ok(db, values, "обход новых постов"):
            return
        job = await enqueue_job(db, provider="apify", kind="apify_new_posts", state="running",
                                purpose=f"Apify: новые посты у {len(chunk)} доноров", payload={"usernames": chunk})
        await db.commit()
        try:
            items, cost = await run_collect(apify.ACTOR_SCRAPER, {
                "directUrls": [f"https://www.instagram.com/{u}/" for u in chunk],
                "resultsType": "posts", "resultsLimit": 12, "onlyPostsNewerThan": "2 days", "addParentData": False,
            })
            n = await imports.import_apify_posts(db, job, items, donors)
            job.state, job.count, job.rows_imported, job.cost_usd = "done", len(items), n, cost
            job.finished_at = job.imported_at = utcnow()
            total_new += n
        except Exception as e:
            job.state, job.error, job.finished_at = "error", str(e)[:500], utcnow()
            await log_event(db, "job.error", f"Apify новые посты: {e}", entity="job", entity_id=job.id, level="error")
        await db.commit()
    await log_event(db, "posts.synced", f"Новых постов за обход: {total_new} (доноров {len(donors)})")
    await db.commit()


async def _counters(db: AsyncSession, values: dict) -> None:
    rows = (await db.execute(
        select(LgPost, LgCity.post_freeze_days).join(LgDonor, LgDonor.id == LgPost.donor_id)
        .join(LgCity, LgCity.id == LgPost.city_id)
        .where(LgDonor.status == "monitored", LgCity.is_active.is_(True), LgCity.collect_comments.is_(True),
               LgPost.monitor_status.in_(["active", "forced"]), LgPost.is_selling.is_(True)))).all()
    if not rows:
        return
    by_sc = {p.shortcode: (p, freeze) for p, freeze in rows}
    today = datetime.now(MSK).date()
    grown = frozen = 0
    for chunk in chunks(list(by_sc.values()), POSTS_PER_RUN):
        if not await _cap_ok(db, values, "сверка счётчиков"):
            return
        job = await enqueue_job(db, provider="apify", kind="apify_counters", state="running",
                                purpose=f"Apify: счётчики {len(chunk)} постов", payload={"post_ids": [p.id for p, _ in chunk]})
        await db.commit()
        try:
            items, cost = await run_collect(apify.ACTOR_SCRAPER, {
                "directUrls": [p.url for p, _ in chunk], "resultsType": "posts", "resultsLimit": 1, "addParentData": False,
            })
            seen = 0
            for it in items:
                sc = it.get("shortCode") or shortcode_of(it.get("url") or "")
                hit = by_sc.get(sc or "")
                if not hit:
                    continue
                p, freeze = hit
                seen += 1
                new = to_int(it.get("commentsCount"))
                if new is None:
                    continue
                checked_today = bool(p.last_checked_at and p.last_checked_at.astimezone(MSK).date() == today)
                p.comments_count = max(new, 0)
                p.comments_delta = max(p.comments_count - (p.comments_count_prev or 0), 0)
                p.likes = to_int(it.get("likesCount")) or p.likes
                p.views = to_int(it.get("videoViewCount") or it.get("videoPlayCount")) or p.views
                if p.comments_delta > 0:
                    p.last_growth_at, p.zero_growth_days = utcnow(), 0
                    grown += 1
                elif not checked_today:
                    p.zero_growth_days = (p.zero_growth_days or 0) + 1
                p.last_checked_at = utcnow()
                if p.monitor_status == "active" and freeze and p.zero_growth_days >= freeze:
                    p.monitor_status = "frozen"
                    frozen += 1
                    await log_event(db, "post.frozen", f"Пост {p.shortcode}: {p.zero_growth_days} дн без прироста — снят с обхода",
                                    entity="post", entity_id=p.id)
            job.state, job.count, job.rows_imported, job.cost_usd = "done", len(items), seen, cost
            job.finished_at = job.imported_at = utcnow()
        except Exception as e:
            job.state, job.error, job.finished_at = "error", str(e)[:500], utcnow()
            await log_event(db, "job.error", f"Apify счётчики: {e}", entity="job", entity_id=job.id, level="error")
        await db.commit()
    await log_event(db, "posts.counters", f"Сверка счётчиков: постов {len(rows)}, с приростом {grown}, заморожено {frozen}")
    await db.commit()
