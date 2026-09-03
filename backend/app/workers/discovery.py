"""Задачи поиска доноров: сбор → фильтр f1 → ИИ «кто и где» → готово.

Теги и ключи уходят в parser.im (p3/p5), рекомендации — в Apify. Дальше у всех
один путь: f1 догружает имя, описание, адрес, подписчиков; ИИ решает, наш ли это
человек по деятельности и в каком он городе. Уверенных распределяет оператор
кнопкой, неясные ждут его решения.
"""
import asyncio
import json
import logging

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal
from ..models import LgCandidate, LgCity, LgJob, LgReject, LgSearchTask
from ..services.ai.client import AiError, chat_json, prompt
from ..services.apify import client as apify
from . import imports
from .common import (
    add_ai_cost, ai_on, chunks, city_by_name, enqueue_job, heartbeat, log_event, settings_all, utcnow,
)

log = logging.getLogger("discovery")

POLL = 20
AI_BATCH = 10
AI_CONCURRENCY = 4
CONFIDENT = 0.9
F1_LASTPOST_DAYS = 30


async def run():
    while True:
        try:
            async with SessionLocal() as db:
                tasks = (await db.execute(select(LgSearchTask).where(
                    LgSearchTask.stage.in_(["collecting", "filtering", "classifying"])).order_by(LgSearchTask.id))).scalars().all()
                for t in tasks:
                    await _step(db, t)
                await heartbeat(db, "discovery")
        except Exception:
            log.exception("проход не удался")
        await asyncio.sleep(POLL)


def _finished(jobs: list[LgJob]) -> bool:
    return bool(jobs) and all(j.state in ("done", "error", "finished") for j in jobs)


async def _set_stage(db: AsyncSession, t: LgSearchTask, stage: str, note: str = "") -> None:
    t.stage, t.stage_changed_at = stage, utcnow()
    await log_event(db, "search.stage", f"{t.title}: → {stage}{(' · ' + note) if note else ''}",
                    entity="search_task", entity_id=t.id)
    await db.commit()


async def _step(db: AsyncSession, t: LgSearchTask) -> None:
    jobs = (await db.execute(select(LgJob).where(LgJob.search_task_id == t.id))).scalars().all()
    if t.stage == "collecting":
        sjobs = [j for j in jobs if j.kind in ("search", "apify_recommend")]
        if not sjobs:
            await _start_collect(db, t)
        elif _finished(sjobs):
            errors = [j.error for j in sjobs if j.state == "error" and j.error]
            t.collected = (await db.execute(select(func.count()).select_from(LgCandidate)
                                            .where(LgCandidate.task_id == t.id))).scalar() or 0
            if t.collected == 0:
                t.error = "Ничего не найдено" + (f": {errors[0]}" if errors else "")
                await _set_stage(db, t, "ready", "пусто")
            else:
                t.error = errors[0][:500] if errors else None
                await _set_stage(db, t, "filtering", f"собрано {t.collected}")
    elif t.stage == "filtering":
        fjobs = [j for j in jobs if j.kind == "filter"]
        if not fjobs:
            await _start_filter(db, t, jobs)
        elif _finished(fjobs):
            await _close_filter(db, t, fjobs)
    elif t.stage == "classifying":
        await _classify_batch(db, t)


# ── сбор ─────────────────────────────────────────────────────────────────────

async def _start_collect(db: AsyncSession, t: LgSearchTask) -> None:
    inp = t.input or {}
    if t.kind in ("hashtag", "keyword"):
        values = [v for v in (inp.get("values") or []) if v]
        for chunk in chunks(values, 10):
            shown = ", ".join(("#" + v) if t.kind == "hashtag" else v for v in chunk[:3])
            await enqueue_job(db, provider="parserim", kind="search",
                              purpose=f"Поиск авторов: {shown}" + (f" +{len(chunk) - 3}" if len(chunk) > 3 else ""),
                              payload={"kind": t.kind, "values": chunk}, lines=len(chunk), search_task_id=t.id)
        await db.commit()
        return
    # рекомендации — Apify, сразу
    seeds = [s for s in (inp.get("seeds") or []) if s]
    job = await enqueue_job(db, provider="apify", kind="apify_recommend", state="running",
                            purpose=f"Рекомендации от {len(seeds)} сидов", payload={"seeds": seeds},
                            search_task_id=t.id)
    await db.commit()
    try:
        items = await apify.related_profiles(seeds)
        entries = []
        for it in items:
            seed = it.get("username") or ""
            for rp in it.get("relatedProfiles") or []:
                if rp.get("username"):
                    entries.append({"username": rp["username"], "ig_id": rp.get("id"), "found_by": f"рек. {seed}"})
        n = await imports.add_candidates(db, t, entries)
        job.state, job.count, job.rows_imported, job.finished_at, job.imported_at = "done", len(entries), n, utcnow(), utcnow()
    except Exception as e:
        job.state, job.error, job.finished_at = "error", str(e)[:500], utcnow()
        await log_event(db, "job.error", f"Рекомендации Apify: {e}", entity="job", entity_id=job.id, level="error")
    await db.commit()


# ── фильтр ───────────────────────────────────────────────────────────────────

async def _start_filter(db: AsyncSession, t: LgSearchTask, jobs: list[LgJob]) -> None:
    made = 0
    if t.kind in ("hashtag", "keyword"):
        for j in jobs:
            if j.kind != "search" or j.state != "done" or not j.external_id:
                continue
            for tid in j.external_id.split(","):
                await enqueue_job(db, provider="parserim", kind="filter",
                                  purpose=f"Фильтр f1 по заданию {tid} · {t.title}",
                                  payload={"source_tid": tid, "lastpost_days": F1_LASTPOST_DAYS},
                                  lines=1, search_task_id=t.id)
                made += 1
    if not made:
        # рекомендации, либо поиск без tid — фильтруем по списку логинов
        logins = (await db.execute(select(LgCandidate.username).where(
            LgCandidate.task_id == t.id, LgCandidate.state == "collected"))).scalars().all()
        for chunk in chunks(logins, 10):
            await enqueue_job(db, provider="parserim", kind="filter",
                              purpose=f"Фильтр f1: {len(chunk)} логинов · {t.title}",
                              payload={"logins": chunk, "lastpost_days": F1_LASTPOST_DAYS},
                              lines=len(chunk), search_task_id=t.id)
            made += 1
    if not made:
        t.error = "Нечего фильтровать"
        await _set_stage(db, t, "ready", "пусто")
        return
    await db.commit()


async def _close_filter(db: AsyncSession, t: LgSearchTask, fjobs: list[LgJob]) -> None:
    """Кто не вернулся из f1 — неактивен или закрыт: отклоняем и запоминаем."""
    errors = [j.error for j in fjobs if j.state == "error" and j.error]
    left = (await db.execute(select(LgCandidate).where(
        LgCandidate.task_id == t.id, LgCandidate.state == "collected"))).scalars().all()
    if errors and len(left) == (await db.execute(select(func.count()).select_from(LgCandidate)
                                                 .where(LgCandidate.task_id == t.id))).scalar():
        t.error = f"Фильтр не отработал: {errors[0]}"[:500]
        await _set_stage(db, t, "ready", "ошибка f1")
        return
    for c in left:
        c.state, c.reject_reason = "rejected", "inactive"
    if left:
        await db.execute(insert(LgReject).values([
            {"username": c.username, "reason": "inactive", "search_task_id": t.id} for c in left
        ]).on_conflict_do_nothing(index_elements=["username"]))
    t.passed = (await db.execute(select(func.count()).select_from(LgCandidate).where(
        LgCandidate.task_id == t.id, LgCandidate.state == "filtered"))).scalar() or 0
    t.rejected_inactive = len(left)
    t.error = errors[0][:500] if errors else None
    await _set_stage(db, t, "classifying", f"прошли {t.passed}, неактивны {len(left)}")


# ── ИИ: кто и где ────────────────────────────────────────────────────────────

async def _classify_batch(db: AsyncSession, t: LgSearchTask) -> None:
    if not await ai_on(db):
        return
    cands = (await db.execute(select(LgCandidate).where(
        LgCandidate.task_id == t.id, LgCandidate.state == "filtered").order_by(LgCandidate.id).limit(AI_BATCH))).scalars().all()
    if not cands:
        await _close_classify(db, t)
        return
    values = await settings_all(db)
    cities = ", ".join((await db.execute(select(LgCity.name).order_by(LgCity.name))).scalars().all())
    system = (prompt("activity", values) + "\n\n" + prompt("city", values, cities=cities)
              + "\n\nФормат ответа: {\"activity_kind\": \"…\", \"ok\": true, \"city\": \"…\" или null, "
                "\"confidence\": 0.0, \"reason\": \"коротко почему\"}")
    sem = asyncio.Semaphore(AI_CONCURRENCY)

    async def ask(c: LgCandidate):
        async with sem:
            user = json.dumps({"username": c.username, "full_name": c.full_name, "bio": c.bio, "address": c.address,
                               "followers": c.followers, "posts": c.posts_count,
                               "last_post": c.last_post_at.isoformat() if c.last_post_at else None},
                              ensure_ascii=False)
            return await chat_json(system, user)

    results = await asyncio.gather(*(ask(c) for c in cands), return_exceptions=True)
    cost, failed = 0.0, 0
    for c, r in zip(cands, results):
        if isinstance(r, BaseException):
            failed += 1
            log.warning("ИИ по %s: %s", c.username, r)
            continue
        cost += getattr(r, "cost", 0.0)
        c.activity_kind = (str(r.get("activity_kind") or "other"))[:30]
        c.activity_ok = bool(r.get("ok"))
        c.city_name_raw = (str(r.get("city") or "")[:80]) or None
        try:
            c.city_confidence = max(0.0, min(1.0, float(r.get("confidence") or 0)))
        except (TypeError, ValueError):
            c.city_confidence = 0.0
        c.ai_reason = (str(r.get("reason") or ""))[:1000] or None
        city = await city_by_name(db, c.city_name_raw)
        c.city_id = city.id if city else None
        if not c.activity_ok:
            c.state, c.reject_reason = "rejected", "activity"
            await db.execute(insert(LgReject).values(username=c.username, reason="activity",
                                                     detail=c.activity_kind, search_task_id=t.id)
                             .on_conflict_do_nothing(index_elements=["username"]))
        elif c.city_id and c.city_confidence >= CONFIDENT:
            c.state = "classified"
        else:
            c.state = "unclear"
    await add_ai_cost(db, cost)
    await db.commit()
    if failed == len(cands):
        await log_event(db, "ai.error", f"{t.title}: ИИ не ответила ни по одному из {failed} кандидатов",
                        entity="search_task", entity_id=t.id, level="error")
        await db.commit()
        await asyncio.sleep(60)


async def _close_classify(db: AsyncSession, t: LgSearchTask) -> None:
    def cnt(*where):
        return select(func.count()).select_from(LgCandidate).where(LgCandidate.task_id == t.id, *where)
    t.confident = (await db.execute(cnt(LgCandidate.state == "classified"))).scalar() or 0
    t.unclear = (await db.execute(cnt(LgCandidate.state == "unclear"))).scalar() or 0
    t.rejected_activity = (await db.execute(cnt(LgCandidate.state == "rejected",
                                                LgCandidate.reject_reason == "activity"))).scalar() or 0
    await _set_stage(db, t, "ready", f"уверенно {t.confident}, неясно {t.unclear}, не те {t.rejected_activity}")
