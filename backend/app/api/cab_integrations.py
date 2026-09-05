"""Интеграции клиента: куда уходят купленные контакты. Google Таблицы, внешний коннектор;
Bitrix24 и AmoCRM — следующим шагом. Каждую можно проверить, отправить тестовый лид,
выключить, удалить.
"""
import secrets
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete as sa_delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import CabClient, CabIntegration, CabOutbox, LgSetting
from ..services import amocrm, bitrix
from ..services import google_sheets as gs
from ..services import telegram_bot as tg
from ..workers.cab_deliver import DEFAULT_COLUMNS, FIELDS, FIELD_KEYS, FIELD_LABELS, deliver_connector, deliver_gsheets, deliver_one, test_payload
from ..workers.cab_notify import summary_text
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
    if cfg.get("secret"):
        cfg["secret_set"], cfg["secret"] = True, ""
    if cfg.get("webhook"):
        cfg["webhook_set"], cfg["webhook_host"], cfg["webhook"] = True, urlparse(cfg["webhook"]).netloc, ""
    if cfg.get("token"):
        cfg["token_set"], cfg["token"] = True, ""
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
    if kind == "bitrix":
        wh = (config.get("webhook") or "").strip()
        if wh:
            try:
                wh = bitrix.norm_webhook(wh)
            except bitrix.BitrixError as e:
                raise HTTPException(400, str(e))
        entity = "deal" if config.get("entity") == "deal" else "lead"
        return {"webhook": wh, "entity": entity, "responsible_id": _int(config.get("responsible_id")),
                "status_id": str(config.get("status_id") or "") if entity == "lead" else "",
                "category_id": _int(config.get("category_id")) or 0, "stage_id": str(config.get("stage_id") or "") if entity == "deal" else "",
                "phone_type": config.get("phone_type") or "MOBILE", "source_id": str(config.get("source_id") or ""),
                "dedupe": bool(config.get("dedupe", True)), "skip_repeats": bool(config.get("skip_repeats", True))}
    if kind == "amo":
        sub = ""
        if config.get("subdomain"):
            try:
                sub = amocrm.norm_subdomain(config["subdomain"])
            except amocrm.AmoError as e:
                raise HTTPException(400, str(e))
        return {"subdomain": sub, "token": (config.get("token") or "").strip(), "pipeline_id": _int(config.get("pipeline_id")),
                "status_id": _int(config.get("status_id")), "responsible_id": _int(config.get("responsible_id")),
                "phone_type": config.get("phone_type") or "MOB", "tag": (config.get("tag") or "ГЦК").strip()[:50],
                "dedupe": bool(config.get("dedupe", True)), "skip_repeats": bool(config.get("skip_repeats", True))}
    raise HTTPException(400, f"kind: {' | '.join(KINDS)}")


def _int(v) -> int | None:
    try:
        return int(v) if v not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        return None


SECRET_KEYS = {"connector": ["secret"], "bitrix": ["webhook"], "amo": ["token", "subdomain"]}


async def _existing(db: AsyncSession, c: CabClient, kind: str) -> CabIntegration | None:
    return (await db.execute(select(CabIntegration).where(CabIntegration.client_id == c.id, CabIntegration.kind == kind))).scalar_one_or_none()


async def _fill_secrets(db: AsyncSession, c: CabClient, kind: str, cfg: dict) -> dict:
    """Секрет в форме пустой — берём сохранённый (форма секрет обратно не получает)."""
    ex = await _existing(db, c, kind)
    if ex:
        for k in SECRET_KEYS.get(kind, []):
            if not cfg.get(k) and (ex.config or {}).get(k):
                cfg[k] = ex.config[k]
    return cfg


async def _crm_check(kind: str, cfg: dict) -> dict:
    try:
        if kind == "bitrix":
            if not cfg.get("webhook"):
                raise bitrix.BitrixError("Вставьте адрес входящего вебхука")
            bx = bitrix.Bitrix(cfg["webhook"])
            await bx.check()
            return {"ok": True, **(await bx.refs())}
        if not cfg.get("subdomain") or not cfg.get("token"):
            raise amocrm.AmoError("Нужны поддомен и долгосрочный токен")
        amo = amocrm.Amo(cfg["subdomain"], cfg["token"])
        acc = await amo.check()
        return {"ok": True, **acc, **(await amo.refs())}
    except (bitrix.BitrixError, amocrm.AmoError) as e:
        raise HTTPException(400, str(e))


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
    if body.kind in ("bitrix", "amo"):
        cfg = await _fill_secrets(db, c, body.kind, dict(body.config))
        return await _crm_check(body.kind, cfg)
    raise HTTPException(400, "Проверка доступна для gsheets, connector, bitrix, amo")


@router.post("", status_code=201)
async def create(body: IntegrationIn, c: CabClient = Depends(require_client), db: AsyncSession = Depends(get_db)):
    cfg = await _fill_secrets(db, c, body.kind, _clean(body.kind, body.config))
    if body.kind == "bitrix" and not cfg.get("webhook"):
        raise HTTPException(400, "Вставьте адрес входящего вебхука")
    if body.kind == "amo" and (not cfg.get("subdomain") or not cfg.get("token")):
        raise HTTPException(400, "Нужны поддомен и долгосрочный токен")
    existing = await _existing(db, c, body.kind)
    if existing:
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
    note = None
    try:
        if i.kind == "gsheets":
            await deliver_gsheets(db, i, [p])
        else:
            note = await deliver_one(i, p)
        i.status, i.last_error, i.last_test_at = "ok", None, utcnow()
    except Exception as e:   # noqa: BLE001
        i.status, i.last_error, i.last_test_at = "error", str(e)[:400], utcnow()
        await db.commit()
        raise HTTPException(400, str(e))
    await db.commit()
    return {"ok": True, "sent": p, "note": note}


# ── Telegram ──────────────────────────────────────────────────────────────────────

@router.get("/telegram")
async def telegram_info(c: CabClient = Depends(require_client), db: AsyncSession = Depends(get_db)):
    token = await tg.bot_token(db)
    username = await tg.bot_username(token) if token else None
    if not c.tg_connect_code:
        c.tg_connect_code = secrets.token_urlsafe(9)
        await db.commit()
    return {"configured": bool(token), "bot": username, "connected": bool(c.tg_chat_id), "code": c.tg_connect_code,
            "link": f"https://t.me/{username}?start={c.tg_connect_code}" if username else None}


@router.post("/telegram/disconnect")
async def telegram_disconnect(c: CabClient = Depends(require_client), db: AsyncSession = Depends(get_db)):
    c.tg_chat_id = None
    c.tg_connect_code = secrets.token_urlsafe(9)
    await db.commit()
    return {"ok": True}


@router.post("/telegram/test")
async def telegram_test(c: CabClient = Depends(require_client), db: AsyncSession = Depends(get_db)):
    token = await tg.bot_token(db)
    if not token or not c.tg_chat_id:
        raise HTTPException(400, "Бот не подключён")
    try:
        await tg.send(token, c.tg_chat_id, await summary_text(db, c))
    except Exception as e:   # noqa: BLE001
        raise HTTPException(400, f"Не отправилось: {e}")
    return {"ok": True}


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
    await db.execute(sa_delete(CabOutbox).where(CabOutbox.integration_id == i.id))   # сначала очередь: FK, ORM порядок не гарантирует
    await db.flush()
    await db.delete(i)
    await db.commit()
