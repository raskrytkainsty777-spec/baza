"""ГЦК (генерация целевых клиентов) — админская вкладка: клиенты = проекты Leads Factory.

Создание клиента заводит проект в LF и проставляет наши стандартные настройки. Баланс
начислить через API LF нельзя — вносится руками в их ЛК, а сюда подтягивается воркером.
"""
import secrets
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import CabClient, CabContact, CabSource
from ..services.leadsfactory.client import LFError, PHONE_SUPPLIERS, lf_for
from ..workers.common import as_int, log_event, settings_all, utcnow
from .cab_auth import create_session, hash_password
from .deps import require_token

router = APIRouter(prefix="/api/gck", tags=["gck"], dependencies=[Depends(require_token)])


class ClientCreate(BaseModel):
    login: str
    password: str
    name: str
    lf_crm_id: int | None = None          # привязать существующий проект LF вместо создания
    answer_cost: Decimal | None = None    # цена заявки в LF; пусто — из настроек ГЦК
    limit_default: int | None = None


class ClientPatch(BaseModel):
    name: str | None = None
    password: str | None = None
    is_active: bool | None = None
    lf_crm_id: int | None = None


def _dto(c: CabClient, extra: dict | None = None) -> dict:
    d = {
        "id": c.id, "login": c.login, "name": c.name, "is_active": c.is_active,
        "lf_crm_id": c.lf_crm_id, "lf_status": c.lf_status,
        "lf_answer_cost": float(c.lf_answer_cost) if c.lf_answer_cost is not None else None,
        "lf_balance_rub": float(c.lf_balance_rub) if c.lf_balance_rub is not None else None,
        "balance_contacts": c.balance_contacts, "balance_synced_at": c.balance_synced_at,
        "contacts_synced_at": c.contacts_synced_at, "lf_error": c.lf_error,
        "limit_default": c.limit_default, "suppliers_default": c.suppliers_default,
        "hook_token": c.hook_token, "tg_connected": bool(c.tg_chat_id), "created_at": c.created_at,
    }
    if extra:
        d.update(extra)
    return d


async def _counts(db: AsyncSession) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for cid, n, on in (await db.execute(
            select(CabSource.client_id, func.count(), func.count().filter(CabSource.enabled_by_user & CabSource.enabled_by_schedule))
            .group_by(CabSource.client_id))).all():
        out.setdefault(cid, {}).update(sources=n, sources_on=on)
    today = utcnow().astimezone().date()
    for cid, total, today_n in (await db.execute(
            select(CabContact.client_id, func.count(),
                   func.count().filter(func.date(func.timezone("Europe/Moscow", CabContact.bought_at)) == func.current_date()))
            .group_by(CabContact.client_id))).all():
        out.setdefault(cid, {}).update(contacts_total=total, contacts_today=today_n)
    return out


@router.get("/clients")
async def list_clients(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(CabClient).order_by(CabClient.id))).scalars().all()
    counts = await _counts(db)
    return {"items": [_dto(c, {"sources": 0, "sources_on": 0, "contacts_total": 0, "contacts_today": 0,
                               **counts.get(c.id, {})}) for c in rows]}


@router.post("/clients", status_code=201)
async def create_client(body: ClientCreate, db: AsyncSession = Depends(get_db)):
    login = body.login.strip().lower()
    if not login or len(body.password) < 6:
        raise HTTPException(400, "Логин обязателен, пароль от 6 символов")
    if (await db.execute(select(CabClient).where(CabClient.login == login))).scalar_one_or_none():
        raise HTTPException(409, "Такой логин уже есть")
    values = await settings_all(db)
    answer_cost = body.answer_cost if body.answer_cost is not None else Decimal(values.get("gck_answer_cost") or "5")
    limit_default = body.limit_default or as_int(values, "gck_limit_default", 5)
    lf = await lf_for(db)
    crm_id = body.lf_crm_id
    steps: list[str] = []
    try:
        if not crm_id:
            crm_id = await lf.create_project(body.name.strip())
            steps.append(f"проект LF создан: {crm_id}")
        await lf.payment_update(crm_id, answer_cost=float(answer_cost), min_client_balance=0)
        steps.append(f"цена заявки {answer_cost} ₽, мин. остаток 0")
        await lf.vdl_project_patch(crm_id, default_limit=limit_default, max_limit=max(limit_default, 100), limit_autochange=False)
        await lf.auto_scripts_update(crm_id, limit_autochange=False, default_tags_limit=limit_default)
        steps.append(f"лимит по умолчанию {limit_default}, автоповышение выкл")
    except LFError as e:
        raise HTTPException(502, f"Leads Factory: {e}. Сделано: {'; '.join(steps) or 'ничего'}")
    c = CabClient(login=login, password_hash=hash_password(body.password), name=body.name.strip(),
                  lf_crm_id=crm_id, lf_answer_cost=answer_cost, limit_default=limit_default,
                  suppliers_default=list(PHONE_SUPPLIERS), weekdays=[True] * 7,
                  hook_token=secrets.token_urlsafe(24))
    db.add(c)
    await db.flush()
    await log_event(db, "gck.client_created", f"Клиент {login} → проект LF {crm_id}: " + "; ".join(steps),
                    entity="cab_client", entity_id=c.id)
    await db.commit()
    return _dto(c, {"steps": steps})


@router.patch("/clients/{client_id}")
async def patch_client(client_id: int, body: ClientPatch, db: AsyncSession = Depends(get_db)):
    c = await db.get(CabClient, client_id)
    if not c:
        raise HTTPException(404, "Клиент не найден")
    if body.name is not None:
        c.name = body.name.strip()
    if body.password:
        if len(body.password) < 6:
            raise HTTPException(400, "Пароль от 6 символов")
        c.password_hash = hash_password(body.password)
    if body.is_active is not None:
        c.is_active = body.is_active
    if body.lf_crm_id is not None:
        c.lf_crm_id = body.lf_crm_id
    await db.commit()
    return _dto(c)


@router.post("/clients/{client_id}/session")
async def impersonate(client_id: int, db: AsyncSession = Depends(get_db)):
    """Открыть кабинет клиента от его имени: выпускаем клиентскую сессию."""
    c = await db.get(CabClient, client_id)
    if not c:
        raise HTTPException(404, "Клиент не найден")
    token = await create_session(db, "client", c.id)
    await db.commit()
    return {"token": token}


@router.post("/clients/{client_id}/refresh")
async def refresh(client_id: int, db: AsyncSession = Depends(get_db)):
    """Подтянуть баланс и цену из LF прямо сейчас (воркер делает то же раз в минуту)."""
    from ..workers.cab_sync import sync_balance
    c = await db.get(CabClient, client_id)
    if not c or not c.lf_crm_id:
        raise HTTPException(404, "Клиент не найден или без проекта LF")
    try:
        await sync_balance(db, await lf_for(db), c)
    except LFError as e:
        raise HTTPException(502, str(e))
    await db.commit()
    return _dto(c)


@router.post("/clients/{client_id}/lf-status")
async def set_lf_status(client_id: int, status: str, db: AsyncSession = Depends(get_db)):
    if status not in ("active", "stop", "pause"):
        raise HTTPException(400, "status: active | stop | pause")
    c = await db.get(CabClient, client_id)
    if not c or not c.lf_crm_id:
        raise HTTPException(404, "Клиент не найден или без проекта LF")
    try:
        await (await lf_for(db)).set_status(c.lf_crm_id, status)
    except LFError as e:
        raise HTTPException(502, str(e))
    c.lf_status = status
    await log_event(db, "gck.lf_status", f"Клиент {c.login}: статус закупки LF → {status}", entity="cab_client", entity_id=c.id)
    await db.commit()
    return _dto(c)
