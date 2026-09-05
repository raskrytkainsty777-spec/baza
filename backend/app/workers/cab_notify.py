"""Telegram-бот клиентов: подключение по коду (/start <код>) и сводка в 19:00 МСК тому,
кто подключил бота. Один бот на всех клиентов, токен — в ГЦК. Без токена воркер просто ждёт.
"""
import asyncio
import logging
from datetime import datetime, time, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal
from ..models import CabClient, CabContact, CabSource, LgSetting
from ..services import telegram_bot as tg
from ..services.leadsfactory.client import MSK
from .common import heartbeat, log_event, utcnow

log = logging.getLogger("cab_notify")

SUMMARY_AT = time(19, 0)
HELP = ("Это бот кабинета закупки номеров. Чтобы подключить уведомления, откройте в кабинете "
        "«Настройки → Telegram» и нажмите «Подключить бота» — или пришлите сюда команду вида\n/start КОД\n\n"
        "Команды после подключения: /stats — сводка сейчас, /stop — отключить.")


async def run():
    offset = None
    while True:
        try:
            async with SessionLocal() as db:
                token = await tg.bot_token(db)
                await heartbeat(db, "cab_notify")
                if not token:
                    await asyncio.sleep(30)
                    continue
                if offset is None:
                    offset = int((await db.execute(select(LgSetting.value).where(LgSetting.key == "tg_cab_offset"))).scalar() or 0)
                offset = await _poll(db, token, offset)
                await _daily(db, token)
        except Exception:
            log.exception("проход не удался")
            await asyncio.sleep(10)


async def _poll(db: AsyncSession, token: str, offset: int) -> int:
    try:
        updates = await tg.api(token, "getUpdates", offset=offset, timeout=20, allowed_updates=["message"])
    except Exception as e:   # noqa: BLE001
        log.warning("getUpdates: %s", e)
        await asyncio.sleep(15)
        return offset
    for u in updates or []:
        offset = u["update_id"] + 1
        msg = u.get("message") or {}
        chat = str((msg.get("chat") or {}).get("id") or "")
        txt = (msg.get("text") or "").strip()
        if not chat or not txt:
            continue
        try:
            await _handle(db, token, chat, txt)
        except Exception as e:   # noqa: BLE001
            log.warning("сообщение %s от %s: %s", txt[:40], chat, e)
    if updates:
        await db.execute(insert(LgSetting).values(key="tg_cab_offset", value=str(offset))
                         .on_conflict_do_update(index_elements=["key"], set_={"value": str(offset)}))
        await db.commit()
    return offset


async def _handle(db: AsyncSession, token: str, chat: str, txt: str) -> None:
    parts = txt.split(maxsplit=1)
    cmd = parts[0].lower().split("@")[0]
    arg = parts[1].strip() if len(parts) > 1 else ""
    if cmd == "/start" and arg:
        c = (await db.execute(select(CabClient).where(CabClient.tg_connect_code == arg, CabClient.is_active.is_(True)))).scalar_one_or_none()
        if not c:
            await tg.send(token, chat, "Код не подошёл. Возьмите свежий код в кабинете: Настройки → Telegram.")
            return
        # один чат — один клиент: если этот чат был привязан к другому, отвязываем
        for other in (await db.execute(select(CabClient).where(CabClient.tg_chat_id == chat, CabClient.id != c.id))).scalars().all():
            other.tg_chat_id = None
        c.tg_chat_id = chat
        await db.commit()
        await log_event(db, "cab.telegram", f"Клиент {c.login}: подключил Telegram", entity="cab_client", entity_id=c.id)
        await db.commit()
        await tg.send(token, chat, f"Подключено: <b>{c.name}</b>.\nКаждый день в 19:00 МСК пришлю сводку по закупке. /stats — сводка сейчас, /stop — отключить.")
        await tg.send(token, chat, await summary_text(db, c))
        return
    c = (await db.execute(select(CabClient).where(CabClient.tg_chat_id == chat))).scalar_one_or_none()
    if cmd == "/stats" and c:
        await tg.send(token, chat, await summary_text(db, c))
    elif cmd == "/stop" and c:
        c.tg_chat_id = None
        await db.commit()
        await tg.send(token, chat, "Отключено. Подключить снова можно из кабинета: Настройки → Telegram.")
    else:
        await tg.send(token, chat, HELP)


async def _daily(db: AsyncSession, token: str) -> None:
    now = utcnow().astimezone(MSK)
    if now.time() < SUMMARY_AT:
        return
    day = now.date().isoformat()
    clients = (await db.execute(select(CabClient).where(CabClient.tg_chat_id.isnot(None), CabClient.is_active.is_(True)))).scalars().all()
    for c in clients:
        key = f"tg_summary.{c.id}"
        last = (await db.execute(select(LgSetting.value).where(LgSetting.key == key))).scalar()
        if last == day:
            continue
        try:
            await tg.send(token, c.tg_chat_id, await summary_text(db, c))
        except Exception as e:   # noqa: BLE001
            log.warning("сводка клиенту %s: %s", c.login, e)
            if "blocked" in str(e).lower() or "chat not found" in str(e).lower():
                c.tg_chat_id = None
        await db.execute(insert(LgSetting).values(key=key, value=day).on_conflict_do_update(index_elements=["key"], set_={"value": day}))
        await db.commit()


async def summary_text(db: AsyncSession, c: CabClient) -> str:
    now = utcnow().astimezone(MSK)
    day_start = datetime.combine(now.date(), time.min, tzinfo=MSK)
    week_start = day_start - timedelta(days=6)
    today = (await db.execute(select(func.count(), func.count().filter(CabContact.lf_status == "repeat"))
                              .where(CabContact.client_id == c.id, CabContact.bought_at >= day_start))).one()
    week = (await db.execute(select(func.count()).where(CabContact.client_id == c.id, CabContact.bought_at >= week_start))).scalar() or 0
    first = (await db.execute(select(func.min(CabContact.bought_at)).where(CabContact.client_id == c.id))).scalar()
    active_days = max(1, min(7, (now.date() - first.astimezone(MSK).date()).days + 1)) if first else 1   # проект младше недели — делим на его дни
    src_on, src_all = (await db.execute(select(func.count().filter(CabSource.enabled_by_user.is_(True) & CabSource.enabled_by_schedule.is_(True)), func.count())
                                        .where(CabSource.client_id == c.id))).one()
    top = (await db.execute(text("""
        SELECT s.phone, COALESCE(co.name, ''), COUNT(*) FROM cab_contacts x
        JOIN cab_sources s ON s.id = x.source_id LEFT JOIN cab_companies co ON co.id = s.company_id
        WHERE x.client_id = :cid AND x.bought_at >= :since GROUP BY s.phone, co.name ORDER BY 3 DESC LIMIT 5
    """), {"cid": c.id, "since": day_start})).all()
    avg = week / active_days
    days_left = int(c.balance_contacts / avg) if c.balance_contacts and avg > 0 else None
    lines = [f"<b>{c.name}</b> — сводка за {now.strftime('%d.%m.%Y')}",
             f"Куплено сегодня: <b>{today[0]}</b> контактов" + (f" (повторов {today[1]})" if today[1] else ""),
             f"За {'7 дней' if active_days == 7 else f'{active_days} дн.'}: {week} · в среднем {avg:.0f} в день",
             f"Баланс: <b>{c.balance_contacts if c.balance_contacts is not None else '—'}</b> контактов"
             + (f" · {c.lf_balance_rub:.0f} ₽" if c.lf_balance_rub is not None else "")
             + (f" · хватит примерно на {days_left} дн." if days_left is not None else ""),
             f"Источников включено: {src_on} из {src_all}",
             "Закупка: " + ("идёт" if c.lf_status == "active" else (c.lf_status or "не запущена"))]
    if c.balance_contacts is not None and days_left is not None and days_left < 2:
        lines.append("⚠️ Баланса меньше чем на два дня — пополните проект.")
    if top:
        lines.append("")
        lines.append("Топ источников за сегодня:")
        for phone, comp, cnt in top:
            lines.append(f"• {phone}{' · ' + comp if comp else ''} — {cnt}")
    return "\n".join(lines)
