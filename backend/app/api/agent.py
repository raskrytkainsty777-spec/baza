"""Кабинет агента досбора: задачи, ресурсы, добавление найденных источников, баланс, реквизиты.
Вход — логин и пароль агента, свои сессии, к данным клиента доступ только через свои задачи.
"""
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import CabAgent, CabClient, CabCompany, CabFoundSource, CabResource, CabSource, CabTask, CabTaskAgent
from .cab import norm_phone
from .cab_auth import login_agent, require_agent
from .cab_dosbor import purchase_found

router = APIRouter(prefix="/api/agent", tags=["agent"])


class LoginIn(BaseModel):
    login: str
    password: str


class RequisitesIn(BaseModel):
    kind: str            # sbp | card
    bank: str
    value: str           # номер телефона для СБП или номер карты


class SourceIn(BaseModel):
    company_id: int
    phone: str


async def _task_for(db: AsyncSession, a: CabAgent, task_id: int) -> CabTask:
    t = await db.get(CabTask, task_id)
    link = (await db.execute(select(CabTaskAgent).where(CabTaskAgent.task_id == task_id, CabTaskAgent.agent_id == a.id))).scalar_one_or_none()
    if not t or not link or t.client_id != a.client_id:
        raise HTTPException(404, "Задача не найдена")
    if not t.enabled:
        raise HTTPException(400, "Задача выключена")
    return t


async def _task_dto(db: AsyncSession, a: CabAgent, t: CabTask) -> dict:
    found = (await db.execute(select(func.count()).where(CabFoundSource.task_id == t.id))).scalar() or 0
    mine = (await db.execute(select(func.count()).where(CabFoundSource.task_id == t.id, CabFoundSource.agent_id == a.id))).scalar() or 0
    resources = (await db.execute(select(func.count()).where(CabResource.list_id == t.list_id))).scalar() or 0
    return {"id": t.id, "name": t.name, "price_per_source": float(t.price_per_source or 0), "limit_sources": t.limit_sources,
            "found": found, "mine": mine, "left": max(0, t.limit_sources - found) if t.limit_sources else None,
            "resources": resources, "enabled": t.enabled}


@router.post("/auth/login")
async def login(body: LoginIn, db: AsyncSession = Depends(get_db)):
    return {"token": await login_agent(db, body.login, body.password)}


@router.get("/me")
async def me(a: CabAgent = Depends(require_agent), db: AsyncSession = Depends(get_db)):
    client = await db.get(CabClient, a.client_id)
    tasks = (await db.execute(select(CabTask).join(CabTaskAgent, CabTaskAgent.task_id == CabTask.id)
                              .where(CabTaskAgent.agent_id == a.id, CabTask.enabled.is_(True)).order_by(desc(CabTask.id)))).scalars().all()
    return {"id": a.id, "name": a.name, "login": a.login, "balance": float(a.balance or 0), "requisites": a.requisites,
            "client_name": client.name if client else None, "tasks": [await _task_dto(db, a, t) for t in tasks]}


@router.patch("/requisites")
async def requisites(body: RequisitesIn, a: CabAgent = Depends(require_agent), db: AsyncSession = Depends(get_db)):
    if body.kind not in ("sbp", "card"):
        raise HTTPException(400, "kind: sbp | card")
    if not body.bank.strip() or not body.value.strip():
        raise HTTPException(400, "Укажите банк и реквизит")
    a.requisites = {"kind": body.kind, "bank": body.bank.strip()[:100], "value": body.value.strip()[:60]}
    await db.commit()
    return {"requisites": a.requisites}


@router.get("/tasks/{task_id}")
async def task(task_id: int, a: CabAgent = Depends(require_agent), db: AsyncSession = Depends(get_db)):
    t = await _task_for(db, a, task_id)
    return await _task_dto(db, a, t)


@router.get("/tasks/{task_id}/resources")
async def resources(task_id: int, a: CabAgent = Depends(require_agent), db: AsyncSession = Depends(get_db)):
    t = await _task_for(db, a, task_id)
    rows = (await db.execute(select(CabResource, CabCompany.name).outerjoin(CabCompany, CabCompany.id == CabResource.company_id)
                             .where(CabResource.list_id == t.list_id).order_by(CabResource.id))).all()
    return {"items": [{"n": i + 1, "url": r.url, "company": comp} for i, (r, comp) in enumerate(rows)]}


@router.get("/tasks/{task_id}/companies")
async def companies(task_id: int, a: CabAgent = Depends(require_agent), db: AsyncSession = Depends(get_db)):
    t = await _task_for(db, a, task_id)
    rows = (await db.execute(select(CabCompany).join(CabResource, CabResource.company_id == CabCompany.id)
                             .where(CabResource.list_id == t.list_id).group_by(CabCompany.id).order_by(CabCompany.name))).scalars().all()
    return {"items": [{"id": c.id, "name": c.name} for c in rows]}


@router.get("/tasks/{task_id}/check")
async def check(task_id: int, phone: str, a: CabAgent = Depends(require_agent), db: AsyncSession = Depends(get_db)):
    await _task_for(db, a, task_id)
    p = norm_phone(phone)
    if not p:
        return {"ok": False, "reason": "Номер не распознан: нужно 11 цифр, начиная с 7"}
    dup = (await db.execute(select(CabFoundSource.id).where(CabFoundSource.client_id == a.client_id, CabFoundSource.phone == p))).scalar()
    dup2 = (await db.execute(select(CabSource.id).where(CabSource.client_id == a.client_id, CabSource.phone == p))).scalar()
    if dup or dup2:
        return {"ok": False, "phone": p, "reason": "Такой источник уже есть в проекте"}
    return {"ok": True, "phone": p}


@router.post("/tasks/{task_id}/sources", status_code=201)
async def add_source(task_id: int, body: SourceIn, a: CabAgent = Depends(require_agent), db: AsyncSession = Depends(get_db)):
    t = await _task_for(db, a, task_id)
    if not a.requisites:
        raise HTTPException(400, "Сначала добавьте реквизиты для выплаты")
    p = norm_phone(body.phone)
    if not p:
        raise HTTPException(400, "Номер не распознан")
    comp = await db.get(CabCompany, body.company_id)
    ok_comp = comp and comp.client_id == a.client_id and (await db.execute(
        select(CabResource.id).where(CabResource.list_id == t.list_id, CabResource.company_id == comp.id))).scalar()
    if not ok_comp:
        raise HTTPException(400, "Компания должна быть из списка ресурсов задачи")
    found = (await db.execute(select(func.count()).where(CabFoundSource.task_id == t.id))).scalar() or 0
    if t.limit_sources and found >= t.limit_sources:
        raise HTTPException(400, "Лимит задачи исчерпан — попросите заказчика поднять лимит")
    dup = (await db.execute(select(CabFoundSource.id).where(CabFoundSource.client_id == a.client_id, CabFoundSource.phone == p))).scalar()
    dup2 = (await db.execute(select(CabSource.id).where(CabSource.client_id == a.client_id, CabSource.phone == p))).scalar()
    if dup or dup2:
        raise HTTPException(409, "Такой источник уже есть в проекте")
    f = CabFoundSource(client_id=a.client_id, task_id=t.id, agent_id=a.id, company_id=comp.id, phone=p)
    db.add(f)
    await db.flush()
    a.balance = Decimal(a.balance or 0) + Decimal(t.price_per_source or 0)
    purchased = 0
    if t.to_purchase:
        client = await db.get(CabClient, a.client_id)
        purchased = await purchase_found(db, client, t, [f])
    await db.commit()
    return {"id": f.id, "phone": p, "balance": float(a.balance), "purchased": bool(purchased),
            "left": max(0, t.limit_sources - found - 1) if t.limit_sources else None}


@router.get("/tasks/{task_id}/my")
async def my_sources(task_id: int, a: CabAgent = Depends(require_agent), db: AsyncSession = Depends(get_db)):
    t = await _task_for(db, a, task_id)
    rows = (await db.execute(select(CabFoundSource, CabCompany.name).outerjoin(CabCompany, CabCompany.id == CabFoundSource.company_id)
                             .where(CabFoundSource.task_id == t.id, CabFoundSource.agent_id == a.id).order_by(desc(CabFoundSource.id)).limit(200))).all()
    return {"items": [{"id": f.id, "phone": f.phone, "company": comp, "added_at": f.added_at} for f, comp in rows]}
