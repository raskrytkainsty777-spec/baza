"""Интеграции клиента: куда уходят купленные контакты. Google Таблицы, внешний коннектор;
Bitrix24 и AmoCRM — следующим шагом. Каждую можно проверить, отправить тестовый лид,
выключить, удалить.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import CabClient, CabIntegration, CabOutbox, LgSetting
from ..services import google_sheets as gs
from ..workers.cab_deliver import DEFAULT_COLUMNS, FIELDS, FIELD_KEYS, FIELD_LABELS, deliver_connector, deliver_gsheets, test_payload
from ..workers.common import utcnow
from .cab_auth import require_client

router = APIRouter(prefix="/api/cab/integrations", tags=["cab-integrations"])

KINDS = ("gsheets", "connector", "bitrix", "amo")


class IntegrationIn(BaseModel):
    kind: str
    config: dict = {}
    enabled: bool = True


class CheckIn(BaseModel):
    kind: str
    config: dict = {}


def _dto(i: CabIntegration, stats: dict | None = None) -> dict:
    cfg = dict(i.config or {})
    if "secret" in cfg and cfg["secret"]:
        cfg["secret_set"], cfg["secret"] = True, ""
    return {"id": i.id, "kind": i.kind, "config": cfg, "enabled": i.enabled, "status": i.status, "last_error": i.last_error,
            "last_test_at": i.last_test_at, "created_at": i.created_at, **(stats or {})}


@router.get("/fields")
async def fields(c: CabClient = Depends(require_client)):
    return {"fields": [{"key": k, "label": l} for k, l in FIELDS], "default": DEFAULT_COLUMNS}


@router.get("/google-account")
async def google_account(c: CabClient = Depends(require_client), db: AsyncSession = Depends(get_db)):
    name = (await db.execute(select(LgSetting.value).where(LgSetting.key == "google_sa_name"))).scalar() or ""
    try:
        info = await gs.sa_info(db)
    except gs.GSError:
        return {"email": None, "name": name, "error": "Google-аккаунт у нас пока не настроен — напишите администратору"}
    return {"email": info["client_email"] if info else None, "name": name}


@router.get("")
async def list_integrations(c: CabClient = Depends(require_client), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(CabIntegration).where(CabIntegration.client_id == c.id).order_by(CabIntegration.id))).scalars().all()
    stats = {}
    for iid, state, n in (await db.execute(select(CabOutbox.integration_id, CabOutbox.state, func.count())
                                           .where(CabOutbox.client_id == c.id).group_by(CabOutbox.integration_id, CabOutbox.state))).all():
        stats.setdefault(iid, {})[state] = n
    return {"items": [_dto(i, {"sent": stats.get(i.id, {}).get("sent", 0), "pending": stats.get(i.id, {}).get("pending", 0) + stats.get(i.id, {}).get("failed", 0),
                               "dead": stats.get(i.id, {}).get("dead", 0)}) for i in rows]}


def _clean(kind: str, config: dict) -> dict:
    if kind == "gsheets":
        sid = gs.spreadsheet_id(config.get("url") or config.get("spreadsheet_id") or "")
        cols = [k for k in (config.get("columns") or DEFAULT_COLUMNS) if k in FIELD_KEYS] or DEFAULT_COLUMNS
        return {"url": config.get("url", ""), "spreadsheet_id": sid, "sheet": (config.get("sheet") or "").strip(),
                "columns": cols, "header": bool(config.get("header", True)), "skip_repeats": bool(config.get("skip_repeats", True))}
    if kind == "connector":
        url = (config.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            raise HTTPException(400, "URL коннектора должен начинаться с http:// или https://")
        return {"url": url, "method": "GET" if (config.get("method") or "").upper() == "GET" else "POST",
                "secret": (config.get("secret") or "").strip(), "skip_repeats": bool(config.get("skip_repeats", True))}
    if kind in ("bitrix", "amo"):
        raise HTTPException(400, "Bitrix24 и AmoCRM подключим следующим шагом")
    raise HTTPException(400, f"kind: {' | '.join(KINDS)}")


@router.post("/check")
async def check(body: CheckIn, c: CabClient = Depends(require_client), db: AsyncSession = Depends(get_db)):
    """Проверить доступ без сохранения: для таблицы — название, листы, первая строка."""
    if body.kind == "gsheets":
        try:
            info = await gs.sa_info(db)
            if not info:
                raise gs.GSError("У нас не задан ключ сервисного аккаунта Google — напишите администратору")
            sid = gs.spreadsheet_id(body.config.get("url") or "")
            d = await gs.spreadsheet_info(info, sid)
        except gs.GSError as e:
            raise HTTPException(400, str(e))
        return {"ok": True, "spreadsheet_id": sid, **d}
    if body.kind == "connector":
        cfg = _clean("connector", body.config)
        fake = CabIntegration(client_id=c.id, kind="connector", config=cfg)
        try:
            await deliver_connector(fake, test_payload())
        except Exception as e:   # noqa: BLE001
            raise HTTPException(400, f"Коннектор не принял тестовый лид: {e}")
        return {"ok": True}
    raise HTTPException(400, "Проверка доступна для gsheets и connector")


@router.post("", status_code=201)
async def create(body: IntegrationIn, c: CabClient = Depends(require_client), db: AsyncSession = Depends(get_db)):
    cfg = _clean(body.kind, body.config)
    existing = (await db.execute(select(CabIntegration).where(CabIntegration.client_id == c.id, CabIntegration.kind == body.kind))).scalar_one_or_none()
    if existing:
        if body.kind == "connector" and not cfg.get("secret") and (existing.config or {}).get("secret"):
            cfg["secret"] = existing.config["secret"]
        existing.config, existing.enabled, existing.status, existing.last_error = cfg, body.enabled, "new", None
        i = existing
    else:
        i = CabIntegration(client_id=c.id, kind=body.kind, config=cfg, enabled=body.enabled)
        db.add(i)
    await db.flush()
    if body.kind == "gsheets" and cfg.get("header"):
        try:
            info = await gs.sa_info(db)
            d = await gs.spreadsheet_info(info, cfg["spreadsheet_id"])
            if not d.get("header"):
                await gs.write_header(info, cfg["spreadsheet_id"], cfg["sheet"], [FIELD_LABELS[k] for k in cfg["columns"]])
        except gs.GSError as e:
            i.status, i.last_error = "error", str(e)
    await db.commit()
    return _dto(i)


@router.post("/{integration_id}/test")
async def test(integration_id: int, c: CabClient = Depends(require_client), db: AsyncSession = Depends(get_db)):
    i = await db.get(CabIntegration, integration_id)
    if not i or i.client_id != c.id:
        raise HTTPException(404, "Интеграция не найдена")
    p = test_payload()
    try:
        if i.kind == "gsheets":
            await deliver_gsheets(db, i, [p])
        elif i.kind == "connector":
            await deliver_connector(i, p)
        else:
            raise HTTPException(400, "Тест для этой интеграции пока недоступен")
        i.status, i.last_error, i.last_test_at = "ok", None, utcnow()
    except (gs.GSError, RuntimeError) as e:
        i.status, i.last_error, i.last_test_at = "error", str(e)[:400], utcnow()
        await db.commit()
        raise HTTPException(400, str(e))
    await db.commit()
    return {"ok": True, "sent": p}


@router.patch("/{integration_id}")
async def toggle(integration_id: int, enabled: bool, c: CabClient = Depends(require_client), db: AsyncSession = Depends(get_db)):
    i = await db.get(CabIntegration, integration_id)
    if not i or i.client_id != c.id:
        raise HTTPException(404, "Интеграция не найдена")
    i.enabled = enabled
    await db.commit()
    return _dto(i)


@router.delete("/{integration_id}", status_code=204)
async def delete(integration_id: int, c: CabClient = Depends(require_client), db: AsyncSession = Depends(get_db)):
    i = await db.get(CabIntegration, integration_id)
    if not i or i.client_id != c.id:
        raise HTTPException(404, "Интеграция не найдена")
    rows = (await db.execute(select(CabOutbox).where(CabOutbox.integration_id == i.id))).scalars().all()
    for r in rows:
        await db.delete(r)
    await db.delete(i)
    await db.commit()
