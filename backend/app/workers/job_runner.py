"""Очередь заданий parser.im.

Один процесс держит учёт строк тарифа: запускает очередные задания по приоритету,
пока сумма их строк не упёрлась в `parserim_lines`, опрашивает запущенные, по
завершении забирает выгрузку и отдаёт её импорту. Apify-задания сюда не ходят —
их воркеры выполняют сами и только записывают в lg_jobs.
"""
import asyncio
import logging
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal
from ..models import LgDonor, LgJob, LgSearchTask
from ..services.parserim import client as pim
from . import imports
from .common import as_int, collection_on, heartbeat, log_event, settings_all, utcnow

log = logging.getLogger("job_runner")

POLL = 12
DONE_STATES = ("completed", "finished", "error", "deleted")
REMOTE_KEEP_DAYS = 3


async def run():
    while True:
        try:
            async with SessionLocal() as db:
                await _finish_manual(db)
                await _poll_running(db)
                await _start_queued(db)
                await _cleanup_remote(db)
                await heartbeat(db, "job_runner")
        except Exception:
            log.exception("проход не удался")
        await asyncio.sleep(POLL)


def _tids(job: LgJob) -> list[str]:
    return [t for t in (job.external_id or "").split(",") if t.strip()]


async def _create(job: LgJob) -> list[str]:
    p = job.payload or {}
    name = f"baza {job.kind} {job.id}"
    if job.kind == "search":
        if p.get("kind") == "keyword":
            return await pim.create_authors_by_keywords(name, p.get("values") or [])
        return await pim.create_authors_by_hashtags(name, p.get("values") or [])
    if job.kind == "filter":
        source = p.get("source_tid") or p.get("logins") or []
        return await pim.create_filter(name, source, lastpost_days=int(p.get("lastpost_days") or 30),
                                       followers_from=int(p.get("followers_from") or 0),
                                       followers_to=int(p.get("followers_to") or 0))
    if job.kind == "posts_intake":
        return await pim.create_posts(name, p.get("logins") or [], per_account=int(p.get("limit") or 60))
    if job.kind == "comments":
        return await pim.create_comments(name, p.get("urls") or [])
    raise pim.ParserImError(f"неизвестный тип задания {job.kind}")


async def _import(db: AsyncSession, job: LgJob, rows: list[dict]) -> int:
    values = await settings_all(db)
    if job.kind == "search":
        return await imports.import_search(db, job, rows)
    if job.kind == "filter":
        return await imports.import_filter(db, job, rows)
    if job.kind == "posts_intake":
        n = await imports.import_posts(db, job, rows, as_int(values, "intake_days", 45))
        await _after_posts(db, job, ok=True)
        return n
    if job.kind == "comments":
        return await imports.import_comments(db, job, rows, as_int(values, "comment_fresh_days_default", 30))
    return 0


async def _after_posts(db: AsyncSession, job: LgJob, ok: bool) -> None:
    """Доноры из задания p1 переходят к разметке ИИ; при ошибке — стоп с причиной."""
    ids = (job.payload or {}).get("donor_ids") or []
    if not ids:
        return
    for d in (await db.execute(select(LgDonor).where(LgDonor.id.in_(ids)))).scalars().all():
        if d.intake_stage != "posts_run":
            continue
        if ok:
            d.intake_stage = "ai"
        else:
            d.intake_stage = "error"
            d.status_reason = f"посты не собрались: {(job.error or '')[:200]}"


async def _complete(db: AsyncSession, job: LgJob, remote_error: str | None) -> None:
    rows: list[dict] = []
    fetch_error = None
    for tid in _tids(job):
        try:
            rows.extend(await pim.fetch_result(tid))
        except pim.ParserImError as e:
            fetch_error = str(e)
    try:
        n = await _import(db, job, rows)
    except Exception as e:
        log.exception("импорт задания %s", job.id)
        job.state, job.error, job.finished_at = "error", f"импорт: {e}"[:500], utcnow()
        if job.kind == "posts_intake":
            await _after_posts(db, job, ok=False)
        await log_event(db, "job.import_error", f"Задание {job.kind} #{job.id}: импорт не удался: {e}",
                        entity="job", entity_id=job.id, level="error")
        await db.commit()
        return
    job.rows_imported = n
    job.count = max(job.count or 0, len(rows))
    job.imported_at = utcnow()
    job.finished_at = job.finished_at or utcnow()
    if remote_error and not rows:
        job.state, job.error = "error", remote_error[:500]
        if job.kind == "posts_intake":
            await _after_posts(db, job, ok=False)
        await log_event(db, "job.error", f"Задание {job.kind} #{job.id} ({job.external_id}): {remote_error}",
                        entity="job", entity_id=job.id, level="error")
    else:
        job.state = "done"
        job.error = (fetch_error or remote_error or None)
        await log_event(db, "job.done", f"{job.purpose}: строк {len(rows)}, в базу {n}",
                        entity="job", entity_id=job.id)
    await db.commit()


async def _finish_manual(db: AsyncSession) -> None:
    jobs = (await db.execute(select(LgJob).where(
        LgJob.provider == "parserim", LgJob.state == "finished", LgJob.imported_at.is_(None)))).scalars().all()
    for job in jobs:
        for tid in _tids(job):
            try:
                await pim.finish_task(tid)
            except pim.ParserImError as e:
                log.warning("finish %s: %s", tid, e)
        await asyncio.sleep(3)   # parser.im дописывает результат после finish
        await _complete(db, job, None)


async def _poll_running(db: AsyncSession) -> None:
    jobs = (await db.execute(select(LgJob).where(
        LgJob.provider == "parserim", LgJob.state == "running").order_by(LgJob.id))).scalars().all()
    for job in jobs:
        statuses, counts, errors = [], 0, []
        for tid in _tids(job):
            try:
                st = await pim.task_status(tid)
            except pim.ParserImError as e:
                log.warning("status %s: %s", tid, e)
                statuses.append("unknown")
                continue
            s = str(st.get("tid_status") or st.get("status_text") or "").strip().lower()
            statuses.append(s or "unknown")
            counts += int(str(st.get("count") or st.get("tid_count") or st.get("total") or 0) or 0)
            if s == "error":
                errors.append(str(st.get("text") or st.get("details") or "parser.im: error"))
            await asyncio.sleep(0.4)
        if not statuses:
            continue
        job.count = max(job.count or 0, counts)
        if all(s in DONE_STATES for s in statuses):
            job.finished_at = utcnow()
            await _complete(db, job, errors[0] if errors else None)
        else:
            await db.commit()


async def _start_queued(db: AsyncSession) -> None:
    values = await settings_all(db)
    max_lines = as_int(values, "parserim_lines", 10)
    collecting = values.get("collection_enabled") == "1"
    comments_on = values.get("comments_enabled") == "1"
    busy = (await db.execute(select(func.coalesce(func.sum(LgJob.lines), 0)).where(
        LgJob.provider == "parserim", LgJob.state == "running"))).scalar() or 0
    queued = (await db.execute(select(LgJob).where(
        LgJob.provider == "parserim", LgJob.state == "queued")
        .order_by(LgJob.priority, LgJob.created_at))).scalars().all()
    for job in queued:
        if job.kind == "posts_intake" and not collecting:
            continue
        if job.kind == "comments" and not comments_on:
            continue
        if busy + (job.lines or 1) > max_lines:
            continue
        try:
            tids = await _create(job)
        except pim.ParserImError as e:
            msg = str(e)
            if "лимит" in msg:
                log.warning("parser.im: лимит запросов, ждём")
                break
            job.state, job.error, job.finished_at = "error", msg[:500], utcnow()
            if job.kind == "posts_intake":
                await _after_posts(db, job, ok=False)
            await log_event(db, "job.create_error", f"{job.purpose}: {msg}", entity="job", entity_id=job.id, level="error")
            await db.commit()
            continue
        job.external_id = ",".join(tids)[:60]
        job.state, job.started_at = "running", utcnow()
        busy += job.lines or 1
        await db.commit()
        log.info("запущено %s #%s → %s (%d строк, занято %d/%d)", job.kind, job.id, job.external_id, job.lines, busy, max_lines)
        await asyncio.sleep(1.5)


async def _cleanup_remote(db: AsyncSession) -> None:
    """Удалить у parser.im задания, чьи результаты давно забраны — чтобы их список не разрастался."""
    cutoff = utcnow() - timedelta(days=REMOTE_KEEP_DAYS)
    jobs = (await db.execute(select(LgJob).where(
        LgJob.provider == "parserim", LgJob.state.in_(["done", "error"]),
        LgJob.finished_at < cutoff, LgJob.external_id.isnot(None)).order_by(LgJob.id).limit(10))).scalars().all()
    for job in jobs:
        if (job.payload or {}).get("remote_deleted"):
            continue
        if (job.payload or {}).get("adopted"):
            # создано заказчиком на сайте parser.im — не наше, не трогаем
            job.payload = {**(job.payload or {}), "remote_deleted": True}
            continue
        for tid in _tids(job):
            try:
                await pim.delete_task(tid)
            except pim.ParserImError as e:
                log.info("delete %s: %s", tid, e)
            await asyncio.sleep(0.4)
        job.payload = {**(job.payload or {}), "remote_deleted": True}
    if jobs:
        await db.commit()
