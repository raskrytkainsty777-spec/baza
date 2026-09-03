"""Посты — список по всем городам: новые за день, прирост, разметка ИИ, монитор."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import IgAccount, LgCity, LgComment, LgDonor, LgLead, LgPost
from .deps import require_token

router = APIRouter(prefix="/api/posts", tags=["posts"], dependencies=[Depends(require_token)])


class PostPatch(BaseModel):
    monitor_status: str | None = None   # active | frozen | excluded | forced
    city_id: int | None = None


@router.get("/summary")
async def summary(city_id: int | None = None, db: AsyncSession = Depends(get_db)):
    """Цифры над таблицей: новых постов сегодня, продающих, с приростом, комментов за день."""
    now = datetime.now(timezone.utc)
    day = now - timedelta(days=1)
    base = select(LgPost)
    if city_id:
        base = base.where(LgPost.city_id == city_id)
    sq = base.subquery()
    row = (await db.execute(select(
        func.count().filter(sq.c.published_at >= day),
        func.count().filter(sq.c.published_at >= day, sq.c.is_selling.is_(True)),
        func.count().filter(sq.c.comments_delta > 0),
        func.count().filter(sq.c.monitor_status == "active"),
        func.count().filter(sq.c.monitor_status == "frozen"),
        func.count(),
    ))).one()
    cq = select(func.count(LgComment.id)).where(LgComment.written_at >= day, LgComment.is_donor_reply.is_(False))
    lq = select(func.count(LgLead.id)).where(LgLead.created_at >= day)
    if city_id:
        cq = cq.where(LgComment.city_id == city_id)
        lq = lq.where(LgLead.city_id == city_id)
    return {
        "new_today": row[0], "new_selling_today": row[1], "growth_today": row[2],
        "active": row[3], "frozen": row[4], "total": row[5],
        "comments_today": (await db.execute(cq)).scalar() or 0,
        "leads_today": (await db.execute(lq)).scalar() or 0,
    }


@router.get("")
async def list_posts(
    city_id: int | None = None,
    donor_id: int | None = None,
    show: str = Query("all", pattern="^(all|new|growth|active|frozen|non_selling|selling)$"),
    hook: str | None = None,
    category: str | None = None,
    q: str | None = None,
    days: int = Query(1, ge=1, le=365),
    sort: str = Query("published", pattern="^(published|comments|delta)$"),
    limit: int = Query(100, le=500),
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    leads_sq = (select(LgLead.post_id, func.count().label("leads")).group_by(LgLead.post_id)).subquery()
    comments_sq = (select(LgComment.post_id, func.count().label("collected"))
                   .where(LgComment.is_donor_reply.is_(False)).group_by(LgComment.post_id)).subquery()

    stmt = (
        select(LgPost, IgAccount.username, LgCity.name.label("city_name"),
               func.coalesce(leads_sq.c.leads, 0), func.coalesce(comments_sq.c.collected, 0))
        .join(IgAccount, IgAccount.id == LgPost.account_id)
        .outerjoin(LgCity, LgCity.id == LgPost.city_id)
        .outerjoin(leads_sq, leads_sq.c.post_id == LgPost.id)
        .outerjoin(comments_sq, comments_sq.c.post_id == LgPost.id)
    )
    if city_id:
        stmt = stmt.where(LgPost.city_id == city_id)
    if donor_id:
        stmt = stmt.where(LgPost.donor_id == donor_id)
    if show == "new":
        stmt = stmt.where(LgPost.published_at >= since)
    elif show == "growth":
        stmt = stmt.where(LgPost.comments_delta > 0)
    elif show == "active":
        stmt = stmt.where(LgPost.monitor_status.in_(["active", "forced"]))
    elif show == "frozen":
        stmt = stmt.where(LgPost.monitor_status == "frozen")
    elif show == "non_selling":
        stmt = stmt.where(LgPost.is_selling.is_(False))
    elif show == "selling":
        stmt = stmt.where(LgPost.is_selling.is_(True))
    if hook:
        stmt = stmt.where(LgPost.hook == hook)
    if category:
        stmt = stmt.where(LgPost.category == category)
    if q:
        stmt = stmt.where(LgPost.caption.ilike(f"%{q.strip()}%"))

    order = {"published": desc(LgPost.published_at), "comments": desc(LgPost.comments_count),
             "delta": desc(LgPost.comments_delta)}[sort]
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    rows = (await db.execute(stmt.order_by(order, LgPost.id.desc()).limit(limit).offset(offset))).all()
    return {"total": total, "items": [{
        "id": p.id, "shortcode": p.shortcode, "url": p.url, "donor_id": p.donor_id, "username": username,
        "city_id": p.city_id, "city": city_name, "city_source": p.city_source,
        "published_at": p.published_at, "product_type": p.product_type,
        "caption": (p.caption or "")[:300], "views": p.views, "likes": p.likes,
        "comments_count": p.comments_count, "comments_prev": p.comments_count_prev,
        "delta": p.comments_delta, "last_growth_at": p.last_growth_at, "zero_growth_days": p.zero_growth_days,
        "is_selling": p.is_selling, "offer": p.offer, "hook": p.hook, "category": p.category,
        "cta_type": p.cta_type, "code_word": p.code_word, "ai_at": p.ai_at,
        "monitor_status": p.monitor_status, "collected_comments": collected, "leads": leads,
    } for p, username, city_name, leads, collected in rows]}


@router.get("/facets")
async def facets(city_id: int | None = None, db: AsyncSession = Depends(get_db)):
    """Значения крючков и категорий для фильтров."""
    out = {}
    for name, col in (("hooks", LgPost.hook), ("categories", LgPost.category)):
        stmt = select(col, func.count()).where(col.isnot(None)).group_by(col).order_by(desc(func.count()))
        if city_id:
            stmt = stmt.where(LgPost.city_id == city_id)
        out[name] = [{"value": v, "count": n} for v, n in (await db.execute(stmt)).all()]
    return out


@router.patch("/{post_id}")
async def patch_post(post_id: int, body: PostPatch, db: AsyncSession = Depends(get_db)):
    p = await db.get(LgPost, post_id)
    if not p:
        raise HTTPException(404, "Пост не найден")
    if body.monitor_status is not None:
        if body.monitor_status not in ("active", "frozen", "excluded", "forced"):
            raise HTTPException(400, "monitor_status: active | frozen | excluded | forced")
        p.monitor_status = body.monitor_status
        if body.monitor_status in ("active", "forced"):
            p.zero_growth_days = 0
    if "city_id" in body.model_fields_set:
        if body.city_id and not await db.get(LgCity, body.city_id):
            raise HTTPException(404, "Город не найден")
        p.city_id = body.city_id
        p.city_source = "manual"
    await db.commit()
    return {"id": p.id, "monitor_status": p.monitor_status, "city_id": p.city_id}
