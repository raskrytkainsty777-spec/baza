"""Мастер задач: сводка по городам и динамика по дням за период.

Пока данных немного — считаем прямо по таблицам. Когда витрина lg_stats_daily
начнёт заполняться ночным пересчётом, этот же эндпоинт переключится на неё.
"""
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import cast, Date, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import LgCity, LgComment, LgDonor, LgLead, LgPost
from .deps import require_token

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"], dependencies=[Depends(require_token)])

PERIODS = {"today": 1, "7d": 7, "30d": 30, "90d": 90}


def _since(period: str, date_from: date | None) -> datetime:
    if date_from:
        return datetime.combine(date_from, datetime.min.time(), tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - timedelta(days=PERIODS.get(period, 7))


def _until(date_to: date | None) -> datetime:
    if date_to:
        return datetime.combine(date_to + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    return datetime.now(timezone.utc) + timedelta(days=1)


def _costs(leads: int, probed: int, sent: int, apps: int, quals: int, deals: int, spend: float) -> dict:
    def per(n):
        return round(spend / n, 2) if n else None
    return {"spend": round(spend, 2), "cpl": per(apps), "cpq": per(quals), "cpo": per(deals)}


@router.get("")
async def dashboard(
    period: str = Query("7d", pattern="^(today|7d|30d|90d|custom)$"),
    date_from: date | None = None,
    date_to: date | None = None,
    db: AsyncSession = Depends(get_db),
):
    since, until = _since(period, date_from), _until(date_to)
    cities = (await db.execute(select(LgCity).order_by(LgCity.is_active.desc(), LgCity.name))).scalars().all()

    donors = {}
    for city_id, status, n in (await db.execute(
        select(LgDonor.city_id, LgDonor.status, func.count()).group_by(LgDonor.city_id, LgDonor.status)
    )).all():
        donors.setdefault(city_id, {})[status] = n

    posts = {}
    for city_id, total, new, growth in (await db.execute(
        select(LgPost.city_id, func.count(),
               func.count().filter(LgPost.published_at >= since, LgPost.published_at < until),
               func.count().filter(LgPost.comments_delta > 0))
        .group_by(LgPost.city_id)
    )).all():
        posts[city_id] = (total, new, growth)

    comments = dict((await db.execute(
        select(LgComment.city_id, func.count())
        .where(LgComment.written_at >= since, LgComment.written_at < until, LgComment.is_donor_reply.is_(False))
        .group_by(LgComment.city_id)
    )).all())

    leads = {}
    for row in (await db.execute(
        select(LgLead.city_id, func.count(),
               func.count().filter(LgLead.probe_status.in_(["pending", "queued"])),
               func.count().filter(LgLead.phone.isnot(None)),
               func.count().filter(LgLead.outbound_status == "sent"),
               func.count().filter(LgLead.crm_status == "application"),
               func.count().filter(LgLead.crm_status == "qual"),
               func.count().filter(LgLead.crm_status == "deal"),
               func.coalesce(func.sum(LgLead.cost_contact + LgLead.cost_handling), 0))
        .where(LgLead.created_at >= since, LgLead.created_at < until)
        .group_by(LgLead.city_id)
    )).all():
        leads[row[0]] = row[1:]

    rows = []
    for c in cities:
        d = donors.get(c.id, {})
        p = posts.get(c.id, (0, 0, 0))
        l = leads.get(c.id, (0, 0, 0, 0, 0, 0, 0, 0))
        rows.append({
            "city_id": c.id, "city": c.name, "is_active": c.is_active,
            "donors_new": d.get("new", 0), "donors_monitored": d.get("monitored", 0),
            "donors_paused": d.get("paused", 0),
            "posts_total": p[0], "new_posts": p[1], "growth_posts": p[2],
            "comments": comments.get(c.id, 0),
            "leads": l[0], "unprobed": l[1], "probed": l[2], "sent": l[3],
            "applications": l[4], "quals": l[5], "deals": l[6],
            **_costs(l[0], l[2], l[3], l[4], l[5], l[6], float(l[7] or 0)),
        })
    unclassified = (await db.execute(
        select(func.count()).select_from(LgDonor).where(LgDonor.city_id.is_(None)))).scalar() or 0
    return {"period": period, "since": since, "until": until, "cities": rows,
            "unclassified_donors": unclassified}


@router.get("/daily")
async def daily(
    city_id: int | None = None,
    days: int = Query(14, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
):
    """Динамика по дням: новые посты, приросты, комменты, лиды, пробито, в CRM."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    out: dict[date, dict] = {}

    def bump(day, key, n):
        if day is None:
            return
        out.setdefault(day, {"day": day, "new_posts": 0, "comments": 0, "leads": 0, "probed": 0, "sent": 0})[key] += n

    pq = select(cast(LgPost.published_at, Date), func.count()).where(LgPost.published_at >= since)
    cq = select(cast(LgComment.written_at, Date), func.count()).where(
        LgComment.written_at >= since, LgComment.is_donor_reply.is_(False))
    lq = select(cast(LgLead.created_at, Date), func.count(),
                func.count().filter(LgLead.phone.isnot(None)),
                func.count().filter(LgLead.outbound_status == "sent")).where(LgLead.created_at >= since)
    if city_id:
        pq, cq, lq = (pq.where(LgPost.city_id == city_id), cq.where(LgComment.city_id == city_id),
                      lq.where(LgLead.city_id == city_id))
    for day, n in (await db.execute(pq.group_by(cast(LgPost.published_at, Date)))).all():
        bump(day, "new_posts", n)
    for day, n in (await db.execute(cq.group_by(cast(LgComment.written_at, Date)))).all():
        bump(day, "comments", n)
    for day, n, probed, sent in (await db.execute(lq.group_by(cast(LgLead.created_at, Date)))).all():
        bump(day, "leads", n); bump(day, "probed", probed); bump(day, "sent", sent)
    return {"days": sorted(out.values(), key=lambda r: r["day"], reverse=True)}


@router.get("/hooks")
async def by_hook(
    city_id: int | None = None,
    days: int = Query(7, ge=1, le=365),
    by: str = Query("hook", pattern="^(hook|category)$"),
    db: AsyncSession = Depends(get_db),
):
    """Срез по крючкам или категориям: постов, комментов, лидов, заявок, CPL."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    col = LgPost.hook if by == "hook" else LgPost.category
    comments_sq = (select(LgComment.post_id, func.count().label("n"))
                   .where(LgComment.written_at >= since, LgComment.is_donor_reply.is_(False))
                   .group_by(LgComment.post_id)).subquery()
    leads_sq = (select(LgLead.post_id, func.count().label("n"),
                       func.count().filter(LgLead.crm_status == "application").label("apps"),
                       func.coalesce(func.sum(LgLead.cost_contact + LgLead.cost_handling), 0).label("spend"))
                .where(LgLead.created_at >= since).group_by(LgLead.post_id)).subquery()
    stmt = (select(func.coalesce(col, "без разметки"), func.count(LgPost.id),
                   func.coalesce(func.sum(comments_sq.c.n), 0),
                   func.coalesce(func.sum(leads_sq.c.n), 0),
                   func.coalesce(func.sum(leads_sq.c.apps), 0),
                   func.coalesce(func.sum(leads_sq.c.spend), 0))
            .outerjoin(comments_sq, comments_sq.c.post_id == LgPost.id)
            .outerjoin(leads_sq, leads_sq.c.post_id == LgPost.id)
            .group_by(col).order_by(func.sum(leads_sq.c.n).desc().nullslast(), func.count(LgPost.id).desc()))
    if city_id:
        stmt = stmt.where(LgPost.city_id == city_id)
    return {"by": by, "rows": [{
        "value": v, "posts": posts, "comments": int(c), "leads": int(l), "applications": int(a),
        "cpl": round(float(s) / int(a), 2) if a else None,
    } for v, posts, c, l, a, s in (await db.execute(stmt)).all()]}
