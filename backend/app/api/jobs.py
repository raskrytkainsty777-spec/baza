"""Задания parser.im / Apify и журнал событий — экраны контроля, кнопок почти нет."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import LgEvent, LgJob
from .deps import require_token

router = APIRouter(prefix="/api", tags=["jobs"], dependencies=[Depends(require_token)])


@router.get("/jobs")
async def list_jobs(
    provider: str | None = None,
    kind: str | None = None,
    state: str = Query("active", pattern="^(active|queued|done|error|all)$"),
    limit: int = Query(100, le=500),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(LgJob)
    if provider:
        stmt = stmt.where(LgJob.provider == provider)
    if kind:
        stmt = stmt.where(LgJob.kind == kind)
    if state == "active":
        stmt = stmt.where(LgJob.state.in_(["queued", "running"]))
    elif state != "all":
        stmt = stmt.where(LgJob.state == state)
    rows = (await db.execute(stmt.order_by(desc(LgJob.created_at)).limit(limit))).scalars().all()

    lines_busy = (await db.execute(
        select(func.coalesce(func.sum(LgJob.lines), 0))
        .where(LgJob.provider == "parserim", LgJob.state == "running"))).scalar() or 0
    lines_queued = (await db.execute(
        select(func.count()).where(LgJob.provider == "parserim", LgJob.state == "queued"))).scalar() or 0
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    apify_spent = (await db.execute(
        select(func.coalesce(func.sum(LgJob.cost_usd), 0))
        .where(LgJob.provider == "apify", LgJob.created_at >= today))).scalar() or 0

    return {
        "parserim": {"lines_busy": int(lines_busy), "queued": lines_queued},
        "apify": {"spent_today_usd": float(apify_spent)},
        "items": [{
            "id": j.id, "provider": j.provider, "external_id": j.external_id, "kind": j.kind,
            "purpose": j.purpose, "city_id": j.city_id, "donor_id": j.donor_id,
            "search_task_id": j.search_task_id, "lines": j.lines, "priority": j.priority,
            "state": j.state, "count": j.count, "rows_imported": j.rows_imported, "error": j.error,
            "cost_usd": float(j.cost_usd) if j.cost_usd is not None else None,
            "created_at": j.created_at, "started_at": j.started_at, "finished_at": j.finished_at,
        } for j in rows],
    }


@router.post("/jobs/{job_id}/finish")
async def finish_job(job_id: int, db: AsyncSession = Depends(get_db)):
    """Освободить строки вручную: пометить завершённым, воркер добьёт на стороне провайдера."""
    j = await db.get(LgJob, job_id)
    if not j:
        raise HTTPException(404, "Задание не найдено")
    if j.state not in ("queued", "running"):
        raise HTTPException(400, "Задание уже не активно")
    j.state = "finished"
    j.finished_at = datetime.now(timezone.utc)
    db.add(LgEvent(kind="job.finished_manually", entity="job", entity_id=j.id, level="warn",
                   message=f"Задание {j.provider} {j.external_id or j.id} завершено вручную"))
    await db.commit()
    return {"id": j.id, "state": j.state}


@router.get("/events")
async def list_events(
    level: str | None = None,
    kind: str | None = None,
    entity: str | None = None,
    limit: int = Query(200, le=1000),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(LgEvent)
    if level:
        stmt = stmt.where(LgEvent.level == level)
    if kind:
        stmt = stmt.where(LgEvent.kind.like(f"{kind}%"))
    if entity:
        stmt = stmt.where(LgEvent.entity == entity)
    rows = (await db.execute(stmt.order_by(desc(LgEvent.at)).limit(limit))).scalars().all()
    return {"items": [{
        "id": e.id, "at": e.at, "level": e.level, "kind": e.kind, "entity": e.entity,
        "entity_id": e.entity_id, "message": e.message, "payload": e.payload,
    } for e in rows]}
