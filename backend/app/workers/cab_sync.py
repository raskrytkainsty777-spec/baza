"""Синхронизация кабинета с Leads Factory.

Раз в проход по каждому активному клиенту:
  1. новые и изменённые источники → в LF (добавить, привязать id, теги-поставщики, лимиты, включение, регионы);
  2. раз в минуту — баланс: остаток ₽ и цена заявки → баланс в контактах;
  3. раз в три минуты — купленные номера из «Заявок» → cab_contacts + счётчики по источникам;
  4. чёрный список → LF;
  5. проект без статуса закупки включается сам, когда есть источники и баланс.
"""
import asyncio
import logging
from collections import defaultdict
from datetime import timedelta

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal
from ..models import CabBlacklist, CabClient, CabContact, CabSource
from ..services.leadsfactory.client import LF, LFError, MSK, SUPPLIERS, get_token, parse_dt
from .common import heartbeat, log_event, utcnow

log = logging.getLogger("cab_sync")

POLL = 15
BALANCE_EVERY = timedelta(seconds=60)
CONTACTS_EVERY = timedelta(seconds=180)
TAG_CONCURRENCY = 5


async def run():
    while True:
        try:
            async with SessionLocal() as db:
                token = await get_token(db)
                clients = (await db.execute(select(CabClient).where(
                    CabClient.is_active.is_(True), CabClient.lf_crm_id.isnot(None)))).scalars().all()
                if clients and token:
                    lf = LF(token)
                    for c in clients:
                        await _client_pass(db, lf, c)
                await heartbeat(db, "cab_sync")
        except Exception:
            log.exception("проход не удался")
        await asyncio.sleep(POLL)


async def _client_pass(db: AsyncSession, lf: LF, c: CabClient) -> None:
    now = utcnow()
    try:
        await push_sources(db, lf, c)
        await push_blacklist(db, lf, c)
        if not c.balance_synced_at or now - c.balance_synced_at >= BALANCE_EVERY:
            await sync_balance(db, lf, c)
        if not c.contacts_synced_at or now - c.contacts_synced_at >= CONTACTS_EVERY:
            await sync_contacts(db, lf, c)
        await maybe_activate(db, lf, c)
        c.lf_error = None
    except LFError as e:
        c.lf_error = str(e)[:500]
        log.warning("клиент %s: %s", c.login, e)
    except Exception as e:   # noqa: BLE001
        c.lf_error = f"{type(e).__name__}: {e}"[:500]
        log.exception("клиент %s", c.login)
    await db.commit()


# ── источники → LF ───────────────────────────────────────────────────────────

async def push_sources(db: AsyncSession, lf: LF, c: CabClient) -> None:
    new = (await db.execute(select(CabSource).where(CabSource.client_id == c.id, CabSource.lf_source_id.is_(None)))).scalars().all()
    dirty = (await db.execute(select(CabSource).where(CabSource.client_id == c.id, CabSource.lf_dirty.is_(True)))).scalars().all()
    if not new and not dirty:
        return
    from ..models import CabCompany
    names = dict((await db.execute(select(CabCompany.id, CabCompany.name).where(CabCompany.client_id == c.id))).all())

    if new:
        groups: dict[str, list[CabSource]] = defaultdict(list)
        for s in new:
            groups[names.get(s.company_id) or ""].append(s)
        for label, group in groups.items():
            for i in range(0, len(group), 100):
                chunk = group[i:i + 100]
                try:
                    await lf.sources_add(c.lf_crm_id, [s.phone for s in chunk], label=label or None)
                except LFError as e:
                    for s in chunk:
                        s.lf_error = str(e)[:300]
                    await db.commit()
                    raise
        await asyncio.sleep(2)

    lf_sources = {s["phone"]: s for s in await lf.sources_all(c.lf_crm_id)}
    tags = await lf.tags_all(c.lf_crm_id)
    by_phone: dict[str, dict[str, dict]] = defaultdict(dict)
    for t in tags:
        parts = (t.get("name") or "").split("_")
        if len(parts) >= 2:
            by_phone[parts[1]][t["type"]] = t

    todo = {s.id: s for s in new + dirty}
    calls = []
    will_on, will_off = [], []
    for s in todo.values():
        lfs = lf_sources.get(s.phone)
        if not lfs:
            s.lf_error = "не появился в списке источников LF"
            continue
        s.lf_source_id = lfs["id"]
        s.lf_will_work = lfs.get("will_work")
        s.lf_sebes_14 = lfs.get("sebes_14_days")
        s.lf_success_14 = lfs.get("success_14_days")
        s.lf_tags = {typ: t["id"] for typ, t in by_phone.get(s.phone, {}).items()}
        for typ, t in by_phone.get(s.phone, {}).items():
            want_norm = typ in (s.suppliers or [])
            want_limit = s.limit
            fields = {}
            if bool(t.get("norm_work")) != want_norm:
                fields["norm_work"] = want_norm
            if want_norm and int(t.get("limit") or 0) != want_limit:
                fields["limit"] = want_limit
            if fields:
                calls.append(lf.tag_update(t["id"], **fields))
        want_work = bool(s.enabled_by_user and s.enabled_by_schedule)
        if lfs.get("will_work") != want_work:
            (will_on if want_work else will_off).append(s.lf_source_id)
        if s.geo_ids:
            calls.append(lf.sources_settings([s.lf_source_id], geo_ids=[int(g) for g in s.geo_ids]))
        s.lf_dirty = False
        s.lf_error = None
    if will_on:
        await lf.sources_will_work(will_on, True)
    if will_off:
        await lf.sources_will_work(will_off, False)
    if calls:
        sem = asyncio.Semaphore(TAG_CONCURRENCY)

        async def one(coro):
            async with sem:
                return await coro
        results = await asyncio.gather(*(one(x) for x in calls), return_exceptions=True)
        errs = [r for r in results if isinstance(r, Exception)]
        if errs:
            log.warning("клиент %s: %d из %d вызовов LF по тегам не прошли: %s", c.login, len(errs), len(calls), errs[0])
    for s in todo.values():
        if s.lf_will_work is not None and s.lf_source_id:
            s.lf_will_work = bool(s.enabled_by_user and s.enabled_by_schedule)
    await db.commit()
    log.info("клиент %s: источников в LF новых %d, обновлено %d, вызовов по тегам %d", c.login, len(new), len(dirty), len(calls))


async def push_blacklist(db: AsyncSession, lf: LF, c: CabClient) -> None:
    rows = (await db.execute(select(CabBlacklist).where(CabBlacklist.client_id == c.id, CabBlacklist.sent_at.is_(None)))).scalars().all()
    if not rows:
        return
    await lf.blacklist_add(c.lf_crm_id, [b.phone for b in rows])
    for b in rows:
        b.sent_at = utcnow()
    await db.commit()


# ── баланс ───────────────────────────────────────────────────────────────────

async def sync_balance(db: AsyncSession, lf: LF, c: CabClient) -> None:
    fin = await lf.finance(c.lf_crm_id)
    pay = await lf.payment_get(c.lf_crm_id)
    prj = await lf.project(c.lf_crm_id)
    balance = float((fin.get("totals") or {}).get("client_balance") or 0)
    cost = float(pay.get("answer_cost") or 0)
    c.lf_balance_rub = balance
    c.lf_answer_cost = cost or c.lf_answer_cost
    c.balance_contacts = int(balance // cost) if cost > 0 else None
    c.lf_status = prj.get("status")
    c.balance_synced_at = utcnow()


async def maybe_activate(db: AsyncSession, lf: LF, c: CabClient) -> None:
    """Закупка включается сама, когда у клиента появились источники в LF и баланс больше нуля."""
    if c.lf_status not in (None, "new"):
        return
    if not c.balance_contacts or c.balance_contacts <= 0:
        return
    has = (await db.execute(select(func.count()).where(CabSource.client_id == c.id, CabSource.lf_source_id.isnot(None)))).scalar() or 0
    if not has:
        return
    await lf.set_status(c.lf_crm_id, "active")
    c.lf_status = "active"
    await log_event(db, "cab.activated", f"Клиент {c.login}: закупка в LF включена (источников {has}, баланс {c.balance_contacts})",
                    entity="cab_client", entity_id=c.id)


# ── контакты ─────────────────────────────────────────────────────────────────

async def sync_contacts(db: AsyncSession, lf: LF, c: CabClient) -> None:
    since = None
    if c.contacts_synced_at:
        since = (c.contacts_synced_at.astimezone(MSK) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    sources = {s.phone: s for s in (await db.execute(select(CabSource).where(CabSource.client_id == c.id))).scalars().all()}
    inserted, page = 0, 1
    while True:
        d = await lf.answers(c.lf_crm_id, page=page, limit=200, date_from=since)
        items = d.get("items") or []
        rows = []
        for a in items:
            tag = a.get("site") or ""
            parts = tag.split("_")
            supplier = parts[0] if parts and parts[0] in SUPPLIERS else None
            src = sources.get(parts[1]) if len(parts) > 1 else None
            rows.append({
                "client_id": c.id, "lf_answer_id": int(a["id"]), "phone": str(a.get("mobile_tel") or ""),
                "operator": (a.get("mobile_operator") or None), "region": (a.get("mobile_operator_region") or None),
                "source_tag": tag[:80] or None, "supplier": supplier, "source_id": src.id if src else None,
                "company_id": src.company_id if src else None, "lf_status": a.get("status"),
                "bought_at": parse_dt(a.get("date")),
            })
        if rows:
            res = await db.execute(insert(CabContact).values(rows).on_conflict_do_nothing(index_elements=["lf_answer_id"]).returning(CabContact.id))
            inserted += len(res.scalars().all())
        if len(items) < 200:
            break
        page += 1
        await asyncio.sleep(0.3)
    c.contacts_synced_at = utcnow()
    if inserted:
        await db.execute(text("""
            UPDATE cab_sources s SET
              contacts_total = agg.total, contacts_today = agg.today, repeats_total = agg.repeats, last_contact_at = agg.last
            FROM (SELECT source_id, COUNT(*) AS total,
                         COUNT(*) FILTER (WHERE (bought_at AT TIME ZONE 'Europe/Moscow')::date = (now() AT TIME ZONE 'Europe/Moscow')::date) AS today,
                         COUNT(*) FILTER (WHERE lf_status = 'repeat') AS repeats, MAX(bought_at) AS last
                  FROM cab_contacts WHERE client_id = :cid AND source_id IS NOT NULL GROUP BY source_id) agg
            WHERE s.id = agg.source_id
        """), {"cid": c.id})
        await log_event(db, "cab.contacts", f"Клиент {c.login}: из LF пришло {inserted} новых контактов", entity="cab_client", entity_id=c.id)
        try:
            from .cab_deliver import enqueue_new_contacts
            await enqueue_new_contacts(db, c)
        except ImportError:
            pass
    await db.commit()
    # «сегодня» у источников надо обнулять и без новых контактов — раз в проход после полуночи
    await db.execute(text("""
        UPDATE cab_sources s SET contacts_today = COALESCE(agg.today, 0)
        FROM (SELECT id, (SELECT COUNT(*) FROM cab_contacts x WHERE x.source_id = cab_sources.id
                          AND (x.bought_at AT TIME ZONE 'Europe/Moscow')::date = (now() AT TIME ZONE 'Europe/Moscow')::date) AS today
              FROM cab_sources WHERE client_id = :cid) agg
        WHERE s.id = agg.id AND s.contacts_today <> COALESCE(agg.today, 0)
    """), {"cid": c.id})
    await db.commit()
