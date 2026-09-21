"""Расписание закупки по дням недели — наша логика, у LF такого нет.

Вечером в 19:40 МСК смотрим на завтра и переводим проект в «паузу» либо «активный»
(их цикл применяет настройки после 20:00 МСК, поэтому именно вечером). Днём, до 19:40,
держим состояние текущего дня: сняли галочку с сегодняшнего дня — закупка встаёт сразу,
вернули — продолжается. Раньше день правился только накануне вечером, и снятая утром
галочка не срабатывала вовсе (разбор 14.09.2026).

Запасной путь — если CRM-контур LF недоступен (у них бывает 502), гасим источники флагом
enabled_by_schedule; источники, выключенные самим клиентом, не трогаем.
"""
import asyncio
import logging
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal
from ..models import CabClient, CabContact, CabSource
from ..services.leadsfactory.client import LF, LFError, MSK, get_token
from .common import heartbeat, log_event

log = logging.getLogger("cab_schedule")

POLL = 60
APPLY_AT = (19, 40)
MIN_BALANCE_AT = (19, 30)   # до вечернего цикла LF (после 20:00 МСК он применяет настройки)


async def run():
    while True:
        try:
            async with SessionLocal() as db:
                now = datetime.now(MSK)
                if (now.hour, now.minute) >= MIN_BALANCE_AT:
                    await _apply_min_balance(db, now)
                if (now.hour, now.minute) >= APPLY_AT:
                    await _apply_tomorrow(db, now)
                else:
                    await _hold_today(db, now)
                await heartbeat(db, "cab_schedule")
        except Exception:
            log.exception("проход не удался")
        await asyncio.sleep(POLL)


def _days(c: CabClient) -> list:
    return c.weekdays if c.weekdays and len(c.weekdays) == 7 else [True] * 7


def _no_money(c: CabClient) -> bool:
    """Баланс исчерпан — расписание не имеет права включать закупку.

    Иначе «сегодня рабочий день» каждую минуту возвращает проект в active, а
    cab_sync тут же гасит его по нулю: два воркера дерутся, и в окна между
    ними закупка успевает уходить в минус. Пополнили баланс — расписание само
    включит закупку обратно на следующем проходе.
    """
    return c.balance_contacts is not None and c.balance_contacts <= 0


async def _set(db: AsyncSession, lf: LF | None, c: CabClient, on: bool, note: str) -> None:
    """Перевести закупку клиента в нужное состояние: статусом проекта, иначе источниками.

    Источники — только запасной путь, когда CRM LF не принял статус. Раньше при уже
    совпадающем статусе функция проваливалась в эту ветку и гасила все источники:
    gckspb078777 18.09.2026 в полночь — проект на паузе, 932 источника выключены «как при
    сбое CRM», а вернуть их 19.09 не вышло (LF обрывал ответ со списком источников),
    и воскресенье прошло без закупки при активном проекте.
    """
    want = "active" if on else "pause"
    if lf and c.lf_crm_id:
        changed = False
        try:
            if c.lf_status != want:
                await lf.set_status(c.lf_crm_id, want)
                c.lf_status = want
                changed = True
        except LFError as e:
            log.warning("клиент %s: статус проекта не сменился (%s) — гасим источниками", c.login, e)
        else:
            # статус на месте — источники, погашенные запасным путём, возвращаем
            back = await db.execute(update(CabSource).where(
                CabSource.client_id == c.id, CabSource.enabled_by_schedule.is_(False))
                .values(enabled_by_schedule=True, lf_dirty=True))
            if changed or back.rowcount:
                await log_event(db, "cab.schedule",
                                f"Клиент {c.login}: {note} — проект "
                                + ("переведён в" if changed else "уже в") + f" «{want}»"
                                + (f", источников возвращено {back.rowcount}" if back.rowcount else ""),
                                entity="cab_client", entity_id=c.id)
            return
    res = await db.execute(update(CabSource).where(
        CabSource.client_id == c.id, CabSource.enabled_by_schedule.is_(not on))
        .values(enabled_by_schedule=on, lf_dirty=True))
    if res.rowcount:
        await log_event(db, "cab.schedule",
                        f"Клиент {c.login}: {note} — через источники (CRM LF недоступен), затронуто {res.rowcount}",
                        entity="cab_client", entity_id=c.id, level="warn")


async def _clients(db: AsyncSession) -> tuple[list[CabClient], LF | None]:
    clients = (await db.execute(select(CabClient).where(CabClient.is_active.is_(True)))).scalars().all()
    if not clients:
        return [], None
    token = await get_token(db)
    return clients, (LF(token) if token else None)


async def _apply_min_balance(db: AsyncSession, now: datetime) -> None:
    """«Мин. остаток актива» = сколько номеров выгрузили сегодня: завтра закупка встанет,
    когда на балансе останется столько же. Порог всегда хотя бы на контакт ниже баланса,
    иначе LF остановит закупку сразу (правило заказчика 17.09.2026).

    Порог — только страховка на «завтра» и работает лишь при положительном
    балансе. Настоящий стоп на нуле — `maybe_stop` в cab_sync, он гасит проект
    статусом в течение минуты.
    """
    today = now.date()
    clients, lf = await _clients(db)
    if not lf:
        return
    day_start = datetime(today.year, today.month, today.day, tzinfo=MSK)
    for c in clients:
        if not c.min_balance_auto or not c.lf_crm_id or c.min_balance_applied_day == today:
            continue
        bought = (await db.execute(select(func.count()).select_from(CabContact).where(
            CabContact.client_id == c.id, CabContact.bought_at >= day_start))).scalar() or 0
        if not bought:
            continue   # первый день или закупки не было — порог не трогаем
        balance = c.balance_contacts or 0
        cost = float(c.lf_answer_cost or 0)
        if not cost:
            continue
        # Ноль LF понимает как «порога нет», а не «стоп на нуле». Поэтому на
        # нулевом и минусовом балансе порог не трогаем вовсе: там закупку гасит
        # maybe_stop статусом проекта. Раньше max(0, …) схлопывал порог в ноль
        # ровно тогда, когда он был нужнее всего, и закупка уходила в минус.
        limit = min(bought, balance - 1)
        if limit <= 0:
            continue
        try:
            await lf.payment_update(c.lf_crm_id, min_client_balance=round(limit * cost, 2))
        except LFError as e:
            log.warning("клиент %s: порог остатка не выставился (%s)", c.login, e)
            continue
        c.min_balance_contacts = limit
        c.min_balance_applied_day = today
        note = f"выгрузили сегодня {bought}"
        if limit < bought:
            note += f", но на балансе {balance} — ставим на контакт ниже"
        await log_event(db, "cab.min_balance",
                        f"Клиент {c.login}: порог остановки {limit} контактов ({round(limit * cost)} ₽) — {note}",
                        entity="cab_client", entity_id=c.id)
    await db.commit()


async def _apply_tomorrow(db: AsyncSession, now: datetime) -> None:
    today = now.date()
    clients, lf = await _clients(db)
    for c in clients:
        if c.schedule_applied_day == today:
            continue
        on = bool(_days(c)[(now.weekday() + 1) % 7]) and not _no_money(c)
        await _set(db, lf, c, on, f"завтра закупка {'включена' if on else 'выключена'} расписанием"
                   + (" (баланс исчерпан)" if _no_money(c) else ""))
        c.schedule_applied_day = today
    await db.commit()


async def _hold_today(db: AsyncSession, now: datetime) -> None:
    """Днём держим состояние текущего дня: правка расписания срабатывает сразу, а не назавтра."""
    clients, lf = await _clients(db)
    for c in clients:
        on = bool(_days(c)[now.weekday()]) and not _no_money(c)
        want = "active" if on else "pause"
        # источники, погашенные запасным путём, надо вернуть, как только статус снова принимается
        fallen = (await db.execute(select(CabSource.id).where(
            CabSource.client_id == c.id, CabSource.enabled_by_schedule.is_(False)).limit(1))).first()
        if c.lf_status == want and not fallen:
            continue
        await _set(db, lf, c, on, f"сегодня закупка {'включена' if on else 'выключена'} расписанием")
    await db.commit()
