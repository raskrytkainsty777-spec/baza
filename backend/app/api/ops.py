"""Рычаги оператора: выключатели сбора и ИИ, «запустить сейчас», пробив по отсекам,
перезапуск заведения донора, здоровье воркеров."""
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import LgCity, LgDonor, LgJob, LgLead, LgSetting
from ..workers.common import set_value, settings_all
from .deps import require_token

router = APIRouter(prefix="/api/ops", tags=["ops"], dependencies=[Depends(require_token)])

RUNNABLE = ("new_posts", "counters")
WORKERS = ("job_runner", "discovery", "donor_intake", "comments_collect", "posts_sync",
           "ai_posts", "ai_comments", "probe_feeder", "outbox", "inbox")


class Switches(BaseModel):
    collection_enabled: bool | None = None
    ai_enabled: bool | None = None


class ProbeRequest(BaseModel):
    date_from: date | None = None     # по дате комментария
    date_to: date | None = None
    limit: int | None = None
    lead_ids: list[int] | None = None


@router.get("/status")
async def status(db: AsyncSession = Depends(get_db)):
    values = await settings_all(db)
    now = datetime.now(timezone.utc)
    hb = {}
    for w in WORKERS:
        raw = values.get(f"heartbeat.{w}")
        alive = False
        if raw:
            try:
                alive = now - datetime.fromisoformat(raw) < timedelta(minutes=5)
            except ValueError:
                alive = False
        hb[w] = {"last": raw, "alive": alive}
    lines_busy = (await db.execute(select(func.coalesce(func.sum(LgJob.lines), 0)).where(
        LgJob.provider == "parserim", LgJob.state == "running"))).scalar() or 0
    queued = (await db.execute(select(func.count()).where(LgJob.provider == "parserim", LgJob.state == "queued"))).scalar() or 0
    return {
        "collection_enabled": values.get("collection_enabled") == "1",
        "ai_enabled": values.get("ai_enabled", "1") == "1",
        "parserim": {"lines_busy": int(lines_busy), "lines_total": int(values.get("parserim_lines") or 10), "queued": queued},
        "ai_cost_today_usd": float(values.get(f"ai_cost.{date.today().isoformat()}") or 0),
        "last_run": {k: values.get(f"last_run.{k}") for k in RUNNABLE},
        "workers": hb,
    }


@router.put("/switches")
async def switches(body: Switches, db: AsyncSession = Depends(get_db)):
    if body.collection_enabled is not None:
        await set_value(db, "collection_enabled", "1" if body.collection_enabled else "0")
    if body.ai_enabled is not None:
        await set_value(db, "ai_enabled", "1" if body.ai_enabled else "0")
    await db.commit()
    return await status(db)


@router.post("/run/{name}")
async def run_now(name: str, db: AsyncSession = Depends(get_db)):
    if name not in RUNNABLE:
        raise HTTPException(400, f"name: {' | '.join(RUNNABLE)}")
    await set_value(db, f"run_now.{name}", "1")
    await db.commit()
    return {"ok": True, "name": name}


@router.post("/donors/{donor_id}/restart-intake")
async def restart_intake(donor_id: int, db: AsyncSession = Depends(get_db)):
    d = await db.get(LgDonor, donor_id)
    if not d:
        raise HTTPException(404, "Донор не найден")
    d.intake_stage = "posts"
    if d.status == "paused":
        d.status = "new" if d.city_id else "unclassified"
    d.status_reason = "заведение перезапущено вручную"
    await db.commit()
    return {"id": d.id, "intake_stage": d.intake_stage, "status": d.status}


@router.get("/cities/{city_id}/probe-summary")
async def probe_summary(city_id: int, db: AsyncSession = Depends(get_db)):
    """Отсеки города: непробитые по дням комментария и итоги пробитых."""
    from ..models import LgComment
    if not await db.get(LgCity, city_id):
        raise HTTPException(404, "Город не найден")
    by_status = dict((await db.execute(
        select(LgLead.probe_status, func.count()).where(LgLead.city_id == city_id).group_by(LgLead.probe_status))).all())
    day = func.date(LgComment.written_at)
    days = (await db.execute(
        select(day, func.count()).join(LgComment, LgComment.id == LgLead.comment_id)
        .where(LgLead.city_id == city_id, LgLead.phone.is_(None), LgLead.probe_status == "pending")
        .group_by(day).order_by(day.desc()).limit(60))).all()
    with_phone = (await db.execute(select(func.count()).where(LgLead.city_id == city_id, LgLead.phone.isnot(None)))).scalar() or 0
    return {"by_status": {k or "": v for k, v in by_status.items()}, "with_phone": with_phone,
            "unprobed_by_day": [{"day": d.isoformat() if d else None, "count": n} for d, n in days]}


@router.post("/cities/{city_id}/probe")
async def probe(city_id: int, body: ProbeRequest, db: AsyncSession = Depends(get_db)):
    """Отдать на пробив: по списку лидов или по датам комментария. Ставит probe_status=manual,
    фидер отправит при следующем проходе (город должен быть с включённым пробивом)."""
    from ..models import LgComment
    city = await db.get(LgCity, city_id)
    if not city:
        raise HTTPException(404, "Город не найден")
    stmt = select(LgLead).join(LgComment, LgComment.id == LgLead.comment_id).where(
        LgLead.city_id == city_id, LgLead.phone.is_(None), LgLead.probe_status.in_(["pending", "error"]))
    if body.lead_ids:
        stmt = stmt.where(LgLead.id.in_(body.lead_ids))
    if body.date_from:
        stmt = stmt.where(LgComment.written_at >= datetime.combine(body.date_from, datetime.min.time(), tzinfo=timezone.utc))
    if body.date_to:
        stmt = stmt.where(LgComment.written_at < datetime.combine(body.date_to + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc))
    stmt = stmt.order_by(LgLead.id)
    if body.limit:
        stmt = stmt.limit(body.limit)
    leads = (await db.execute(stmt)).scalars().all()
    for lead in leads:
        lead.probe_status = "manual"
    await db.commit()
    return {"queued": len(leads), "probe_enabled": city.probe_enabled, "has_token": bool(city.probe_hook_token)}
