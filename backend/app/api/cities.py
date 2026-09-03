"""Города — они же проекты. Список со счётчиками, создание, настройки города."""
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import LgCity, LgDonor, LgLead, LgPost
from .deps import require_token

router = APIRouter(prefix="/api/cities", tags=["cities"], dependencies=[Depends(require_token)])


class CityCreate(BaseModel):
    name: str


class CityPatch(BaseModel):
    name: str | None = None
    is_active: bool | None = None
    collect_posts: bool | None = None
    collect_comments: bool | None = None
    cost_per_contact: Decimal | None = None
    cost_per_handling: Decimal | None = None
    comment_fresh_days: int | None = None
    post_freeze_days: int | None = None
    donor_pause_days: int | None = None
    resend_after_days: int | None = None
    probe_mode: str | None = None
    probe_enabled: bool | None = None
    probe_hook_token: str | None = None
    crm_webhook_url: str | None = None
    crm_secret: str | None = None
    send_mode: str | None = None


def _dto(c: LgCity, extra: dict | None = None) -> dict:
    d = {
        "id": c.id, "name": c.name, "is_active": c.is_active,
        "collect_posts": c.collect_posts, "collect_comments": c.collect_comments,
        "cost_per_contact": float(c.cost_per_contact or 0),
        "cost_per_handling": float(c.cost_per_handling or 0),
        "comment_fresh_days": c.comment_fresh_days, "post_freeze_days": c.post_freeze_days,
        "donor_pause_days": c.donor_pause_days, "resend_after_days": c.resend_after_days,
        "probe_mode": c.probe_mode, "probe_enabled": c.probe_enabled,
        "probe_hook_token_set": bool(c.probe_hook_token),
        "crm_webhook_url": c.crm_webhook_url or "", "crm_secret_set": bool(c.crm_secret),
        "send_mode": c.send_mode, "created_at": c.created_at,
    }
    if extra:
        d.update(extra)
    return d


async def _counts(db: AsyncSession) -> dict[int, dict]:
    """Счётчики по городам одним проходом: доноры по статусам, посты, лиды."""
    out: dict[int, dict] = {}
    rows = (await db.execute(
        select(LgDonor.city_id, LgDonor.status, func.count()).group_by(LgDonor.city_id, LgDonor.status)
    )).all()
    for city_id, status, n in rows:
        if city_id is None:
            continue
        out.setdefault(city_id, {})[f"donors_{status}"] = n
    rows = (await db.execute(
        select(LgPost.city_id,
               func.count(),
               func.count().filter(LgPost.monitor_status == "active"),
               func.count().filter(LgPost.is_selling.is_(True)))
        .group_by(LgPost.city_id)
    )).all()
    for city_id, total, active, selling in rows:
        if city_id is not None:
            out.setdefault(city_id, {}).update(posts=total, posts_active=active, posts_selling=selling)
    rows = (await db.execute(
        select(LgLead.city_id,
               func.count(),
               func.count().filter(LgLead.probe_status.in_(["pending", "queued"])),
               func.count().filter(LgLead.phone.isnot(None)),
               func.count().filter(LgLead.outbound_status == "sent"))
        .group_by(LgLead.city_id)
    )).all()
    for city_id, total, unprobed, with_phone, sent in rows:
        out.setdefault(city_id, {}).update(leads=total, leads_unprobed=unprobed,
                                           leads_with_phone=with_phone, leads_sent=sent)
    return out


@router.get("")
async def list_cities(db: AsyncSession = Depends(get_db)):
    cities = (await db.execute(select(LgCity).order_by(LgCity.is_active.desc(), LgCity.name))).scalars().all()
    counts = await _counts(db)
    unclassified = (await db.execute(
        select(func.count()).select_from(LgDonor).where(LgDonor.city_id.is_(None)))).scalar() or 0
    return {
        "cities": [_dto(c, {"donors_new": 0, "donors_monitored": 0, "donors_paused": 0,
                            "posts": 0, "posts_active": 0, "posts_selling": 0,
                            "leads": 0, "leads_unprobed": 0, "leads_with_phone": 0, "leads_sent": 0,
                            **counts.get(c.id, {})}) for c in cities],
        "unclassified_donors": unclassified,
    }


@router.post("", status_code=201)
async def create_city(body: CityCreate, db: AsyncSession = Depends(get_db)):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Название пустое")
    exists = (await db.execute(select(LgCity).where(func.lower(LgCity.name) == name.lower()))).scalar_one_or_none()
    if exists:
        raise HTTPException(409, f"Город «{exists.name}» уже есть")
    city = LgCity(name=name, is_active=True)
    db.add(city)
    await db.commit()
    await db.refresh(city)
    return _dto(city)


@router.get("/{city_id}")
async def get_city(city_id: int, db: AsyncSession = Depends(get_db)):
    city = await db.get(LgCity, city_id)
    if not city:
        raise HTTPException(404, "Город не найден")
    data = {k: 0 for k in ("donors_new", "donors_monitored", "donors_paused", "posts", "posts_selling",
                          "posts_active", "leads", "leads_unprobed", "leads_with_phone", "leads_sent")}
    data.update((await _counts(db)).get(city_id, {}))
    return _dto(city, data)


@router.patch("/{city_id}")
async def patch_city(city_id: int, body: CityPatch, db: AsyncSession = Depends(get_db)):
    city = await db.get(LgCity, city_id)
    if not city:
        raise HTTPException(404, "Город не найден")
    data = body.model_dump(exclude_unset=True)
    if "probe_mode" in data and data["probe_mode"] not in ("manual", "auto"):
        raise HTTPException(400, "probe_mode: manual | auto")
    if "send_mode" in data and data["send_mode"] not in ("manual", "auto"):
        raise HTTPException(400, "send_mode: manual | auto")
    for k, v in data.items():
        if isinstance(v, str):
            v = v.strip()
        setattr(city, k, v)
    await db.commit()
    await db.refresh(city)
    return _dto(city)
