"""Кандидат → донор и распределение задачи поиска.

Общее для API (кнопки «Распределить», «В город») и воркера discovery, который
распределяет уверенных сам, если включено auto_distribute.
"""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import IgAccount, LgCandidate, LgDonor, LgEvent, LgSearchTask

CONFIDENT = 0.9


async def make_donor(db: AsyncSession, c: LgCandidate, city_id: int | None, task: LgSearchTask | None) -> bool:
    """Кандидат → аккаунт + донор. False, если такой донор уже есть."""
    acc = (await db.execute(select(IgAccount).where(IgAccount.username == c.username))).scalar_one_or_none()
    if acc is None:
        acc = IgAccount(username=c.username, ig_id=c.ig_id, full_name=c.full_name, bio=c.bio,
                        address=c.address, followers=c.followers, posts_count=c.posts_count,
                        last_post_at=c.last_post_at, roles="donor", activity_kind=c.activity_kind,
                        city_id=city_id, city_source="ai" if city_id else None)
        db.add(acc)
        await db.flush()
    else:
        acc.roles = "donor" if acc.roles == "donor" else "both"
        if city_id and not acc.city_id:
            acc.city_id, acc.city_source = city_id, "ai"
        for k in ("full_name", "bio", "address", "followers", "posts_count", "last_post_at", "activity_kind"):
            if getattr(acc, k) in (None, "") and getattr(c, k) not in (None, ""):
                setattr(acc, k, getattr(c, k))
    q = select(LgDonor).where(LgDonor.account_id == acc.id)
    q = q.where(LgDonor.city_id == city_id) if city_id else q.where(LgDonor.city_id.is_(None))
    if (await db.execute(q)).scalar_one_or_none():
        return False
    db.add(LgDonor(account_id=acc.id, city_id=city_id, status="new" if city_id else "unclassified",
                   intake_stage="posts", found_via=task.kind if task else "manual",
                   search_task_id=task.id if task else None,
                   status_reason=f"из задачи поиска #{task.id}" if task else "добавлен вручную"))
    return True


async def distribute_task(db: AsyncSession, t: LgSearchTask) -> tuple[int, int]:
    """Уверенные (≥ CONFIDENT, деятельность подходит, город известен) → в свои города статусом «новый».
    Возвращает (создано доноров, рассмотрено кандидатов)."""
    cands = (await db.execute(select(LgCandidate).where(
        LgCandidate.task_id == t.id, LgCandidate.state == "classified",
        LgCandidate.city_id.isnot(None), LgCandidate.city_confidence >= CONFIDENT,
        LgCandidate.activity_ok.isnot(False)))).scalars().all()
    made = 0
    for c in cands:
        if await make_donor(db, c, c.city_id, t):
            made += 1
        c.state = "distributed"
    if cands:
        t.distributed = (t.distributed or 0) + made
        db.add(LgEvent(kind="search.distributed", entity="search_task", entity_id=t.id,
                       message=f"{t.title}: по городам ушло {made} из {len(cands)}"))
    if t.stage == "ready" and cands:
        t.stage, t.stage_changed_at = "distributed", datetime.now(timezone.utc)
    return made, len(cands)
