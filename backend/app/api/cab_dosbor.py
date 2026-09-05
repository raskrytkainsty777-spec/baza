"""Досбор — клиент управляет агентами, списками ресурсов и задачами; агенты приносят новые
номера-источники. Всё под /api/cab/dosbor, вход клиента.
"""
import csv
import io
import re
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import (
    CabAgent, CabClient, CabCompany, CabFoundSource, CabPayout, CabResource, CabResourceList, CabSource, CabTask,
    CabTaskAgent,
)
from ..services.leadsfactory.client import MSK, PHONE_SUPPLIERS, SUPPLIERS
from ..workers.common import utcnow
from .cab import _company
from .cab_auth import hash_password, require_client

router = APIRouter(prefix="/api/cab/dosbor", tags=["cab-dosbor"])


class AgentIn(BaseModel):
    login: str
    password: str
    name: str


class AgentPatch(BaseModel):
    name: str | None = None
    password: str | None = None
    is_active: bool | None = None


class PayoutIn(BaseModel):
    note: str | None = None


class ListIn(BaseModel):
    name: str
    text: str = ""
    delimiter: str = ";"


class ResourcesIn(BaseModel):
    text: str
    delimiter: str = ";"


class TaskIn(BaseModel):
    name: str
    list_id: int
    agent_ids: list[int] = []
    price_per_source: Decimal = Decimal(0)
    limit_sources: int = 0
    to_purchase: bool = False
    purchase_limit: int = 5
    purchase_suppliers: list[str] = []


class TaskPatch(BaseModel):
    name: str | None = None
    agent_ids: list[int] | None = None
    price_per_source: Decimal | None = None
    limit_sources: int | None = None
    to_purchase: bool | None = None
    purchase_limit: int | None = None
    purchase_suppliers: list[str] | None = None
    enabled: bool | None = None


def _url(raw: str) -> str | None:
    u = (raw or "").strip()
    if not u:
        return None
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u
    return u[:500]


# ── агенты ───────────────────────────────────────────────────────────────────

def _agent_dto(a: CabAgent, extra: dict | None = None) -> dict:
    d = {"id": a.id, "login": a.login, "name": a.name, "balance": float(a.balance or 0), "requisites": a.requisites,
         "is_active": a.is_active, "created_at": a.created_at}
    if extra:
        d.update(extra)
    return d


@router.get("/agents")
async def agents(c: CabClient = Depends(require_client), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(CabAgent).where(CabAgent.client_id == c.id).order_by(CabAgent.id))).scalars().all()
    found = dict((await db.execute(select(CabFoundSource.agent_id, func.count()).where(CabFoundSource.client_id == c.id)
                                   .group_by(CabFoundSource.agent_id))).all())
    paid = dict((await db.execute(select(CabPayout.agent_id, func.coalesce(func.sum(CabPayout.amount), 0))
                                  .join(CabAgent, CabAgent.id == CabPayout.agent_id).where(CabAgent.client_id == c.id)
                                  .group_by(CabPayout.agent_id))).all())
    return {"items": [_agent_dto(a, {"found_total": found.get(a.id, 0), "paid_total": float(paid.get(a.id, 0))}) for a in rows]}


@router.post("/agents", status_code=201)
async def agent_create(body: AgentIn, c: CabClient = Depends(require_client), db: AsyncSession = Depends(get_db)):
    login = body.login.strip().lower()
    if not login or len(body.password) < 6 or not body.name.strip():
        raise HTTPException(400, "Логин, имя и пароль от 6 символов обязательны")
    if (await db.execute(select(CabAgent).where(CabAgent.login == login))).scalar_one_or_none():
        raise HTTPException(409, "Такой логин агента уже есть")
    a = CabAgent(client_id=c.id, login=login, password_hash=hash_password(body.password), name=body.name.strip())
    db.add(a)
    await db.commit()
    return _agent_dto(a)


@router.patch("/agents/{agent_id}")
async def agent_patch(agent_id: int, body: AgentPatch, c: CabClient = Depends(require_client), db: AsyncSession = Depends(get_db)):
    a = await db.get(CabAgent, agent_id)
    if not a or a.client_id != c.id:
        raise HTTPException(404, "Агент не найден")
    if body.name is not None:
        a.name = body.name.strip()
    if body.password:
        if len(body.password) < 6:
            raise HTTPException(400, "Пароль от 6 символов")
        a.password_hash = hash_password(body.password)
    if body.is_active is not None:
        a.is_active = body.is_active
    await db.commit()
    return _agent_dto(a)


@router.get("/agents/{agent_id}/payouts")
async def payouts(agent_id: int, c: CabClient = Depends(require_client), db: AsyncSession = Depends(get_db)):
    a = await db.get(CabAgent, agent_id)
    if not a or a.client_id != c.id:
        raise HTTPException(404, "Агент не найден")
    rows = (await db.execute(select(CabPayout).where(CabPayout.agent_id == agent_id).order_by(desc(CabPayout.id)))).scalars().all()
    return {"items": [{"id": p.id, "amount": float(p.amount), "requisites": p.requisites, "note": p.note, "paid_at": p.paid_at} for p in rows]}


@router.post("/agents/{agent_id}/payout")
async def payout(agent_id: int, body: PayoutIn, c: CabClient = Depends(require_client), db: AsyncSession = Depends(get_db)):
    """«Обнулить баланс»: записываем выплату на текущую сумму со снимком реквизитов, баланс → 0."""
    a = await db.get(CabAgent, agent_id)
    if not a or a.client_id != c.id:
        raise HTTPException(404, "Агент не найден")
    amount = Decimal(a.balance or 0)
    if amount <= 0:
        raise HTTPException(400, "Баланс уже нулевой")
    p = CabPayout(agent_id=a.id, amount=amount, requisites=a.requisites, note=(body.note or "")[:300] or None)
    a.balance = Decimal(0)
    db.add(p)
    await db.commit()
    return {"paid": float(amount), "requisites": a.requisites, "payout_id": p.id}


# ── ресурсы ──────────────────────────────────────────────────────────────────

async def _add_resources(db: AsyncSession, c: CabClient, lst: CabResourceList, text: str, delim: str) -> int:
    delim = delim if delim in (";", ",", "\t", "|") else ";"
    have = {r.url for r in (await db.execute(select(CabResource).where(CabResource.list_id == lst.id))).scalars().all()}
    cache: dict = {}
    n = 0
    for line in text.splitlines():
        parts = [p.strip() for p in line.strip().split(delim, 1)]
        url = _url(parts[0]) if parts and parts[0] else None
        if not url or url in have:
            continue
        comp = await _company(db, c.id, parts[1] if len(parts) > 1 else "", cache)
        db.add(CabResource(list_id=lst.id, url=url, company_id=comp.id if comp else None))
        have.add(url)
        n += 1
    return n


@router.get("/lists")
async def lists(c: CabClient = Depends(require_client), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(CabResourceList, func.count(CabResource.id), func.count(func.distinct(CabResource.company_id)))
        .outerjoin(CabResource, CabResource.list_id == CabResourceList.id)
        .where(CabResourceList.client_id == c.id).group_by(CabResourceList.id).order_by(desc(CabResourceList.id)))).all()
    return {"items": [{"id": l.id, "name": l.name, "resources": n, "companies": k, "created_at": l.created_at} for l, n, k in rows]}


@router.post("/lists", status_code=201)
async def list_create(body: ListIn, c: CabClient = Depends(require_client), db: AsyncSession = Depends(get_db)):
    if not body.name.strip():
        raise HTTPException(400, "Название списка обязательно")
    lst = CabResourceList(client_id=c.id, name=body.name.strip()[:200])
    db.add(lst)
    await db.flush()
    n = await _add_resources(db, c, lst, body.text, body.delimiter)
    await db.commit()
    return {"id": lst.id, "name": lst.name, "resources": n}


@router.post("/lists/{list_id}/resources")
async def list_add(list_id: int, body: ResourcesIn, c: CabClient = Depends(require_client), db: AsyncSession = Depends(get_db)):
    lst = await db.get(CabResourceList, list_id)
    if not lst or lst.client_id != c.id:
        raise HTTPException(404, "Список не найден")
    n = await _add_resources(db, c, lst, body.text, body.delimiter)
    await db.commit()
    return {"added": n}


@router.get("/lists/{list_id}/resources")
async def list_resources(list_id: int, c: CabClient = Depends(require_client), db: AsyncSession = Depends(get_db)):
    lst = await db.get(CabResourceList, list_id)
    if not lst or lst.client_id != c.id:
        raise HTTPException(404, "Список не найден")
    rows = (await db.execute(select(CabResource, CabCompany.name).outerjoin(CabCompany, CabCompany.id == CabResource.company_id)
                             .where(CabResource.list_id == list_id).order_by(CabResource.id))).all()
    return {"items": [{"id": r.id, "url": r.url, "company_id": r.company_id, "company": comp} for r, comp in rows]}


# ── задачи ───────────────────────────────────────────────────────────────────

async def _task_dto(db: AsyncSession, t: CabTask) -> dict:
    agent_ids = (await db.execute(select(CabTaskAgent.agent_id).where(CabTaskAgent.task_id == t.id))).scalars().all()
    found = (await db.execute(select(func.count()).where(CabFoundSource.task_id == t.id))).scalar() or 0
    lst = await db.get(CabResourceList, t.list_id)
    return {"id": t.id, "name": t.name, "list_id": t.list_id, "list_name": lst.name if lst else None, "agent_ids": agent_ids,
            "price_per_source": float(t.price_per_source or 0), "limit_sources": t.limit_sources, "found": found,
            "to_purchase": t.to_purchase, "purchase_limit": t.purchase_limit, "purchase_suppliers": t.purchase_suppliers or [],
            "enabled": t.enabled, "created_at": t.created_at}


async def _set_agents(db: AsyncSession, c: CabClient, t: CabTask, agent_ids: list[int]) -> None:
    valid = set((await db.execute(select(CabAgent.id).where(CabAgent.client_id == c.id, CabAgent.id.in_(agent_ids or [-1])))).scalars().all())
    current = (await db.execute(select(CabTaskAgent).where(CabTaskAgent.task_id == t.id))).scalars().all()
    for ta in current:
        if ta.agent_id not in valid:
            await db.delete(ta)
    have = {ta.agent_id for ta in current}
    for aid in valid - have:
        db.add(CabTaskAgent(task_id=t.id, agent_id=aid))


@router.get("/tasks")
async def tasks(c: CabClient = Depends(require_client), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(CabTask).where(CabTask.client_id == c.id).order_by(desc(CabTask.id)))).scalars().all()
    return {"items": [await _task_dto(db, t) for t in rows]}


@router.post("/tasks", status_code=201)
async def task_create(body: TaskIn, c: CabClient = Depends(require_client), db: AsyncSession = Depends(get_db)):
    lst = await db.get(CabResourceList, body.list_id)
    if not lst or lst.client_id != c.id:
        raise HTTPException(404, "Список ресурсов не найден")
    t = CabTask(client_id=c.id, name=body.name.strip()[:200] or lst.name, list_id=lst.id,
                price_per_source=body.price_per_source, limit_sources=max(0, body.limit_sources),
                to_purchase=body.to_purchase, purchase_limit=max(0, body.purchase_limit),
                purchase_suppliers=[s for s in body.purchase_suppliers if s in SUPPLIERS] or list(c.suppliers_default or PHONE_SUPPLIERS))
    db.add(t)
    await db.flush()
    await _set_agents(db, c, t, body.agent_ids)
    await db.commit()
    return await _task_dto(db, t)


@router.patch("/tasks/{task_id}")
async def task_patch(task_id: int, body: TaskPatch, c: CabClient = Depends(require_client), db: AsyncSession = Depends(get_db)):
    t = await db.get(CabTask, task_id)
    if not t or t.client_id != c.id:
        raise HTTPException(404, "Задача не найдена")
    if body.name is not None:
        t.name = body.name.strip()[:200]
    if body.price_per_source is not None:
        t.price_per_source = body.price_per_source
    if body.limit_sources is not None:
        t.limit_sources = max(0, body.limit_sources)
    if body.to_purchase is not None:
        t.to_purchase = body.to_purchase
    if body.purchase_limit is not None:
        t.purchase_limit = max(0, body.purchase_limit)
    if body.purchase_suppliers is not None:
        t.purchase_suppliers = [s for s in body.purchase_suppliers if s in SUPPLIERS]
    if body.enabled is not None:
        t.enabled = body.enabled
    if body.agent_ids is not None:
        await _set_agents(db, c, t, body.agent_ids)
    await db.commit()
    return await _task_dto(db, t)


@router.get("/tasks/{task_id}/stats")
async def task_stats(task_id: int, c: CabClient = Depends(require_client), db: AsyncSession = Depends(get_db)):
    t = await db.get(CabTask, task_id)
    if not t or t.client_id != c.id:
        raise HTTPException(404, "Задача не найдена")
    day = func.date(func.timezone("Europe/Moscow", CabFoundSource.added_at))
    rows = (await db.execute(select(day, CabAgent.name, func.count()).select_from(CabFoundSource)
                             .join(CabAgent, CabAgent.id == CabFoundSource.agent_id)
                             .where(CabFoundSource.task_id == task_id).group_by(day, CabAgent.name).order_by(desc(day), CabAgent.name))).all()
    by_agent = (await db.execute(select(CabAgent.name, func.count()).select_from(CabFoundSource)
                                 .join(CabAgent, CabAgent.id == CabFoundSource.agent_id)
                                 .where(CabFoundSource.task_id == task_id).group_by(CabAgent.name).order_by(desc(func.count())))).all()
    purchased = (await db.execute(select(func.count()).where(CabFoundSource.task_id == task_id, CabFoundSource.source_id.isnot(None)))).scalar() or 0
    return {"task": await _task_dto(db, t), "purchased": purchased,
            "by_day": [{"day": d.isoformat(), "agent": a, "count": n} for d, a, n in rows],
            "by_agent": [{"agent": a, "count": n} for a, n in by_agent]}


@router.get("/tasks/{task_id}/found")
async def task_found(task_id: int, c: CabClient = Depends(require_client), db: AsyncSession = Depends(get_db)):
    t = await db.get(CabTask, task_id)
    if not t or t.client_id != c.id:
        raise HTTPException(404, "Задача не найдена")
    rows = (await db.execute(select(CabFoundSource, CabAgent.name, CabCompany.name)
                             .join(CabAgent, CabAgent.id == CabFoundSource.agent_id)
                             .outerjoin(CabCompany, CabCompany.id == CabFoundSource.company_id)
                             .where(CabFoundSource.task_id == task_id).order_by(desc(CabFoundSource.id)))).all()
    return {"items": [{"id": f.id, "phone": f.phone, "agent": a, "company": comp, "added_at": f.added_at, "purchased": f.source_id is not None}
                      for f, a, comp in rows]}


@router.get("/tasks/{task_id}/export")
async def task_export(task_id: int, fmt: str = "csv", c: CabClient = Depends(require_client), db: AsyncSession = Depends(get_db)):
    t = await db.get(CabTask, task_id)
    if not t or t.client_id != c.id:
        raise HTTPException(404, "Задача не найдена")
    rows = (await db.execute(select(CabFoundSource, CabAgent.name, CabCompany.name)
                             .join(CabAgent, CabAgent.id == CabFoundSource.agent_id)
                             .outerjoin(CabCompany, CabCompany.id == CabFoundSource.company_id)
                             .where(CabFoundSource.task_id == task_id).order_by(CabFoundSource.id))).all()
    if fmt == "numbers":
        data = "\n".join(f.phone for f, _, _ in rows).encode("utf-8")
        return StreamingResponse(iter([data]), media_type="text/plain",
                                 headers={"Content-Disposition": f"attachment; filename=task_{task_id}_numbers.txt"})
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["номер", "компания", "агент", "дата", "в закупке"])
    for f, a, comp in rows:
        w.writerow([f.phone, comp or "", a, f.added_at.astimezone(MSK).strftime("%d.%m.%Y %H:%M"), "да" if f.source_id else "нет"])
    data = ("﻿" + buf.getvalue()).encode("utf-8")
    return StreamingResponse(iter([data]), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename=task_{task_id}.csv"})


async def purchase_found(db: AsyncSession, c: CabClient, t: CabTask, found: list[CabFoundSource]) -> int:
    """Найденные агентами номера → источники клиента (уйдут в LF воркером)."""
    have = {s.phone: s for s in (await db.execute(select(CabSource).where(CabSource.client_id == c.id))).scalars().all()}
    sup = t.purchase_suppliers or list(c.suppliers_default or PHONE_SUPPLIERS)
    n = 0
    for f in found:
        if f.source_id:
            continue
        s = have.get(f.phone)
        if s is None:
            s = CabSource(client_id=c.id, company_id=f.company_id, phone=f.phone, suppliers=sup,
                          limit=t.purchase_limit, lf_dirty=True, found_by_agent_id=f.agent_id)
            db.add(s)
            await db.flush()
            have[f.phone] = s
            n += 1
        f.source_id = s.id
    return n


@router.post("/tasks/{task_id}/to-purchase")
async def task_to_purchase(task_id: int, c: CabClient = Depends(require_client), db: AsyncSession = Depends(get_db)):
    t = await db.get(CabTask, task_id)
    if not t or t.client_id != c.id:
        raise HTTPException(404, "Задача не найдена")
    found = (await db.execute(select(CabFoundSource).where(CabFoundSource.task_id == task_id, CabFoundSource.source_id.is_(None)))).scalars().all()
    n = await purchase_found(db, c, t, found)
    await db.commit()
    return {"purchased": n, "note": "Уйдут в Leads Factory в течение минуты"}
