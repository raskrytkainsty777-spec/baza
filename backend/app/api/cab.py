"""Кабинет клиента: источники, компании, база контактов, чёрный список, статусы по вебхуку,
настройки. Вход — логин и пароль клиента (cab_auth), к админскому API доступа нет.

Изменения источников (лимиты, поставщики, регионы, включение) пишутся у нас и помечаются
lf_dirty — воркер cab_sync доносит их до Leads Factory в фоне.
"""
import csv
import io
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import case, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import CabBlacklist, CabClient, CabCompany, CabContact, CabInbox, CabSource
from ..services.leadsfactory.client import LFError, MSK, PHONE_SUPPLIERS, SUPPLIERS, lf_for
from ..workers.common import utcnow
from .cab_auth import login_client, require_client

router = APIRouter(prefix="/api/cab", tags=["cab"])

_GEO_CACHE: dict = {"at": None, "items": []}


class LoginIn(BaseModel):
    login: str
    password: str


class SettingsIn(BaseModel):
    contact_cost: Decimal | None = None
    handling_cost: Decimal | None = None
    suppliers_default: list[str] | None = None
    limit_default: int | None = None
    weekdays: list[bool] | None = None


class SourcesIn(BaseModel):
    text: str
    delimiter: str = ";"
    suppliers: list[str] = []
    limit: int = 5
    geo_ids: list[int] = []


class BulkIn(BaseModel):
    ids: list[int]
    action: str            # limit | enable | disable | suppliers | geo_add | geo_remove
    value: int | list | None = None


class BlacklistIn(BaseModel):
    text: str


def norm_phone(raw: str) -> str | None:
    d = re.sub(r"\D", "", raw or "")
    if len(d) == 11 and d[0] == "8":
        d = "7" + d[1:]
    if len(d) == 10:
        d = "7" + d
    return d if len(d) == 11 and d[0] == "7" else None


def _client_dto(c: CabClient) -> dict:
    cost = float(c.lf_answer_cost) if c.lf_answer_cost is not None else None
    return {
        "id": c.id, "login": c.login, "name": c.name,
        "balance_contacts": c.balance_contacts, "balance_rub": float(c.lf_balance_rub) if c.lf_balance_rub is not None else None,
        "answer_cost": cost, "balance_synced_at": c.balance_synced_at, "contacts_synced_at": c.contacts_synced_at,
        "lf_status": c.lf_status, "lf_error": c.lf_error,
        "contact_cost": float(c.contact_cost or 0), "handling_cost": float(c.handling_cost or 0),
        "suppliers_default": c.suppliers_default or PHONE_SUPPLIERS, "limit_default": c.limit_default,
        "weekdays": c.weekdays or [True] * 7, "hook_token": c.hook_token, "tg_connected": bool(c.tg_chat_id),
    }


# ── вход и профиль ───────────────────────────────────────────────────────────

@router.post("/auth/login")
async def login(body: LoginIn, db: AsyncSession = Depends(get_db)):
    return {"token": await login_client(db, body.login, body.password)}


@router.get("/me")
async def me(c: CabClient = Depends(require_client)):
    return _client_dto(c)


@router.patch("/settings")
async def settings_patch(body: SettingsIn, c: CabClient = Depends(require_client), db: AsyncSession = Depends(get_db)):
    if body.contact_cost is not None:
        c.contact_cost = body.contact_cost
    if body.handling_cost is not None:
        c.handling_cost = body.handling_cost
    if body.suppliers_default is not None:
        c.suppliers_default = [s for s in body.suppliers_default if s in SUPPLIERS]
    if body.limit_default is not None:
        c.limit_default = max(0, body.limit_default)
    if body.weekdays is not None:
        if len(body.weekdays) != 7:
            raise HTTPException(400, "weekdays: 7 флагов, пн..вс")
        c.weekdays = list(body.weekdays)
    await db.commit()
    return _client_dto(c)


@router.get("/suppliers")
async def suppliers(c: CabClient = Depends(require_client)):
    return {"items": [{"code": k, "label": SUPPLIERS[k], "available": k in PHONE_SUPPLIERS} for k in SUPPLIERS]}


@router.get("/geo")
async def geo(c: CabClient = Depends(require_client), db: AsyncSession = Depends(get_db)):
    if not _GEO_CACHE["at"] or (utcnow() - _GEO_CACHE["at"]) > timedelta(hours=6):
        try:
            _GEO_CACHE["items"] = await (await lf_for(db)).geo_all()
            _GEO_CACHE["at"] = utcnow()
        except LFError as e:
            if not _GEO_CACHE["items"]:
                raise HTTPException(502, str(e))
    return {"items": _GEO_CACHE["items"]}


# ── источники ────────────────────────────────────────────────────────────────

async def _company(db: AsyncSession, client_id: int, name: str, cache: dict) -> CabCompany | None:
    name = (name or "").strip()[:200]
    if not name:
        return None
    key = name.lower()
    if key in cache:
        return cache[key]
    comp = (await db.execute(select(CabCompany).where(
        CabCompany.client_id == client_id, func.lower(CabCompany.name) == key))).scalar_one_or_none()
    if comp is None:
        comp = CabCompany(client_id=client_id, name=name)
        db.add(comp)
        await db.flush()
    cache[key] = comp
    return comp


@router.post("/sources", status_code=201)
async def add_sources(body: SourcesIn, c: CabClient = Depends(require_client), db: AsyncSession = Depends(get_db)):
    """Строки `номер` или `номер<разделитель>компания`. Дубли внутри проекта не добавляются."""
    sup = [s for s in body.suppliers if s in SUPPLIERS] or list(c.suppliers_default or PHONE_SUPPLIERS)
    delim = body.delimiter if body.delimiter in (";", ",", "\t", "|") else ";"
    existing = {p for p in (await db.execute(select(CabSource.phone).where(CabSource.client_id == c.id))).scalars().all()}
    cache: dict = {}
    added, dup, invalid, seen = [], 0, [], set()
    for line in body.text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(delim, 1)
        phone = norm_phone(parts[0])
        company = parts[1].strip() if len(parts) > 1 else ""
        if not phone:
            invalid.append(line[:40])
            continue
        if phone in existing or phone in seen:
            dup += 1
            continue
        seen.add(phone)
        comp = await _company(db, c.id, company, cache)
        db.add(CabSource(client_id=c.id, company_id=comp.id if comp else None, phone=phone, suppliers=sup,
                         limit=max(0, body.limit), geo_ids=list(body.geo_ids), lf_dirty=True))
        added.append(phone)
    await db.commit()
    return {"added": len(added), "duplicates": dup, "invalid": invalid[:20], "invalid_count": len(invalid),
            "note": "Источники уходят в Leads Factory в фоне: у каждого появится ID и статус в течение минуты."}


SORTS = {
    "id": CabSource.id, "phone": CabSource.phone, "added_at": CabSource.added_at, "limit": CabSource.limit,
    "contacts_total": CabSource.contacts_total, "contacts_today": CabSource.contacts_today,
    "last_contact_at": CabSource.last_contact_at, "repeats_total": CabSource.repeats_total,
}


@router.get("/sources")
async def list_sources(
    search: str | None = None,
    status: str | None = Query(None, pattern="^(on|off|pending|error)$"),
    company_id: int | None = None,
    sort: str = "added_at", order: str = "desc",
    page: int = 1, limit: int = Query(20, ge=1, le=5000),
    c: CabClient = Depends(require_client), db: AsyncSession = Depends(get_db),
):
    enabled = CabSource.enabled_by_user & CabSource.enabled_by_schedule
    stmt = select(CabSource, CabCompany.name).outerjoin(CabCompany, CabCompany.id == CabSource.company_id).where(CabSource.client_id == c.id)
    if search:
        s = f"%{search.strip()}%"
        stmt = stmt.where(or_(CabSource.phone.ilike(s), CabCompany.name.ilike(s)))
    if status == "on":
        stmt = stmt.where(enabled)
    elif status == "off":
        stmt = stmt.where(~enabled)
    elif status == "pending":
        stmt = stmt.where(CabSource.lf_source_id.is_(None))
    elif status == "error":
        stmt = stmt.where(CabSource.lf_error.isnot(None))
    if company_id:
        stmt = stmt.where(CabSource.company_id == company_id)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    col = {"company": CabCompany.name, "enabled": enabled}.get(sort) or SORTS.get(sort, CabSource.added_at)
    stmt = stmt.order_by(desc(col).nullslast() if order == "desc" else col.asc().nullsfirst(), CabSource.id)
    rows = (await db.execute(stmt.limit(limit).offset((page - 1) * limit))).all()
    return {"total": total, "page": page, "limit": limit, "items": [{
        "id": s.id, "lf_source_id": s.lf_source_id, "phone": s.phone, "company_id": s.company_id, "company": comp,
        "added_at": s.added_at, "enabled": bool(s.enabled_by_user and s.enabled_by_schedule),
        "enabled_by_user": s.enabled_by_user, "enabled_by_schedule": s.enabled_by_schedule,
        "suppliers": s.suppliers or [], "limit": s.limit, "geo_ids": s.geo_ids or [],
        "contacts_total": s.contacts_total, "contacts_today": s.contacts_today, "repeats_total": s.repeats_total,
        "last_contact_at": s.last_contact_at, "lf_dirty": s.lf_dirty, "lf_error": s.lf_error,
        "lf_sebes_14": float(s.lf_sebes_14) if s.lf_sebes_14 is not None else None,
        "lf_success_14": float(s.lf_success_14) if s.lf_success_14 is not None else None,
    } for s, comp in rows]}


@router.post("/sources/bulk")
async def bulk(body: BulkIn, c: CabClient = Depends(require_client), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(CabSource).where(CabSource.client_id == c.id, CabSource.id.in_(body.ids)))).scalars().all()
    if not rows:
        raise HTTPException(404, "Источники не найдены")
    for s in rows:
        if body.action == "limit":
            s.limit = max(0, int(body.value or 0))
        elif body.action == "enable":
            s.enabled_by_user = True
        elif body.action == "disable":
            s.enabled_by_user = False
        elif body.action == "suppliers":
            s.suppliers = [x for x in (body.value or []) if x in SUPPLIERS]
        elif body.action == "geo_add":
            s.geo_ids = sorted(set(s.geo_ids or []) | {int(x) for x in (body.value or [])})
        elif body.action == "geo_remove":
            s.geo_ids = sorted(set(s.geo_ids or []) - {int(x) for x in (body.value or [])})
        else:
            raise HTTPException(400, "action: limit | enable | disable | suppliers | geo_add | geo_remove")
        s.lf_dirty = True
    await db.commit()
    return {"updated": len(rows)}


# ── компании ─────────────────────────────────────────────────────────────────

@router.get("/companies")
async def companies(c: CabClient = Depends(require_client), db: AsyncSession = Depends(get_db)):
    src = (select(CabSource.company_id, func.count().label("sources"))
           .where(CabSource.client_id == c.id).group_by(CabSource.company_id).subquery())
    con = (select(CabContact.company_id,
                  func.count().label("contacts"),
                  func.count(func.distinct(CabContact.phone)).filter(CabContact.hook_status == "lead").label("leads"),
                  func.count(func.distinct(CabContact.phone)).filter(CabContact.hook_status == "qual").label("quals"),
                  func.count(func.distinct(CabContact.phone)).filter(CabContact.hook_status == "unsuccessful").label("unsuccessful"))
           .where(CabContact.client_id == c.id).group_by(CabContact.company_id).subquery())
    rows = (await db.execute(
        select(CabCompany, src.c.sources, con.c.contacts, con.c.leads, con.c.quals, con.c.unsuccessful)
        .outerjoin(src, src.c.company_id == CabCompany.id).outerjoin(con, con.c.company_id == CabCompany.id)
        .where(CabCompany.client_id == c.id).order_by(desc(func.coalesce(con.c.contacts, 0)), CabCompany.name))).all()
    per_contact = float(c.contact_cost or 0) + float(c.handling_cost or 0)
    items = []
    for comp, sources, contacts, leads, quals, uns in rows:
        contacts, leads, quals, uns = contacts or 0, leads or 0, quals or 0, uns or 0
        spend = contacts * per_contact
        items.append({
            "id": comp.id, "name": comp.name, "sources": sources or 0, "contacts": contacts,
            "leads": leads, "quals": quals, "unsuccessful": uns,
            "conversion_lead": round(leads / contacts * 100, 1) if contacts else None,
            "conversion_qual": round(quals / contacts * 100, 1) if contacts else None,
            "spend": round(spend, 2), "cost_per_lead": round(spend / leads, 2) if leads else None,
            "cost_per_qual": round(spend / quals, 2) if quals else None,
        })
    return {"items": items, "contact_cost": float(c.contact_cost or 0), "handling_cost": float(c.handling_cost or 0)}


# ── база контактов ───────────────────────────────────────────────────────────

def _contacts_stmt(c: CabClient, search: str | None, date_from: str | None, date_to: str | None, status: str | None):
    stmt = (select(CabContact, CabCompany.name).outerjoin(CabCompany, CabCompany.id == CabContact.company_id)
            .where(CabContact.client_id == c.id))
    if search:
        s = f"%{search.strip()}%"
        stmt = stmt.where(or_(CabContact.phone.ilike(s), CabContact.source_tag.ilike(s), CabCompany.name.ilike(s)))
    if date_from:
        stmt = stmt.where(CabContact.bought_at >= datetime.fromisoformat(date_from).replace(tzinfo=MSK))
    if date_to:
        stmt = stmt.where(CabContact.bought_at < datetime.fromisoformat(date_to).replace(tzinfo=MSK) + timedelta(days=1))
    if status:
        stmt = stmt.where(CabContact.hook_status == status) if status != "none" else stmt.where(CabContact.hook_status.is_(None))
    return stmt


def _contact_row(x: CabContact, comp: str | None) -> dict:
    src_phone = (x.source_tag or "").split("_")[1] if x.source_tag and "_" in x.source_tag else None
    return {"id": x.id, "phone": x.phone, "operator": x.operator, "region": x.region, "supplier": x.supplier,
            "supplier_label": SUPPLIERS.get(x.supplier or "", x.supplier), "source_phone": src_phone,
            "company": comp, "lf_status": x.lf_status, "bought_at": x.bought_at, "pulled_at": x.pulled_at,
            "hook_status": x.hook_status, "hook_status_at": x.hook_status_at}


@router.get("/contacts")
async def contacts(
    search: str | None = None, date_from: str | None = None, date_to: str | None = None, status: str | None = None,
    page: int = 1, limit: int = Query(50, ge=1, le=1000),
    c: CabClient = Depends(require_client), db: AsyncSession = Depends(get_db),
):
    stmt = _contacts_stmt(c, search, date_from, date_to, status)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    rows = (await db.execute(stmt.order_by(desc(CabContact.bought_at), desc(CabContact.id)).limit(limit).offset((page - 1) * limit))).all()
    return {"total": total, "page": page, "limit": limit, "items": [_contact_row(x, comp) for x, comp in rows]}


@router.get("/contacts/export.csv")
async def contacts_csv(search: str | None = None, date_from: str | None = None, date_to: str | None = None,
                       status: str | None = None, c: CabClient = Depends(require_client), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(_contacts_stmt(c, search, date_from, date_to, status).order_by(desc(CabContact.bought_at)).limit(100000))).all()
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["оператор", "источник", "контакт", "дата выгрузки", "поставщик", "компания", "регион", "статус LF", "наш статус"])
    for x, comp in rows:
        r = _contact_row(x, comp)
        w.writerow([r["operator"] or "", r["source_phone"] or "", r["phone"],
                    x.bought_at.astimezone(MSK).strftime("%d.%m.%Y %H:%M") if x.bought_at else "",
                    r["supplier_label"] or "", comp or "", r["region"] or "", r["lf_status"] or "", r["hook_status"] or ""])
    data = ("﻿" + buf.getvalue()).encode("utf-8")
    return StreamingResponse(iter([data]), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename=contacts_{c.login}.csv"})


@router.get("/stats")
async def stats(days: int = Query(30, le=365), c: CabClient = Depends(require_client), db: AsyncSession = Depends(get_db)):
    day = func.date(func.timezone("Europe/Moscow", CabContact.bought_at))
    since = utcnow() - timedelta(days=days)
    rows = (await db.execute(select(day, func.count(), func.count().filter(CabContact.lf_status == "new"))
                             .where(CabContact.client_id == c.id, CabContact.bought_at >= since)
                             .group_by(day).order_by(day))).all()
    daily = [{"day": d.isoformat(), "contacts": n, "new": nn} for d, n, nn in rows]
    avg = (sum(x["new"] for x in daily) / max(len(daily), 1)) if daily else 0
    total = (await db.execute(select(func.count()).where(CabContact.client_id == c.id))).scalar() or 0
    return {"daily": daily, "avg_per_day": round(avg, 1), "total": total,
            "days_left": round(c.balance_contacts / avg, 1) if (avg and c.balance_contacts) else None,
            "balance_contacts": c.balance_contacts}


# ── чёрный список ────────────────────────────────────────────────────────────

@router.get("/blacklist")
async def blacklist(c: CabClient = Depends(require_client), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(CabBlacklist).where(CabBlacklist.client_id == c.id).order_by(desc(CabBlacklist.id)))).scalars().all()
    return {"items": [{"id": b.id, "phone": b.phone, "sent_at": b.sent_at, "created_at": b.created_at} for b in rows]}


@router.post("/blacklist", status_code=201)
async def blacklist_add(body: BlacklistIn, c: CabClient = Depends(require_client), db: AsyncSession = Depends(get_db)):
    have = {p for p in (await db.execute(select(CabBlacklist.phone).where(CabBlacklist.client_id == c.id))).scalars().all()}
    added, bad = 0, 0
    for line in re.split(r"[\s,;]+", body.text):
        p = norm_phone(line)
        if not p:
            if line.strip():
                bad += 1
            continue
        if p in have:
            continue
        db.add(CabBlacklist(client_id=c.id, phone=p))
        have.add(p)
        added += 1
    await db.commit()
    return {"added": added, "invalid": bad, "note": "Уйдут в Leads Factory в течение минуты"}


# ── статусы по вебхуку (без входа, по токену клиента) ────────────────────────

STATUS_MAP = {
    "unsuccessful": "unsuccessful", "fail": "unsuccessful", "failed": "unsuccessful", "неуспешный": "unsuccessful",
    "неуспех": "unsuccessful", "не успешный": "unsuccessful", "негатив": "unsuccessful",
    "lead": "lead", "лид": "lead", "success": "lead", "успешный": "lead",
    "qual": "qual", "qualified": "qual", "квал": "qual", "квал-лид": "qual", "квал лид": "qual", "квалифицированный": "qual",
}


async def _hook(db: AsyncSession, token: str, data: dict) -> dict:
    c = (await db.execute(select(CabClient).where(CabClient.hook_token == token))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Неизвестный токен")
    phone = norm_phone(str(data.get("phone") or data.get("mobile_tel") or data.get("tel") or ""))
    status = STATUS_MAP.get(str(data.get("status") or "").strip().lower())
    row = CabInbox(client_id=c.id, raw=data, phone=phone, status=status)
    if not phone or not status:
        row.state, row.error = "error", "нужны phone и status ∈ unsuccessful | lead | qual"
    db.add(row)
    await db.commit()
    return {"ok": row.state != "error", "id": row.id, "error": row.error}


@router.post("/hook/{token}")
async def hook_post(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    try:
        data = await request.json()
    except ValueError:
        data = dict((await request.form()).items()) if request.headers.get("content-type", "").startswith("application/x-www-form") else {}
    if not isinstance(data, dict):
        data = {}
    data = {**dict(request.query_params), **data}
    return await _hook(db, token, data)


@router.get("/hook/{token}")
async def hook_get(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    return await _hook(db, token, dict(request.query_params))
