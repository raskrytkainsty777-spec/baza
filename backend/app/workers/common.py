"""Общее для воркеров: настройки, журнал, постановка заданий, мелкие утилиты.

Флаги и служебные значения (run_now.*, last_run.*, heartbeat.*) живут в той же
lg_settings, что и пользовательские настройки — воркер и API общаются через базу,
отдельного канала между процессами нет и не нужно.
"""
import re
from datetime import date, datetime, timezone
from typing import Iterable, Iterator

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.settings import DEFAULTS, get_all
from ..models import LgCity, LgEvent, LgJob, LgSetting

# приоритет строк parser.im: меньше — раньше
PRIORITY = {"comments_growth": 5, "comments": 10, "posts_intake": 20, "filter": 30, "search": 40}

_SC = re.compile(r"/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def today_key() -> str:
    return date.today().isoformat()


async def settings_all(db: AsyncSession) -> dict[str, str]:
    return await get_all(db)


def as_int(values: dict, key: str, default: int | None = None) -> int:
    try:
        return int(values.get(key) or DEFAULTS.get(key) or default or 0)
    except ValueError:
        return int(DEFAULTS.get(key) or default or 0)


def as_float(values: dict, key: str, default: float = 0.0) -> float:
    try:
        return float(values.get(key) or DEFAULTS.get(key) or default)
    except ValueError:
        return default


async def collection_on(db: AsyncSession) -> bool:
    return (await settings_all(db)).get("collection_enabled") == "1"


async def ai_on(db: AsyncSession) -> bool:
    return (await settings_all(db)).get("ai_enabled", "1") == "1"


async def set_value(db: AsyncSession, key: str, value: str) -> None:
    await db.execute(
        insert(LgSetting).values(key=key, value=value)
        .on_conflict_do_update(index_elements=["key"], set_={"value": value}))


async def get_value(db: AsyncSession, key: str) -> str | None:
    return (await db.execute(select(LgSetting.value).where(LgSetting.key == key))).scalar()


async def take_flag(db: AsyncSession, key: str) -> bool:
    """Одноразовый флаг из API («запустить сейчас»): прочитать и снять."""
    v = await get_value(db, key)
    if v != "1":
        return False
    await db.execute(delete(LgSetting).where(LgSetting.key == key))
    await db.commit()
    return True


async def heartbeat(db: AsyncSession, worker: str) -> None:
    await set_value(db, f"heartbeat.{worker}", utcnow().isoformat(timespec="seconds"))
    await db.commit()


async def add_ai_cost(db: AsyncSession, usd: float) -> None:
    if usd <= 0:
        return
    key = f"ai_cost.{today_key()}"
    cur = await get_value(db, key)
    try:
        total = float(cur or 0) + usd
    except ValueError:
        total = usd
    await set_value(db, key, f"{total:.5f}")


async def log_event(db: AsyncSession, kind: str, message: str, *, entity: str | None = None,
                    entity_id: int | None = None, level: str = "info", payload: dict | None = None) -> None:
    db.add(LgEvent(kind=kind, message=message[:500], entity=entity, entity_id=entity_id,
                   level=level, payload=payload))


async def enqueue_job(db: AsyncSession, *, provider: str, kind: str, purpose: str,
                      payload: dict | None = None, lines: int = 0, priority: int | None = None,
                      city_id: int | None = None, donor_id: int | None = None,
                      search_task_id: int | None = None, state: str = "queued") -> LgJob:
    job = LgJob(provider=provider, kind=kind, purpose=purpose[:300], payload=payload or {},
                lines=lines, priority=PRIORITY.get(kind, 50) if priority is None else priority,
                city_id=city_id, donor_id=donor_id, search_task_id=search_task_id, state=state,
                started_at=utcnow() if state == "running" else None)
    db.add(job)
    await db.flush()
    return job


def chunks(xs: Iterable, n: int) -> Iterator[list]:
    buf: list = []
    for x in xs:
        buf.append(x)
        if len(buf) >= n:
            yield buf
            buf = []
    if buf:
        yield buf


def shortcode_of(url: str) -> str | None:
    m = _SC.search(url or "")
    return m.group(1) if m else None


def norm_login(s: str) -> str:
    """`https://www.instagram.com/Login/` → `login`; `@login` → `login`."""
    s = (s or "").strip()
    s = re.sub(r"^https?[:_]//(www\.)?instagram\.com/", "", s, flags=re.I)
    return s.strip("/@ ").split("/")[0].split("?")[0].lower()


def to_int(v) -> int | None:
    try:
        return int(str(v).strip().replace(" ", "")) if v not in (None, "") else None
    except ValueError:
        return None


async def city_by_name(db: AsyncSession, name: str | None) -> LgCity | None:
    if not name or not str(name).strip():
        return None
    n = str(name).strip()
    rows = (await db.execute(select(LgCity))).scalars().all()
    for c in rows:
        if c.name.lower() == n.lower():
            return c
    return None


async def get_or_create_city(db: AsyncSession, name: str | None) -> LgCity | None:
    """Город из ответа ИИ. Нет такого — заводим выключенным, чтобы пост не потерялся,
    а оператор увидел новый город в списке и решил, включать ли."""
    c = await city_by_name(db, name)
    if c or not name:
        return c
    n = str(name).strip()[:80]
    if len(n) < 2 or n.lower() in ("null", "none", "нет", "неясно"):
        return None
    c = LgCity(name=n, is_active=False)
    db.add(c)
    await db.flush()
    await log_event(db, "city.created_by_ai", f"ИИ назвала новый город «{n}» — заведён выключенным",
                    entity="city", entity_id=c.id, level="warn")
    return c
