"""Чёрный список источников кабинета: номера, которые нельзя включать никогда.

Попал в список — источник выключается сразу, cab_sync гасит его в LF. Появился снова —
при ручном добавлении пропускается, от агента досбора не принимается, из досбора в закупку
не уходит, при импорте из LF и при любом «включить» остаётся выключенным. Раз в полчаса
cab_sync сверяет список с LF: включили там руками — выключаем обратно.

Один список на клиента (проект LF): у разных клиентов разные источники и разные причины.
"""
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import CabSource, CabSourceBlacklist


def phones_stmt(client_id: int):
    """Подзапрос для in_/notin_: номера чёрного списка клиента."""
    return select(CabSourceBlacklist.phone).where(CabSourceBlacklist.client_id == client_id)


async def phones(db: AsyncSession, client_id: int) -> set[str]:
    return set((await db.execute(phones_stmt(client_id))).scalars().all())


async def is_blacklisted(db: AsyncSession, client_id: int, phone: str) -> bool:
    return bool((await db.execute(select(CabSourceBlacklist.id).where(
        CabSourceBlacklist.client_id == client_id, CabSourceBlacklist.phone == phone))).scalar())


async def add(db: AsyncSession, client_id: int, new_phones: set[str], note: str | None = None) -> tuple[int, int]:
    """→ (добавлено, уже были)."""
    have = await phones(db, client_id)
    added = 0
    for p in sorted(new_phones):
        if p in have:
            continue
        db.add(CabSourceBlacklist(client_id=client_id, phone=p, note=note))
        have.add(p)
        added += 1
    await db.flush()
    return added, len(new_phones) - added


async def disable_sources(db: AsyncSession, client_id: int, only: set[str] | None = None) -> int:
    """Выключить источники клиента из чёрного списка (все или только `only`) и пометить для LF.

    Возвращает, сколько из них было включено. Уже выключенные у нас, но включённые в LF
    (там могли включить руками), тоже помечаем lf_dirty — push_sources снова отправит
    will_work=false.
    """
    where = [CabSource.client_id == client_id, CabSource.phone.in_(phones_stmt(client_id))]
    if only is not None:
        if not only:
            return 0
        where.append(CabSource.phone.in_(list(only)))
    was_on = (await db.execute(
        update(CabSource).where(*where, CabSource.enabled_by_user.is_(True))
        .values(enabled_by_user=False, lf_dirty=True))).rowcount
    await db.execute(
        update(CabSource).where(*where, CabSource.enabled_by_user.is_(False), CabSource.lf_will_work.is_(True))
        .values(lf_dirty=True))
    return int(was_on or 0)
