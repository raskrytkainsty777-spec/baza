"""Пробитая база: все лиды с номером, дата пробива, фильтры и выгрузка.

Отдельная вкладка вместо кнопки «скачать» в карточке города: нужен список с датой
пробива и возможность забрать, например, только сегодняшних (запрос заказчика 16.09.2026).
"""
import csv
import io
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import IgAccount, LgCity, LgComment, LgDonor, LgLead, LgPost
from ..services.outbound import MSK_TZ
from .deps import require_token

router = APIRouter(prefix="/api/leads", tags=["leads"], dependencies=[Depends(require_token)])

SORTS = {"probed_at": LgLead.probed_at, "created_at": LgLead.created_at, "phone": LgLead.phone}


def _stmt(city_id: int | None, date_from: str | None, date_to: str | None,
          q: str | None, source: str | None, crm_status: str | None):
    donor = IgAccount.__table__.alias("donor_acc")
    st = (select(LgLead, LgComment, LgPost, IgAccount.username, LgCity.name, donor.c.username)
          .join(LgComment, LgComment.id == LgLead.comment_id)
          .join(LgPost, LgPost.id == LgLead.post_id)
          .join(IgAccount, IgAccount.id == LgLead.account_id)
          .outerjoin(LgCity, LgCity.id == LgLead.city_id)
          .outerjoin(LgDonor, LgDonor.id == LgPost.donor_id)
          .outerjoin(donor, donor.c.id == LgDonor.account_id)
          .where(LgLead.phone.isnot(None)))
    if city_id:
        st = st.where(LgLead.city_id == city_id)
    if date_from:
        st = st.where(LgLead.probed_at >= datetime.fromisoformat(date_from).replace(tzinfo=MSK_TZ))
    if date_to:
        st = st.where(LgLead.probed_at < datetime.fromisoformat(date_to).replace(tzinfo=MSK_TZ) + timedelta(days=1))
    if q:
        s = f"%{q.strip().lstrip('@')}%"
        st = st.where(or_(LgLead.phone.ilike(s), IgAccount.username.ilike(s), LgComment.text.ilike(s)))
    if source in ("probe", "base"):
        st = st.where(LgLead.phone_from == source)
    if crm_status:
        st = st.where(LgLead.crm_status == crm_status) if crm_status != "none" else st.where(LgLead.crm_status.is_(None))
    return st


def _row(lead, c, p, username, city, donor_name) -> dict:
    return {
        "id": lead.id, "phone": lead.phone, "phone_from": lead.phone_from,
        "probed_at": lead.probed_at, "username": username, "city": city, "donor": donor_name,
        "comment": (c.text or "")[:300], "comment_at": c.written_at,
        "offer_text": p.offer_text, "offer": p.offer, "category": p.category,
        "post_url": p.url, "post_published_at": p.published_at,
        "crm_status": lead.crm_status, "outbound_status": lead.outbound_status,
    }


@router.get("/probed")
async def probed(city_id: int | None = None, date_from: str | None = None, date_to: str | None = None,
                 q: str | None = None, source: str | None = None, crm_status: str | None = None,
                 sort: str = "probed_at", order: str = "desc",
                 limit: int = Query(100, le=1000), offset: int = 0,
                 db: AsyncSession = Depends(get_db)):
    """Пробитая база: лиды с номером. Даты — по дате пробива, в московском времени."""
    st = _stmt(city_id, date_from, date_to, q, source, crm_status)
    total = (await db.execute(select(func.count()).select_from(st.subquery()))).scalar() or 0
    # считаем по той же выборке: distinct по внешней таблице поверх подзапроса дал бы всю базу
    uniq_sub = st.with_only_columns(LgLead.phone).subquery()
    uniq = (await db.execute(select(func.count(func.distinct(uniq_sub.c.phone))))).scalar() or 0
    col = SORTS.get(sort, LgLead.probed_at)
    st = st.order_by(desc(col).nullslast() if order == "desc" else col.asc().nullsfirst(), desc(LgLead.id))
    rows = (await db.execute(st.limit(limit).offset(offset))).all()
    today = datetime.now(MSK_TZ).date()
    today_cnt = (await db.execute(select(func.count()).select_from(
        _stmt(city_id, today.isoformat(), today.isoformat(), None, None, None).subquery()))).scalar() or 0
    return {"total": total, "unique_phones": uniq, "today": today_cnt,
            "items": [_row(*r) for r in rows]}


@router.get("/probed.csv")
async def probed_csv(city_id: int | None = None, date_from: str | None = None, date_to: str | None = None,
                     q: str | None = None, source: str | None = None, crm_status: str | None = None,
                     db: AsyncSession = Depends(get_db)):
    st = _stmt(city_id, date_from, date_to, q, source, crm_status).order_by(desc(LgLead.probed_at).nullslast())
    rows = (await db.execute(st.limit(100000))).all()
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["id", "телефон", "откуда номер", "дата пробива", "логин", "город", "донор",
                "комментарий", "дата комментария", "на что привлёкся", "предложение", "категория",
                "ссылка на пост", "дата поста", "статус CRM", "отправка"])
    def dt(v, fmt="%d.%m.%Y %H:%M"):
        return v.astimezone(MSK_TZ).strftime(fmt) if v else ""
    for lead, c, p, username, city, donor_name in rows:
        w.writerow([lead.id, lead.phone, "из базы" if lead.phone_from == "base" else "пробив",
                    dt(lead.probed_at), username, city or "", donor_name or "",
                    (c.text or "").replace("\n", " "), dt(c.written_at),
                    p.offer_text or "", p.offer or "", p.category or "",
                    p.url or "", dt(p.published_at, "%d.%m.%Y"),
                    lead.crm_status or "", lead.outbound_status or ""])
    data = ("\ufeff" + buf.getvalue()).encode("utf-8")
    name = "probed" + (f"_{date_from}" if date_from else "") + (f"_{date_to}" if date_to else "")
    return StreamingResponse(iter([data]), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename={name}.csv"})


@router.get("/probed/by-day")
async def probed_by_day(city_id: int | None = None, days: int = Query(30, le=365),
                        db: AsyncSession = Depends(get_db)):
    """Сколько пробили по дням — для быстрых кнопок «сегодня», «вчера»."""
    day = func.date(func.timezone("Europe/Moscow", LgLead.probed_at))
    st = (select(day, func.count(), func.count(func.distinct(LgLead.phone)))
          .where(LgLead.phone.isnot(None), LgLead.probed_at.isnot(None),
                 LgLead.probed_at >= datetime.now(MSK_TZ) - timedelta(days=days)))
    if city_id:
        st = st.where(LgLead.city_id == city_id)
    rows = (await db.execute(st.group_by(day).order_by(desc(day)))).all()
    return {"items": [{"day": d.isoformat() if d else None, "leads": n, "phones": u} for d, n, u in rows]}
