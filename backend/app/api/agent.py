"""Кабинет агента досбора: проекты и их задачи, ресурсы, добавление найденных источников,
баланс, реквизиты.

Агенты общие для всех проектов (решение заказчика 21.09.2026): один логин на человека,
агент видит активные задачи всех клиентов, выбирает проект и работает по его списку сайтов.
Назначение агентов на задачу в кабинете клиента — только для учёта: при первом найденном
источнике агент привязывается к задаче сам. Баланс и реквизиты — общие, не по проекту.
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


async def _task_for(db: AsyncSession, task_id: int) -> tuple[CabTask, CabClient]:
    """Любая включённая задача активного клиента доступна любому агенту."""
    t = await db.get(CabTask, task_id)
    c = await db.get(CabClient, t.client_id) if t else None
    if not t or not c or not c.is_active:
        raise HTTPException(404, "Задача не найдена")
    if not t.enabled:
        raise HTTPException(400, "Задача выключена")
    return t, c


async def _task_dto(db: AsyncSession, a: CabAgent, t: CabTask, c: CabClient) -> dict:
    found = (await db.execute(select(func.count()).where(CabFoundSource.task_id == t.id))).scalar() or 0
    mine = (await db.execute(select(func.count()).where(CabFoundSource.task_id == t.id, CabFoundSource.agent_id == a.id))).scalar() or 0
    resources = (await db.execute(select(func.count()).where(CabResource.list_id == t.list_id))).scalar() or 0
    return {"id": t.id, "name": t.name, "client_id": c.id, "client_name": c.name,
            "price_per_source": float(t.price_per_source or 0), "limit_sources": t.limit_sources,
            "found": found, "mine": mine, "left": max(0, t.limit_sources - found) if t.limit_sources else None,
            "resources": resources, "enabled": t.enabled}


@router.post("/auth/login")
async def login(body: LoginIn, db: AsyncSession = Depends(get_db)):
    return {"token": await login_agent(db, body.login, body.password)}


@router.get("/me")
async def me(a: CabAgent = Depends(require_agent), db: AsyncSession = Depends(get_db)):
    """Агент и все доступные проекты: активные клиенты с включёнными задачами.

    «Мои» (mine) — проекты, где агент назначен на задачу или уже приносил номера;
    они идут первыми и в кабинете показаны отдельным блоком (просьба заказчика 21.09.2026).
    """
    rows = (await db.execute(select(CabTask, CabClient).join(CabClient, CabClient.id == CabTask.client_id)
                             .where(CabTask.enabled.is_(True), CabClient.is_active.is_(True))
                             .order_by(CabClient.name, desc(CabTask.id)))).all()
    linked = set((await db.execute(select(CabTaskAgent.task_id).where(CabTaskAgent.agent_id == a.id))).scalars().all())
    worked = set((await db.execute(select(CabFoundSource.client_id).where(CabFoundSource.agent_id == a.id).distinct())).scalars().all())
    projects: dict[int, dict] = {}
    for t, c in rows:
        p = projects.setdefault(c.id, {"id": c.id, "name": c.name, "mine": c.id in worked, "tasks": []})
        d = await _task_dto(db, a, t, c)
        d["mine"] = t.id in linked
        p["mine"] = p["mine"] or d["mine"]
        p["tasks"].append(d)
    ordered = sorted(projects.values(), key=lambda p: (not p["mine"], p["name"]))
    return {"id": a.id, "name": a.name, "login": a.login, "balance": float(a.balance or 0), "requisites": a.requisites,
            "projects": ordered,
            "tasks": [t for p in ordered for t in p["tasks"]]}


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
    t, c = await _task_for(db, task_id)
    return await _task_dto(db, a, t, c)


@router.get("/tasks/{task_id}/resources")
async def resources(task_id: int, a: CabAgent = Depends(require_agent), db: AsyncSession = Depends(get_db)):
    t, _ = await _task_for(db, task_id)
    rows = (await db.execute(select(CabResource, CabCompany.name).outerjoin(CabCompany, CabCompany.id == CabResource.company_id)
                             .where(CabResource.list_id == t.list_id).order_by(CabResource.id))).all()
    return {"items": [{"n": i + 1, "url": r.url, "company": comp} for i, (r, comp) in enumerate(rows)]}


@router.get("/tasks/{task_id}/companies")
async def companies(task_id: int, a: CabAgent = Depends(require_agent), db: AsyncSession = Depends(get_db)):
    t, _ = await _task_for(db, task_id)
    rows = (await db.execute(select(CabCompany).join(CabResource, CabResource.company_id == CabCompany.id)
                             .where(CabResource.list_id == t.list_id).group_by(CabCompany.id).order_by(CabCompany.name))).scalars().all()
    return {"items": [{"id": c.id, "name": c.name} for c in rows]}


async def _exists_in_project(db: AsyncSession, client_id: int, phone: str) -> bool:
    dup = (await db.execute(select(CabFoundSource.id).where(CabFoundSource.client_id == client_id, CabFoundSource.phone == phone))).scalar()
    dup2 = (await db.execute(select(CabSource.id).where(CabSource.client_id == client_id, CabSource.phone == phone))).scalar()
    return bool(dup or dup2)


@router.get("/tasks/{task_id}/check")
async def check(task_id: int, phone: str, a: CabAgent = Depends(require_agent), db: AsyncSession = Depends(get_db)):
    t, _ = await _task_for(db, task_id)
    p = norm_phone(phone)
    if not p:
        return {"ok": False, "reason": "Номер не распознан: нужно 11 цифр, начиная с 7"}
    if await _exists_in_project(db, t.client_id, p):
        return {"ok": False, "phone": p, "reason": "Такой источник уже есть в проекте"}
    return {"ok": True, "phone": p}


@router.post("/tasks/{task_id}/sources", status_code=201)
async def add_source(task_id: int, body: SourceIn, a: CabAgent = Depends(require_agent), db: AsyncSession = Depends(get_db)):
    t, client = await _task_for(db, task_id)
    if not a.requisites:
        raise HTTPException(400, "Сначала добавьте реквизиты для выплаты")
    p = norm_phone(body.phone)
    if not p:
        raise HTTPException(400, "Номер не распознан")
    comp = await db.get(CabCompany, body.company_id)
    ok_comp = comp and comp.client_id == t.client_id and (await db.execute(
        select(CabResource.id).where(CabResource.list_id == t.list_id, CabResource.company_id == comp.id))).scalar()
    if not ok_comp:
        raise HTTPException(400, "Компания должна быть из списка ресурсов задачи")
    found = (await db.execute(select(func.count()).where(CabFoundSource.task_id == t.id))).scalar() or 0
    if t.limit_sources and found >= t.limit_sources:
        raise HTTPException(400, "Лимит задачи исчерпан — попросите заказчика поднять лимит")
    if await _exists_in_project(db, t.client_id, p):
        raise HTTPException(409, "Такой источник уже есть в проекте")
    f = CabFoundSource(client_id=t.client_id, task_id=t.id, agent_id=a.id, company_id=comp.id, phone=p)
    db.add(f)
    # привязка к задаче — для учёта в кабинете клиента («агентов: N», статистика по агентам)
    link = (await db.execute(select(CabTaskAgent).where(CabTaskAgent.task_id == t.id, CabTaskAgent.agent_id == a.id))).scalar_one_or_none()
    if link is None:
        db.add(CabTaskAgent(task_id=t.id, agent_id=a.id))
    await db.flush()
    a.balance = Decimal(a.balance or 0) + Decimal(t.price_per_source or 0)
    purchased = 0
    if t.to_purchase:
        purchased = await purchase_found(db, client, t, [f])
    await db.commit()
    return {"id": f.id, "phone": p, "balance": float(a.balance), "purchased": bool(purchased),
            "left": max(0, t.limit_sources - found - 1) if t.limit_sources else None}


@router.get("/tasks/{task_id}/my")
async def my_sources(task_id: int, a: CabAgent = Depends(require_agent), db: AsyncSession = Depends(get_db)):
    t, _ = await _task_for(db, task_id)
    rows = (await db.execute(select(CabFoundSource, CabCompany.name).outerjoin(CabCompany, CabCompany.id == CabFoundSource.company_id)
                             .where(CabFoundSource.task_id == t.id, CabFoundSource.agent_id == a.id).order_by(desc(CabFoundSource.id)).limit(200))).all()
    return {"items": [{"id": f.id, "phone": f.phone, "company": comp, "added_at": f.added_at} for f, comp in rows]}
