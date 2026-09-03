"""Клиент Apify — только то, что нам нужно ежедневно и точечно.

Роль Apify в схеме узкая (docs/DECISIONS.md): новые посты доноров на мониторе,
счётчики комментариев по известным постам, досбор свежих комментариев на крупных
постах (актор отдаёт новые → старые, проверено), рекомендации похожих аккаунтов.
Всё остальное — parser.im.

Все вызовы — REST v2 с токеном из настроек. Расход считаем по `usageTotalUsd`
прогона, чтобы держать суточный потолок.
"""
import asyncio
import logging
from typing import Any

import httpx

from ...config import settings

log = logging.getLogger(__name__)

BASE = "https://api.apify.com/v2"
TIMEOUT = 330.0

ACTOR_SCRAPER = "apify~instagram-scraper"            # посты профилей и по URL постов
ACTOR_COMMENTS = "apify~instagram-comment-scraper"   # комментарии, новые → старые
ACTOR_PROFILE = "apify~instagram-profile-scraper"    # профиль + relatedProfiles


class ApifyError(Exception):
    pass


def _token() -> str:
    if not settings.apify_token:
        raise ApifyError("Не задан APIFY_TOKEN")
    return settings.apify_token


async def run_sync(actor: str, run_input: dict, timeout_s: int = 300) -> list[dict]:
    """Запустить актор и дождаться датасета одним запросом (лимит ~5 минут)."""
    url = f"{BASE}/acts/{actor}/run-sync-get-dataset-items"
    async with httpx.AsyncClient(timeout=TIMEOUT) as cl:
        r = await cl.post(url, params={"token": _token(), "timeout": timeout_s, "clean": "true"},
                          json=run_input)
    if r.status_code >= 400:
        raise ApifyError(f"Apify {r.status_code}: {r.text[:300]}")
    data = r.json()
    if isinstance(data, dict) and data.get("error"):
        raise ApifyError(str(data["error"]))
    return data if isinstance(data, list) else []


async def run_async(actor: str, run_input: dict) -> dict:
    """Запустить и не ждать — для больших прогонов. Возвращает объект run."""
    async with httpx.AsyncClient(timeout=60) as cl:
        r = await cl.post(f"{BASE}/acts/{actor}/runs", params={"token": _token()}, json=run_input)
    if r.status_code >= 400:
        raise ApifyError(f"Apify {r.status_code}: {r.text[:300]}")
    return r.json()["data"]


async def run_status(run_id: str) -> dict:
    async with httpx.AsyncClient(timeout=60) as cl:
        r = await cl.get(f"{BASE}/actor-runs/{run_id}", params={"token": _token()})
    r.raise_for_status()
    return r.json()["data"]


async def dataset_items(dataset_id: str, limit: int = 10000) -> list[dict]:
    async with httpx.AsyncClient(timeout=TIMEOUT) as cl:
        r = await cl.get(f"{BASE}/datasets/{dataset_id}/items",
                         params={"token": _token(), "clean": "true", "limit": limit})
    r.raise_for_status()
    return r.json()


async def wait_run(run_id: str, poll_s: float = 5, max_s: float = 1800) -> dict:
    waited = 0.0
    while waited < max_s:
        st = await run_status(run_id)
        if st.get("status") in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
            return st
        await asyncio.sleep(poll_s)
        waited += poll_s
    raise ApifyError(f"Apify run {run_id}: не завершился за {max_s}s")


def run_cost_usd(run: dict) -> float:
    try:
        return float(run.get("usageTotalUsd") or 0)
    except (TypeError, ValueError):
        return 0.0


# ── то, что зовут воркеры ────────────────────────────────────────────────────

async def new_posts(usernames: list[str], newer_than: str = "1 day", per_profile: int = 20) -> list[dict]:
    """Новые посты и рилсы у доноров на мониторе. Поля: shortCode, url, caption,
    timestamp, commentsCount, likesCount, videoViewCount, productType, ownerUsername."""
    return await run_sync(ACTOR_SCRAPER, {
        "directUrls": [f"https://www.instagram.com/{u}/" for u in usernames],
        "resultsType": "posts",
        "resultsLimit": per_profile,
        "onlyPostsNewerThan": newer_than,
        "addParentData": False,
    })


async def post_counters(post_urls: list[str]) -> list[dict]:
    """Актуальные счётчики по конкретным постам: одна строка на URL."""
    return await run_sync(ACTOR_SCRAPER, {
        "directUrls": post_urls,
        "resultsType": "posts",
        "resultsLimit": 1,
        "addParentData": False,
    })


async def fresh_comments(post_url: str, limit: int) -> list[dict]:
    """Свежие комментарии крупного поста: актор отдаёт новые → старые, поэтому
    `limit` ≈ вчерашний прирост × 2 покрывает всё новое. Поля: id, text, timestamp,
    ownerUsername, owner.id, likesCount, replies[]."""
    return await run_sync(ACTOR_COMMENTS, {"directUrls": [post_url], "resultsLimit": limit})


async def related_profiles(usernames: list[str]) -> list[dict]:
    """Профили сидов с полем relatedProfiles (до ~20 у каждого)."""
    return await run_sync(ACTOR_PROFILE, {"usernames": usernames})


def flatten_comments(items: list[dict]) -> list[dict]:
    """Ответы лежат внутри `replies` — раскладываем в плоский список, помечая родителя."""
    out: list[dict] = []
    for c in items:
        out.append({**c, "parent_id": None})
        for r in c.get("replies") or []:
            out.append({**r, "parent_id": c.get("id")})
    return out


def pick(d: dict, *keys: str, default: Any = None) -> Any:
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return default
