"""Задачи поиска доноров — карточки с этапами; кандидаты живут внутри задачи.

Создание кладёт задачу в стадию collecting, дальше её ведёт воркер discovery:
сбор → f1 → ИИ «кто и где» → ready. Здесь — только создание, чтение и кнопки
«Распределить», «В город руками», «Отклонить».
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
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
    kind: str                       # hashtag | keyword | recommendation
    values: list[str] = []          # теги / слова
    seed_donor_ids: list[int] = []  # для recommendation


class Assign(BaseModel):
    candidate_ids: list[int]
    city_id: int | None = None      # None → неразобранный донор


class Adopt(BaseModel):
    tid: str                        # id задания на parser.im
    kind: str | None = None         # hashtag | keyword; пусто — по типу задания (p3 / p5)


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
        .where(LgJob.kind.in_(["search", "apify_recommend"]), LgJob.search_task_id.isnot(None))
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
    if body.kind not in ("hashtag", "keyword", "recommendation"):
        raise HTTPException(400, "kind: hashtag | keyword | recommendation")
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
                 payload={"kind": kind, "values": [], "adopted": True}, lines=1, priority=40, state="running",
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
