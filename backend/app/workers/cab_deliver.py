"""Доставка купленных контактов в интеграции клиента: Google Таблицы, внешний коннектор
(GET/POST на URL), позже Bitrix24 и AmoCRM. Прокладка cab_outbox: одна строка на контакт и
интеграцию, повторы с растущей паузой, после десяти неудач — «мёртвая» и интеграция в ошибке.
"""
import asyncio
import logging
from datetime import timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal
from ..models import CabClient, CabCompany, CabContact, CabIntegration, CabOutbox
from ..services import amocrm, bitrix
from ..services import google_sheets as gs
from ..services.leadsfactory.client import MSK, SUPPLIERS
from .common import heartbeat, utcnow

log = logging.getLogger("cab_deliver")

POLL = 10
MAX_ATTEMPTS = 10
FIELDS = [
    ("operator", "Оператор"), ("source_phone", "Источник"), ("phone", "Контакт"), ("bought_at", "Дата выгрузки"),
    ("supplier_label", "Поставщик"), ("company", "Компания"), ("region", "Регион"), ("lf_status", "Статус LF"),
]
FIELD_KEYS = [k for k, _ in FIELDS]
FIELD_LABELS = dict(FIELDS)
DEFAULT_COLUMNS = ["operator", "source_phone", "phone", "bought_at"]


def contact_payload(x: CabContact, company: str | None) -> dict:
    src_phone = (x.source_tag or "").split("_")[1] if x.source_tag and "_" in x.source_tag else ""
    return {
        "phone": x.phone, "operator": x.operator or "", "region": x.region or "",
        "supplier": x.supplier or "", "supplier_label": SUPPLIERS.get(x.supplier or "", x.supplier or ""),
        "source_phone": src_phone, "company": company or "",
        "bought_at": x.bought_at.astimezone(MSK).strftime("%d.%m.%Y %H:%M") if x.bought_at else "",
        "lf_status": "повтор" if x.lf_status == "repeat" else "новая",
    }


def test_payload() -> dict:
    return {"phone": "79999999999", "operator": "тест", "region": "тест", "supplier": "B222", "supplier_label": "тест",
            "source_phone": "тест источник", "company": "тест", "bought_at": utcnow().astimezone(MSK).strftime("%d.%m.%Y %H:%M"),
            "lf_status": "новая"}


async def enqueue_new_contacts(db: AsyncSession, c: CabClient) -> int:
    """Новые контакты клиента → строки outbox по каждой включённой интеграции."""
    integrations = (await db.execute(select(CabIntegration).where(
        CabIntegration.client_id == c.id, CabIntegration.enabled.is_(True),
        CabIntegration.kind.in_(["gsheets", "connector", "bitrix", "amo"])))).scalars().all()
    if not integrations:
        return 0
    total = 0
    for integ in integrations:
        since = max(integ.created_at, utcnow() - timedelta(days=2))   # интеграция получает только то, что пришло после её подключения
        q = select(CabContact).where(CabContact.client_id == c.id, CabContact.pulled_at >= since)
        if (integ.config or {}).get("skip_repeats", True):
            q = q.where(CabContact.lf_status != "repeat")
        contacts = (await db.execute(q.order_by(CabContact.id).limit(5000))).scalars().all()
        if not contacts:
            continue
        rows = [{"client_id": c.id, "integration_id": integ.id, "contact_id": x.id, "payload": {}} for x in contacts]
        res = await db.execute(insert(CabOutbox).values(rows).on_conflict_do_nothing(
            index_elements=["integration_id", "contact_id"]).returning(CabOutbox.id))
        total += len(res.scalars().all())
    return total


async def run():
    while True:
        try:
            async with SessionLocal() as db:
                await _pass(db)
                await heartbeat(db, "cab_deliver")
        except Exception:
            log.exception("проход не удался")
        await asyncio.sleep(POLL)


async def _pass(db: AsyncSession) -> None:
    rows = (await db.execute(select(CabOutbox).where(
        CabOutbox.state.in_(["pending", "failed"]), CabOutbox.next_try_at <= utcnow()).order_by(CabOutbox.id).limit(500))).scalars().all()
    if not rows:
        return
    by_integ: dict[int, list[CabOutbox]] = {}
    for r in rows:
        by_integ.setdefault(r.integration_id, []).append(r)
    for integ_id, items in by_integ.items():
        integ = await db.get(CabIntegration, integ_id)
        if not integ or not integ.enabled:
            for r in items:
                r.state = "dead"
                r.last_error = "интеграция выключена"
            continue
        contacts = {x.id: x for x in (await db.execute(select(CabContact).where(
            CabContact.id.in_([r.contact_id for r in items if r.contact_id])))).scalars().all()}
        companies = dict((await db.execute(select(CabCompany.id, CabCompany.name).where(CabCompany.client_id == integ.client_id))).all())
        payloads = {}
        for r in items:
            x = contacts.get(r.contact_id)
            payloads[r.id] = r.payload if (r.payload and not r.contact_id) else (contact_payload(x, companies.get(x.company_id)) if x else None)
        try:
            if integ.kind == "gsheets":
                ok_rows = [r for r in items if payloads.get(r.id)]
                await deliver_gsheets(db, integ, [payloads[r.id] for r in ok_rows])
                for r in ok_rows:
                    _sent(r)
                for r in items:
                    if not payloads.get(r.id):
                        r.state, r.last_error = "dead", "контакт не найден"
            else:
                item_error = None
                for r in items:
                    p = payloads.get(r.id)
                    if not p:
                        r.state, r.last_error = "dead", "контакт не найден"
                        continue
                    try:
                        _sent(r, await deliver_one(integ, p))
                    except Exception as e:   # noqa: BLE001
                        item_error = str(e)[:400]
                        _failed(r, item_error)
                if item_error:
                    raise RuntimeError(item_error)   # интеграцию пометить ошибкой, строки уже разобраны
            integ.status, integ.last_error = "ok", None
        except Exception as e:   # noqa: BLE001
            msg = str(e)[:400]
            for r in items:
                if r.state in ("pending",):   # gsheets: пачка целиком; connector: строки уже помечены поштучно
                    _failed(r, msg)
            integ.status, integ.last_error = "error", msg
            log.warning("интеграция %s #%s: %s", integ.kind, integ.id, msg)
        await db.commit()


def _sent(r: CabOutbox, note: str | None = None) -> None:
    r.state, r.sent_at, r.last_error = "sent", utcnow(), (note[:400] if note else None)


async def deliver_one(integ: CabIntegration, p: dict) -> str | None:
    """Один контакт в поштучную интеграцию. Возвращает заметку (что создали / почему пропустили)."""
    if integ.kind == "connector":
        await deliver_connector(integ, p)
        return None
    if integ.kind == "bitrix":
        return await bitrix.push(integ.config or {}, p)
    if integ.kind == "amo":
        return await amocrm.push(integ.config or {}, p)
    raise RuntimeError(f"интеграция {integ.kind} не поддерживается")


def _failed(r: CabOutbox, msg: str) -> None:
    r.attempts = (r.attempts or 0) + 1
    r.last_error = msg[:400]
    if r.attempts >= MAX_ATTEMPTS:
        r.state = "dead"
    else:
        r.state = "failed"
        r.next_try_at = utcnow() + timedelta(seconds=min(30 * 2 ** r.attempts, 3600))


# ── каналы ───────────────────────────────────────────────────────────────────

def gsheet_row(cfg: dict, p: dict) -> list:
    cols = cfg.get("columns") or DEFAULT_COLUMNS
    return [p.get(k, "") for k in cols]


async def deliver_gsheets(db: AsyncSession, integ: CabIntegration, payloads: list[dict]) -> int:
    info = await gs.sa_info(db)
    if not info:
        raise gs.GSError("У нас не задан ключ сервисного аккаунта Google (ГЦК → Google)")
    cfg = integ.config or {}
    sid, sheet = cfg.get("spreadsheet_id"), cfg.get("sheet")
    if not sid or not sheet:
        raise gs.GSError("Интеграция не настроена: нет таблицы или листа")
    rows = [gsheet_row(cfg, p) for p in payloads]
    n = 0
    for i in range(0, len(rows), 200):
        n += await gs.append_rows(info, sid, sheet, rows[i:i + 200])
    return n


async def deliver_connector(integ: CabIntegration, p: dict) -> None:
    cfg = integ.config or {}
    url, method = (cfg.get("url") or "").strip(), (cfg.get("method") or "POST").upper()
    if not url:
        raise RuntimeError("не задан URL коннектора")
    headers = {}
    if cfg.get("secret"):
        headers["X-Baza-Secret"] = cfg["secret"]
    async with httpx.AsyncClient(timeout=20) as cl:
        if method == "GET":
            r = await cl.get(url, params=p, headers=headers)
        else:
            r = await cl.post(url, json=p, headers=headers)
    if r.status_code >= 400:
        raise RuntimeError(f"коннектор ответил {r.status_code}: {r.text[:150]}")
