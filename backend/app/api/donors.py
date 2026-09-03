"""Доноры — список по всем городам с фильтром и сортировкой, правка статуса и города,
ручное добавление. «Топа» нет: только сортировки."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import case, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import IgAccount, LgCity, LgComment, LgDonor, LgLead, LgPost
from .deps import require_token

router = APIRouter(prefix="/api/donors", tags=["donors"], dependencies=[Depends(require_token)])

SORTS = {
    "leads": "leads_period", "comments": "comments_period", "new_posts": "new_posts_period",
    "posts": "posts", "added": "added_at", "username": "username",
}


class DonorCreate(BaseModel):
    username: str
    city_id: int | None = None


class DonorPatch(BaseModel):
    status: str | None = None          # monitored | paused | new | unclassified
    city_id: int | None = None
    status_reason: str | None = None


@router.get("")
async def list_donors(
    city_id: int | None = None,
    unclassified: bool = False,
    status: str | None = None,
    q: str | None = None,
    days: int = Query(7, ge=1, le=365),
    sort: str = "leads",
    limit: int = Query(100, le=500),
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    since = datetime.now(timezone.utc) - timedelta(days=days)

    posts_sq = (
        select(LgPost.donor_id.label("donor_id"),
               func.count().label("posts"),
               func.count().filter(LgPost.is_selling.is_(True)).label("selling"),
               func.count().filter(LgPost.published_at >= since).label("new_posts_period"),
               func.count().filter(LgPost.monitor_status == "active").label("posts_active"))
        .group_by(LgPost.donor_id).subquery()
    )
    comments_sq = (
        select(LgPost.donor_id.label("donor_id"),
               func.count(LgComment.id).label("comments_period"))
        .join(LgComment, LgComment.post_id == LgPost.id)
        .where(LgComment.written_at >= since, LgComment.is_donor_reply.is_(False))
        .group_by(LgPost.donor_id).subquery()
    )
    leads_sq = (
        select(LgPost.donor_id.label("donor_id"),
               func.count(LgLead.id).label("leads_period"),
               func.count(LgLead.id).filter(LgLead.phone.isnot(None)).label("probed_period"),
               func.count(LgLead.id).filter(LgLead.crm_status == "application").label("applications"),
               func.count(LgLead.id).filter(LgLead.crm_status == "qual").label("quals"),
               func.count(LgLead.id).filter(LgLead.crm_status == "deal").label("deals"),
               func.coalesce(func.sum(LgLead.cost_contact + LgLead.cost_handling), 0).label("spend"))
        .join(LgLead, LgLead.post_id == LgPost.id)
        .where(LgLead.created_at >= since)
        .group_by(LgPost.donor_id).subquery()
    )

    stmt = (
        select(LgDonor, IgAccount, LgCity.name.label("city_name"),
               posts_sq.c.posts, posts_sq.c.selling, posts_sq.c.new_posts_period, posts_sq.c.posts_active,
               comments_sq.c.comments_period,
               leads_sq.c.leads_period, leads_sq.c.probed_period, leads_sq.c.applications,
               leads_sq.c.quals, leads_sq.c.deals, leads_sq.c.spend)
        .join(IgAccount, IgAccount.id == LgDonor.account_id)
        .outerjoin(LgCity, LgCity.id == LgDonor.city_id)
        .outerjoin(posts_sq, posts_sq.c.donor_id == LgDonor.id)
        .outerjoin(comments_sq, comments_sq.c.donor_id == LgDonor.id)
        .outerjoin(leads_sq, leads_sq.c.donor_id == LgDonor.id)
    )
    if unclassified:
        stmt = stmt.where(LgDonor.city_id.is_(None))
    elif city_id:
        stmt = stmt.where(LgDonor.city_id == city_id)
    if status:
        stmt = stmt.where(LgDonor.status == status)
    if q:
        stmt = stmt.where(IgAccount.username.ilike(f"%{q.strip().lstrip('@')}%"))

    col = SORTS.get(sort, "leads_period")
    order = {
        "leads_period": desc(func.coalesce(leads_sq.c.leads_period, 0)),
        "comments_period": desc(func.coalesce(comments_sq.c.comments_period, 0)),
        "new_posts_period": desc(func.coalesce(posts_sq.c.new_posts_period, 0)),
        "posts": desc(func.coalesce(posts_sq.c.posts, 0)),
        "added_at": desc(LgDonor.added_at),
        "username": IgAccount.username,
    }[col]
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    rows = (await db.execute(stmt.order_by(order, LgDonor.id).limit(limit).offset(offset))).all()

    items = []
    for d, a, city_name, posts, selling, new_posts, active, comments, leads, probed, apps, quals, deals, spend in rows:
        leads = leads or 0
        items.append({
            "id": d.id, "account_id": a.id, "username": a.username, "full_name": a.full_name,
            "followers": a.followers, "activity_kind": a.activity_kind,
            "city_id": d.city_id, "city": city_name,
            "status": d.status, "intake_stage": d.intake_stage, "status_reason": d.status_reason,
            "found_via": d.found_via, "added_at": d.added_at,
            "posts": posts or 0, "selling": selling or 0, "posts_active": active or 0,
            "new_posts_period": new_posts or 0, "comments_period": comments or 0,
            "leads_period": leads, "probed_period": probed or 0,
            "applications": apps or 0, "quals": quals or 0, "deals": deals or 0,
            "cost_per_lead": round(float(spend or 0) / leads, 2) if leads else None,
        })
    return {"total": total, "items": items, "days": days}


@router.post("", status_code=201)
async def create_donor(body: DonorCreate, db: AsyncSession = Depends(get_db)):
    username = body.username.strip().lstrip("@").rstrip("/").lower()
    if not username:
        raise HTTPException(400, "Логин пустой")
    if body.city_id and not await db.get(LgCity, body.city_id):
        raise HTTPException(404, "Город не найден")
    acc = (await db.execute(select(IgAccount).where(IgAccount.username == username))).scalar_one_or_none()
    if acc is None:
        acc = IgAccount(username=username, roles="donor", city_id=body.city_id,
                        city_source="manual" if body.city_id else None)
        db.add(acc)
        await db.flush()
    q = select(LgDonor).where(LgDonor.account_id == acc.id)
    q = q.where(LgDonor.city_id == body.city_id) if body.city_id else q.where(LgDonor.city_id.is_(None))
    if (await db.execute(q)).scalar_one_or_none():
        raise HTTPException(409, "Такой донор уже есть")
    donor = LgDonor(account_id=acc.id, city_id=body.city_id,
                    status="new" if body.city_id else "unclassified",
                    intake_stage="posts", found_via="manual", status_reason="добавлен вручную")
    db.add(donor)
    await db.commit()
    return {"id": donor.id, "username": username, "status": donor.status}


@router.patch("/{donor_id}")
async def patch_donor(donor_id: int, body: DonorPatch, db: AsyncSession = Depends(get_db)):
    d = await db.get(LgDonor, donor_id)
    if not d:
        raise HTTPException(404, "Донор не найден")
    if body.status is not None:
        if body.status not in ("new", "monitored", "paused", "unclassified"):
            raise HTTPException(400, "status: new | monitored | paused | unclassified")
        d.status = body.status
        d.status_changed_at = datetime.now(timezone.utc)
        d.status_reason = body.status_reason or "изменено вручную"
    if "city_id" in body.model_fields_set:
        if body.city_id and not await db.get(LgCity, body.city_id):
            raise HTTPException(404, "Город не найден")
        d.city_id = body.city_id
        if body.city_id and d.status == "unclassified":
            d.status = "new"
            d.status_changed_at = datetime.now(timezone.utc)
            d.status_reason = "город назначен вручную"
        acc = await db.get(IgAccount, d.account_id)
        if acc and body.city_id:
            acc.city_id = body.city_id
            acc.city_source = "manual"
    await db.commit()
    return {"id": d.id, "status": d.status, "city_id": d.city_id}
