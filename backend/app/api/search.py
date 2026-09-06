"""Задачи поиска доноров — карточки с этапами; кандидаты живут внутри задачи.

Создание кладёт задачу в стадию collecting, дальше её ведёт воркер discovery:
сбор → f1 → ИИ «кто и где» → ready. Здесь — только создание, чтение и кнопки
«Распределить», «В город руками», «Отклонить».
"""
import csv
import io
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import IgAccount, LgCandidate, LgCity, LgDonor, LgEvent, LgJob, LgReject, LgSearchTask
from ..services.donors import CONFIDENT, distribute_task, make_donor
from ..services.parserim import client as pim
from .deps import require_token

router = APIRouter(prefix="/api/search", tags=["search"], dependencies=[Depends(require_token)])



class TaskCreate(BaseModel):
    kind: str                       # hashtag | keyword | recommendation | apify_keyword
    values: list[str] = []          # теги / слова
    seed_donor_ids: list[int] = []  # для recommendation
    lastpost_days: int = 30         # apify_keyword: последний пост не старше
    min_comments: int = 20          # apify_keyword: на лучшем из 12 последних постов


class Assign(BaseModel):
    candidate_ids: list[int]
    city_id: int | None = None      # None → неразобранный донор


class Adopt(BaseModel):
    tid: str                        # id задания на parser.im
    kind: str | None = None         # hashtag | keyword; пусто — по типу задания (p3 / p5)
    lines: int = 1                  # сколько строк тарифа занимает: тегов / ключей внутри задания


def _task_dto(t: LgSearchTask) -> dict:
    return {
        "id": t.id, "kind": t.kind, "title": t.title, "input": t.input, "stage": t.stage, "error": t.error,
        "collected": t.collected, "passed": t.passed, "rejected_inactive": t.rejected_inactive,
        "rejected_activity": t.rejected_activity, "confident": t.confident, "unclear": t.unclear,
        "distributed": t.distributed, "created_at": t.created_at, "stage_changed_at": t.stage_changed_at,
    }


@router.get("/tasks")
async def list_tasks(limit: int = Query(50, le=200), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(LgSearchTask).order_by(desc(LgSearchTask.created_at)).limit(limit))).scalars().all()
    unclear_total = (await db.execute(
        select(func.count()).select_from(LgCandidate).where(LgCandidate.state == "unclear"))).scalar() or 0
    # пока идёт сбор, число авторов живёт в задании parser.im, а не у нас
    live = dict((await db.execute(
        select(LgJob.search_task_id, func.coalesce(func.sum(LgJob.count), 0))
        .where(LgJob.kind.in_(["search", "apify_recommend", "apify_search"]), LgJob.search_task_id.isnot(None))
        .group_by(LgJob.search_task_id))).all())
    items = []
    for t in rows:
        d = _task_dto(t)
        d["collected_live"] = int(live.get(t.id, 0))
        if t.stage == "collecting":
            d["collected"] = max(d["collected"] or 0, d["collected_live"])
        items.append(d)
    return {"items": items, "unclear_total": unclear_total}


@router.post("/tasks", status_code=201)
async def create_task(body: TaskCreate, db: AsyncSession = Depends(get_db)):
    if body.kind not in ("hashtag", "keyword", "recommendation", "apify_keyword"):
        raise HTTPException(400, "kind: hashtag | keyword | recommendation | apify_keyword")
    values = [v.strip().lstrip("#") for v in body.values if v.strip()]
    if body.kind == "recommendation":
        if not body.seed_donor_ids:
            raise HTTPException(400, "Выберите доноров-сидов")
        seeds = (await db.execute(
            select(IgAccount.username).join(LgDonor, LgDonor.account_id == IgAccount.id)
            .where(LgDonor.id.in_(body.seed_donor_ids)))).scalars().all()
        if not seeds:
            raise HTTPException(404, "Сиды не найдены")
        title = "Рекомендации: " + ", ".join(seeds[:4]) + (f" +{len(seeds) - 4}" if len(seeds) > 4 else "")
        payload = {"seeds": seeds, "seed_donor_ids": body.seed_donor_ids}
    elif body.kind == "apify_keyword":
        if not values:
            raise HTTPException(400, "Введите ключевые слова")
        title = "Ключи Apify: " + ", ".join(values[:3]) + (f" +{len(values) - 3}" if len(values) > 3 else "")
        payload = {"values": values, "lastpost_days": max(1, body.lastpost_days), "min_comments": max(0, body.min_comments)}
    else:
        if not values:
            raise HTTPException(400, "Введите теги или ключевые слова")
        label = "Теги" if body.kind == "hashtag" else "Ключи"
        shown = [("#" + v) if body.kind == "hashtag" else v for v in values]
        title = f"{label}: " + ", ".join(shown[:3]) + (f" +{len(shown) - 3}" if len(shown) > 3 else "")
        payload = {"values": values}
    t = LgSearchTask(kind=body.kind, input=payload, title=title, stage="collecting",
                     stage_changed_at=datetime.now(timezone.utc))
    db.add(t)
    await db.flush()
    db.add(LgEvent(kind="search.created", entity="search_task", entity_id=t.id, message=f"Задача поиска: {title}"))
    await db.commit()
    return _task_dto(t)


@router.post("/adopt", status_code=201)
async def adopt(body: Adopt, db: AsyncSession = Depends(get_db)):
    """Подключить задание, созданное на сайте parser.im: без пересбора встаёт в нашу
    цепочку — воркер заберёт результат, когда оно завершится, дальше f1 → ИИ → распределение."""
    tid = body.tid.strip()
    if not tid.isdigit():
        raise HTTPException(400, "tid — число из списка заданий parser.im")
    dup = (await db.execute(select(LgJob).where(LgJob.provider == "parserim", LgJob.external_id == tid))).scalar_one_or_none()
    if dup:
        raise HTTPException(409, f"Задание {tid} уже подключено (задача поиска #{dup.search_task_id})")
    try:
        st = await pim.task_status(tid)
    except pim.ParserImError as e:
        raise HTTPException(400, f"parser.im: {e}")
    kind = body.kind or {"p3": "hashtag", "p5": "keyword"}.get(str(st.get("type") or ""))
    if kind not in ("hashtag", "keyword"):
        raise HTTPException(400, "Подключать можно p3 (авторы по тегам) и p5 (авторы по ключам)")
    name = (st.get("name") or f"parser.im {tid}").strip()
    title = f"{'Теги' if kind == 'hashtag' else 'Ключи'}: {name} · parser.im {tid}"
    t = LgSearchTask(kind=kind, input={"values": [], "adopted_tid": tid, "parserim_name": name}, title=title,
                     stage="collecting", stage_changed_at=datetime.now(timezone.utc))
    db.add(t)
    await db.flush()
    try:
        count = int(str(st.get("count") or 0))
    except ValueError:
        count = 0
    db.add(LgJob(provider="parserim", external_id=tid, kind="search", purpose=f"Поиск авторов (подключено): {name}",
                 payload={"kind": kind, "values": [], "adopted": True}, lines=max(1, body.lines), priority=40, state="running",
                 search_task_id=t.id, count=count, started_at=datetime.now(timezone.utc)))
    db.add(LgEvent(kind="search.adopted", entity="search_task", entity_id=t.id,
                   message=f"Подключено задание parser.im {tid} «{name}», статус {st.get('tid_status')}, авторов {count}"))
    await db.commit()
    return _task_dto(t)


@router.get("/tasks/{task_id}")
async def get_task(task_id: int, db: AsyncSession = Depends(get_db)):
    t = await db.get(LgSearchTask, task_id)
    if not t:
        raise HTTPException(404, "Задача не найдена")
    by_city = (await db.execute(
        select(LgCity.name, func.count()).join(LgCandidate, LgCandidate.city_id == LgCity.id)
        .where(LgCandidate.task_id == task_id, LgCandidate.state == "classified",
               LgCandidate.city_confidence >= CONFIDENT)
        .group_by(LgCity.name).order_by(desc(func.count())))).all()
    return {**_task_dto(t), "ready_by_city": [{"city": c, "count": n} for c, n in by_city]}


STATE_LABEL = {"collected": "собран", "filtered": "прошёл f1", "classified": "разобран ИИ",
               "distributed": "стал донором", "unclear": "неясно", "rejected": "отклонён"}
REJECT_LABEL = {"inactive": "неактивен", "low_comments": "мало комментариев", "activity": "не та деятельность", "manual": "вручную",
                "private": "закрытый", "not_found": "не найден"}


@router.get("/tasks/{task_id}/report")
async def task_report(task_id: int, db: AsyncSession = Depends(get_db)):
    """Итог задачи: сколько кандидатов на каком этапе, по каким городам разошлись,
    что дал каждый сид/тег, почему отклоняли. Для анализа после «готово»."""
    t = await db.get(LgSearchTask, task_id)
    if not t:
        raise HTTPException(404, "Задача не найдена")
    C = LgCandidate
    states = (await db.execute(select(C.state, func.count()).where(C.task_id == task_id).group_by(C.state))).all()
    reasons = (await db.execute(select(C.reject_reason, func.count()).where(
        C.task_id == task_id, C.state == "rejected").group_by(C.reject_reason))).all()
    city_expr = func.coalesce(LgCity.name, C.city_name_raw, "—")
    c_dist, c_wait, c_unc = (func.count().filter(C.state == "distributed"), func.count().filter(C.state == "classified"),
                             func.count().filter(C.state == "unclear"))
    cities = (await db.execute(
        select(city_expr, c_dist, c_wait, c_unc)
        .select_from(C).outerjoin(LgCity, LgCity.id == C.city_id)
        .where(C.task_id == task_id, C.state.in_(("distributed", "classified", "unclear")))
        .group_by(city_expr).order_by(desc(c_dist), desc(c_wait), desc(c_unc)))).all()
    src_expr = func.coalesce(C.found_by, "—")
    s_all, s_pass, s_dist = (func.count(), func.count().filter(C.state.in_(("filtered", "classified", "distributed", "unclear"))),
                             func.count().filter(C.state == "distributed"))
    sources = (await db.execute(
        select(src_expr, s_all, s_pass, s_dist)
        .where(C.task_id == task_id).group_by(src_expr).order_by(desc(s_dist), desc(s_all)))).all()
    return {
        "task": _task_dto(t),
        "states": [{"state": st, "label": STATE_LABEL.get(st, st), "count": n} for st, n in states],
        "reject_reasons": [{"reason": r, "label": REJECT_LABEL.get(r or "", r or "—"), "count": n} for r, n in reasons],
        "cities": [{"city": c, "distributed": d, "waiting": w, "unclear": u} for c, d, w, u in cities],
        "sources": [{"source": s, "collected": n, "passed": p, "distributed": d} for s, n, p, d in sources],
    }


@router.get("/tasks/{task_id}/export.csv")
async def task_export(task_id: int, db: AsyncSession = Depends(get_db)):
    t = await db.get(LgSearchTask, task_id)
    if not t:
        raise HTTPException(404, "Задача не найдена")
    rows = (await db.execute(
        select(LgCandidate, LgCity.name).outerjoin(LgCity, LgCity.id == LgCandidate.city_id)
        .where(LgCandidate.task_id == task_id)
        .order_by(LgCandidate.state, desc(LgCandidate.city_confidence).nullslast(), LgCandidate.id))).all()
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["логин", "найден через", "имя", "подписчики", "макс. комм.", "последний пост", "деятельность", "подходит",
                "город", "город по ИИ", "уверенность %", "этап", "причина отказа", "комментарий ИИ", "адрес", "описание"])
    for c, city in rows:
        w.writerow([c.username, c.found_by or "", c.full_name or "", c.followers if c.followers is not None else "",
                    c.max_comments if c.max_comments is not None else "",
                    c.last_post_at.strftime("%d.%m.%Y") if c.last_post_at else "", c.activity_kind or "",
                    "" if c.activity_ok is None else ("да" if c.activity_ok else "нет"),
                    city or "", c.city_name_raw or "", round(c.city_confidence * 100) if c.city_confidence is not None else "",
                    STATE_LABEL.get(c.state, c.state), REJECT_LABEL.get(c.reject_reason or "", c.reject_reason or ""),
                    (c.ai_reason or "").replace("\n", " "), c.address or "", (c.bio or "").replace("\n", " ")[:300]])
    data = ("﻿" + buf.getvalue()).encode("utf-8")
    return StreamingResponse(iter([data]), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename=search_task_{t.id}.csv"})


@router.get("/candidates")
async def list_candidates(
    task_id: int | None = None,
    state: str | None = None,
    limit: int = Query(200, le=1000),
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """Кандидаты — обычно только неразобранные: логины по задаче в UI не показываем."""
    stmt = select(LgCandidate, LgCity.name).outerjoin(LgCity, LgCity.id == LgCandidate.city_id)
    if task_id:
        stmt = stmt.where(LgCandidate.task_id == task_id)
    if state:
        stmt = stmt.where(LgCandidate.state == state)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    rows = (await db.execute(stmt.order_by(desc(LgCandidate.city_confidence).nullslast(), LgCandidate.id)
                             .limit(limit).offset(offset))).all()
    return {"total": total, "items": [{
        "id": c.id, "task_id": c.task_id, "username": c.username, "found_by": c.found_by,
        "full_name": c.full_name, "bio": (c.bio or "")[:200], "address": c.address,
        "followers": c.followers, "last_post_at": c.last_post_at,
        "activity_kind": c.activity_kind, "activity_ok": c.activity_ok,
        "city_id": c.city_id, "city": city_name, "city_name_raw": c.city_name_raw,
        "city_confidence": c.city_confidence, "ai_reason": c.ai_reason,
        "state": c.state, "reject_reason": c.reject_reason,
    } for c, city_name in rows]}


@router.post("/tasks/{task_id}/distribute")
async def distribute(task_id: int, db: AsyncSession = Depends(get_db)):
    """Уверенные (≥0.9, деятельность подходит) → в свои города статусом «новый».
    При включённом auto_distribute воркер делает это сам; кнопка — запасной путь."""
    t = await db.get(LgSearchTask, task_id)
    if not t:
        raise HTTPException(404, "Задача не найдена")
    made, considered = await distribute_task(db, t)
    await db.commit()
    return {"distributed": made, "considered": considered}


@router.post("/candidates/assign")
async def assign(body: Assign, db: AsyncSession = Depends(get_db)):
    """Руками: в конкретный город или (city_id=null) — неразобранным донором,
    чьи продающие посты получат город от ИИ."""
    if body.city_id and not await db.get(LgCity, body.city_id):
        raise HTTPException(404, "Город не найден")
    cands = (await db.execute(select(LgCandidate).where(LgCandidate.id.in_(body.candidate_ids)))).scalars().all()
    made = 0
    for c in cands:
        t = await db.get(LgSearchTask, c.task_id)
        if await make_donor(db, c, body.city_id, t):
            made += 1
        c.state = "distributed"
        c.city_id = body.city_id
    await db.commit()
    return {"assigned": made, "considered": len(cands)}


@router.post("/candidates/reject")
async def reject(body: Assign, db: AsyncSession = Depends(get_db)):
    cands = (await db.execute(select(LgCandidate).where(LgCandidate.id.in_(body.candidate_ids)))).scalars().all()
    for c in cands:
        c.state = "rejected"
        c.reject_reason = "manual"
        exists = (await db.execute(select(LgReject).where(LgReject.username == c.username))).scalar_one_or_none()
        if not exists:
            db.add(LgReject(username=c.username, reason="manual", search_task_id=c.task_id))
    await db.commit()
    return {"rejected": len(cands)}
