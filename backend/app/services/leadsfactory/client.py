"""Клиент Open API Leads Factory — только то, что нужно кабинету закупки.

Всё, что знаем про их API и кабинет, — в docs/LEADSFACTORY.md. Ключевое:
* один Bearer-токен на аккаунт, лежит в настройках (leadsfactory_token), запасной — в .env;
* тег = источник × поставщик (B222 Билайн исходящие, B223 Билайн входящие, B333 МТС входящие,
  B111 исход по сайтам); норма тега — наше «закупать», статус «Включён» ставит их планировщик;
* подпись источника (label) только вместе с цветом;
* поступление денег через API не вносится; остаток и цена читаются;
* изменения применяются их циклом после 20:00 МСК.
"""
import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...models import LgSetting

log = logging.getLogger(__name__)

MSK = ZoneInfo("Europe/Moscow")
TIMEOUT = 60.0
COLORS = ["red", "orange", "yellow", "green", "blue", "purple", "pink", "lime", "teal", "sky"]
PROJECT_TYPE_VDL_NUMBERS = 5        # «VDL по номерам» из GET /crm/open-api/projects/types
SUPPLIERS = {
    "B222": "Билайн · исходящие", "B223": "Билайн · входящие", "B333": "МТС · входящие",
    "B111": "исход по сайтам", "B221": "Билайн · сайты",
}
PHONE_SUPPLIERS = ["B222", "B223", "B333"]          # что имеет смысл для номера-источника


class LFError(Exception):
    pass


def color_for(label: str) -> str:
    return COLORS[sum(map(ord, label or "")) % len(COLORS)]


def parse_dt(s: str | None) -> datetime | None:
    """`2026-09-05 08:22:25` — их время московское."""
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19], fmt).replace(tzinfo=MSK)
        except ValueError:
            continue
    return None


async def get_token(db: AsyncSession) -> str:
    v = (await db.execute(select(LgSetting.value).where(LgSetting.key == "leadsfactory_token"))).scalar()
    return (v or "").strip() or (settings.leadsfactory_token or "").strip()


class LF:
    def __init__(self, token: str, base: str | None = None):
        if not token:
            raise LFError("Не задан токен Leads Factory (Настройки → Ключи и связки)")
        self.token = token
        self.base = (base or settings.leadsfactory_base).rstrip("/")

    async def _req(self, method: str, path: str, params: dict | None = None, json=None):
        async with httpx.AsyncClient(timeout=TIMEOUT) as cl:
            r = await cl.request(method, self.base + path, params=params, json=json,
                                 headers={"Authorization": f"Bearer {self.token}"})
        if r.status_code == 401:
            raise LFError("Leads Factory: токен не принят (401)")
        if r.status_code >= 400:
            raise LFError(f"Leads Factory {r.status_code}: {r.text[:300]}")
        try:
            return r.json()
        except ValueError:
            return r.text

    # ── проект ──────────────────────────────────────────────────────────────
    async def create_project(self, name: str, type_id: int = PROJECT_TYPE_VDL_NUMBERS) -> int:
        d = await self._req("POST", "/v1/crm/open-api/projects", json={"name": name, "type": type_id})
        pid = (d or {}).get("id")
        if not pid:
            raise LFError(f"Leads Factory не вернул id проекта: {d}")
        return int(pid)

    async def project(self, crm_id: int) -> dict:
        return await self._req("GET", f"/v1/crm/open-api/projects/{crm_id}")

    async def set_status(self, crm_id: int, status: str) -> dict:
        return await self._req("PATCH", f"/v1/crm/open-api/projects/{crm_id}", json={"status": status})

    async def payment_get(self, crm_id: int) -> dict:
        return await self._req("GET", f"/v1/crm/open-api/projects/{crm_id}/payment/get")

    async def payment_update(self, crm_id: int, **fields) -> dict:
        return await self._req("PATCH", f"/v1/crm/open-api/projects/{crm_id}/payment/update", json=fields)

    async def finance(self, crm_id: int) -> dict:
        return await self._req("GET", f"/v1/crm/open-api/projects/{crm_id}/finance/get")

    async def vdl_project_patch(self, crm_id: int, **fields) -> dict:
        return await self._req("PATCH", f"/v1/vdl/api/projects/info/{crm_id}", json=fields)

    async def auto_scripts_update(self, crm_id: int, **fields) -> dict:
        return await self._req("POST", f"/v1/vdl/api/projects_settings/update_auto_scripts_settings/{crm_id}", json=fields)

    # ── источники ───────────────────────────────────────────────────────────
    async def sources_add(self, crm_id: int, phones: list[str], label: str | None = None,
                          geo_ids: list[int] | None = None) -> dict:
        body = {"source": phones, "source_type": "phone", "source_from": "web",
                "active_duplicate_source": True, "geo_ids": geo_ids or []}
        if label:
            body["label"], body["label_color"] = label[:60], color_for(label)
        return await self._req("PUT", f"/v1/vdl/api/sources/add_all/{crm_id}", json=body)

    async def sources_all(self, crm_id: int) -> list[dict]:
        out, page = [], 1
        while True:
            d = await self._req("GET", f"/v1/vdl/api/sources/get_detail_by_project/{crm_id}",
                                params={"page": page, "limit": 200})
            items = d.get("sources") or []
            out += items
            if len(items) < 200:
                return out
            page += 1

    async def sources_will_work(self, ids: list[int], on: bool) -> dict:
        return await self._req("POST", "/v1/vdl/api/sources/update_will_work_bulk",
                               json={"source_ids": ids, "will_work": on})

    async def sources_settings(self, ids: list[int], **fields) -> dict:
        return await self._req("POST", "/v1/vdl/api/sources/update_settings", json={"source_ids": ids, **fields})

    async def geo_all(self) -> list[dict]:
        d = await self._req("GET", "/v1/vdl/api/source_geo", params={"page": 1, "limit": 500})
        return d.get("geo") or []

    # ── теги ────────────────────────────────────────────────────────────────
    async def tags_all(self, crm_id: int) -> list[dict]:
        out, page = [], 1
        while True:
            d = await self._req("GET", f"/v1/vdl/api/tags/get_by_project/{crm_id}",
                                params={"page": page, "limit": 300, "show_locked": "true"})
            items = d.get("tags") or []
            out += items
            if len(items) < 300:
                return out
            page += 1

    async def tag_update(self, tag_id: int, **fields) -> dict:
        return await self._req("PATCH", f"/v1/vdl/api/tags/update/{tag_id}", json=fields)

    async def tags_increment(self, tag_ids: list[int], delta: int) -> dict:
        return await self._req("PATCH", "/v1/vdl/api/tags/increment_limits",
                               json={"tag_ids": tag_ids, "increment_by": delta})

    # ── чёрный список и заявки ──────────────────────────────────────────────
    async def blacklist_add(self, crm_id: int, phones: list[str]) -> dict:
        return await self._req("POST", f"/v1/crm/open-api/projects/{crm_id}/blacklist/add", json={"phones": phones})

    async def answers(self, crm_id: int, page: int = 1, limit: int = 200, date_from: str | None = None,
                      date_updated_from: str | None = None) -> dict:
        params = {"page": page, "limit": limit}
        if date_from:
            params["date_from"] = date_from
        if date_updated_from:
            params["date_updated_from"] = date_updated_from
        return await self._req("GET", f"/v1/crm/open-api/projects/{crm_id}/answers", params=params)


async def lf_for(db: AsyncSession) -> LF:
    return LF(await get_token(db))


async def gather_limited(coros, limit: int = 5):
    sem = asyncio.Semaphore(limit)

    async def run(c):
        async with sem:
            return await c
    return await asyncio.gather(*(run(c) for c in coros), return_exceptions=True)
