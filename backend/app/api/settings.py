"""Общие настройки: ключ → значение в lg_settings поверх дефолтов.

Секреты (ключи API, токены) живут в .env и здесь не показываются — только
флаг «задан / не задан». Промпты и расписание — обычные строки, правятся из UI.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings as env
from ..db import get_db
from ..models import LgSetting
from .deps import require_token

router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Depends(require_token)])

DEFAULTS: dict[str, str] = {
    "intake_days": "45",                    # окно постов при заводе донора
    "comment_fresh_days_default": "30",     # свежесть комментариев по умолчанию для городов
    "parserim_lines": "10",                 # строк в тарифе parser.im
    "big_post_threshold": "1000",           # с какого числа комментариев пост досбирается через Apify
    "min_comments_first": "6",              # первый сбор: посты с меньшим числом комментариев пропускаем (прирост их подхватит)
    "f1_lastpost_days": "30",               # f1: отсев кандидатов, у кого последний пост старше N дней
    "f1_followers_min": "0",                # f1: подписчиков не меньше (0 — без порога)
    "f1_followers_max": "0",                # f1: подписчиков не больше (0 — без порога)
    "apify_daily_cap_usd": "10",
    "collection_enabled": "1",             # master: posts of new donors, Apify daily cycle (per-city flags decide where)
    "comments_enabled": "1",               # master: p2 / Apify comment collection (per-city flags decide where)
    "auto_distribute": "1",                # confident candidates become donors without the button
    "unclassified_collect_posts": "0",     # donors without a city: collect posts (p1) so AI can place posts by city
    "ai_model.comments": "google/gemini-2.5-flash-lite",   # bench 04.09: 98% agreement with haiku in batch mode, $0.025/1000
    "ai_model.posts": "google/gemini-2.5-flash-lite",      # bench 04.09: category/code word closer to manual labels than haiku
    "ai_model.cands": "anthropic/claude-haiku-4.5",         # small volume, city decision matters; cheap models agree 77-87%
    "ai_batch_size": "20",                 # comments of one post per AI call
    "leadsfactory_token": "",              # Leads Factory Open API bearer (vdl), issued in their cabinet
    "ai_enabled": "1",
    "schedule.new_posts": "09:00,21:00",
    "schedule.counters": "09:10",
    "schedule.comments": "10:00",
    "schedule.rollup": "03:00",
    "prompt.activity": "",
    "prompt.city": "",
    "prompt.post": "",
    "prompt.post_city": "",
    "prompt.comment": "",
}


class SettingsPatch(BaseModel):
    values: dict[str, str]


async def get_all(db: AsyncSession) -> dict[str, str]:
    rows = (await db.execute(select(LgSetting))).scalars().all()
    merged = dict(DEFAULTS)
    merged.update({r.key: r.value or "" for r in rows})
    return merged


async def get_int(db: AsyncSession, key: str) -> int:
    try:
        return int((await get_all(db)).get(key, DEFAULTS.get(key, "0")))
    except ValueError:
        return int(DEFAULTS.get(key, "0"))


@router.get("")
async def read(db: AsyncSession = Depends(get_db)):
    return {
        "values": await get_all(db),
        "env": {
            "parserim_key_set": bool(env.parserim_key),
            "apify_token_set": bool(env.apify_token),
            "openrouter_key_set": bool(env.openrouter_key),
            "telegram_bot_set": bool(env.telegram_bot_token),
            "probe_base_url": env.probe_base_url,
            "ai_model_cheap": env.ai_model_cheap,
            "ai_model_smart": env.ai_model_smart,
        },
    }


@router.put("")
async def write(body: SettingsPatch, db: AsyncSession = Depends(get_db)):
    for key, value in body.values.items():
        if key not in DEFAULTS:
            raise HTTPException(400, f"Неизвестная настройка: {key}")
        await db.execute(
            insert(LgSetting).values(key=key, value=str(value))
            .on_conflict_do_update(index_elements=["key"], set_={"value": str(value)})
        )
    await db.commit()
    return {"values": await get_all(db)}
