"""Bitrix24 через входящий вебхук: https://<портал>.bitrix24.ru/rest/<user>/<код>/ .
Нужны права CRM (crm) и, для списка ответственных, «Пользователи» (user).
"""
import re

import httpx

PHONE_TYPES = [("MOBILE", "Мобильный"), ("WORK", "Рабочий"), ("HOME", "Домашний"), ("OTHER", "Другой")]


class BitrixError(Exception):
    pass


def norm_webhook(url: str) -> str:
    u = (url or "").strip()
    if not re.match(r"^https://[\w.-]+/rest/\d+/[\w]+/?$", u):
        raise BitrixError("Вебхук должен выглядеть как https://портал.bitrix24.ru/rest/1/abcdef123456/")
    return u if u.endswith("/") else u + "/"


class Bitrix:
    def __init__(self, webhook: str):
        self.base = norm_webhook(webhook)

    async def call(self, method: str, params: dict | None = None):
        async with httpx.AsyncClient(timeout=25) as cl:
            try:
                r = await cl.post(self.base + method + ".json", json=params or {})
            except httpx.HTTPError as e:
                raise BitrixError(f"Bitrix24 недоступен: {e}")
        try:
            d = r.json()
        except ValueError:
            raise BitrixError(f"Bitrix24 ответил не JSON ({r.status_code})")
        if r.status_code >= 400 or "error" in d:
            desc = d.get("error_description") or d.get("error") or f"HTTP {r.status_code}"
            if d.get("error") in ("INVALID_CREDENTIALS", "ERROR_METHOD_NOT_FOUND") or r.status_code in (401, 403):
                desc = f"вебхук не принят или нет прав: {desc}"
            raise BitrixError(f"Bitrix24: {desc}")
        return d.get("result")

    async def call_all(self, method: str, params: dict | None = None, max_pages: int = 6) -> list:
        out, start = [], 0
        for _ in range(max_pages):
            p = dict(params or {}, start=start)
            async with httpx.AsyncClient(timeout=25) as cl:
                r = await cl.post(self.base + method + ".json", json=p)
            d = r.json()
            if "error" in d:
                raise BitrixError(f"Bitrix24: {d.get('error_description') or d.get('error')}")
            out.extend(d.get("result") or [])
            if d.get("next") is None:
                break
            start = d["next"]
        return out

    async def check(self) -> dict:
        fields = await self.call("crm.lead.fields")
        if not isinstance(fields, dict):
            raise BitrixError("Bitrix24: нет доступа к CRM (проверьте права вебхука)")
        return {"ok": True}

    async def refs(self) -> dict:
        """Справочники для настройки: ответственные, статусы лидов, источники, воронки со стадиями."""
        users = []
        try:
            for u in await self.call_all("user.get", {"filter": {"ACTIVE": True}}):
                name = " ".join(x for x in (u.get("NAME"), u.get("LAST_NAME")) if x) or u.get("EMAIL") or f"#{u['ID']}"
                users.append({"id": int(u["ID"]), "name": name})
        except BitrixError:
            pass   # нет права «Пользователи» — ответственного можно вписать номером
        statuses = [{"id": s["STATUS_ID"], "name": s["NAME"]} for s in await self.call("crm.status.list", {"filter": {"ENTITY_ID": "STATUS"}})]
        sources = [{"id": s["STATUS_ID"], "name": s["NAME"]} for s in await self.call("crm.status.list", {"filter": {"ENTITY_ID": "SOURCE"}})]
        cats = [{"id": 0, "name": "Общая"}]
        try:
            d = await self.call("crm.dealcategory.default.get")
            if isinstance(d, dict) and d.get("NAME"):
                cats[0]["name"] = d["NAME"]
        except BitrixError:
            pass
        for c in await self.call("crm.dealcategory.list") or []:
            cats.append({"id": int(c["ID"]), "name": c["NAME"]})
        for c in cats:
            stages = await self.call("crm.dealcategory.stage.list", {"id": c["id"]})
            c["stages"] = [{"id": s["STATUS_ID"], "name": s["NAME"]} for s in stages or []]
        return {"users": users, "lead_statuses": statuses, "sources": sources, "categories": cats, "phone_types": [{"id": k, "name": v} for k, v in PHONE_TYPES]}

    async def find_by_phone(self, phone: str, entity: str) -> int | None:
        res = await self.call("crm.duplicate.findbycomm", {"type": "PHONE", "values": [phone, "+" + phone], "entity_type": entity})
        if isinstance(res, dict):
            ids = res.get(entity) or []
            return int(ids[0]) if ids else None
        return None

    async def add(self, entity: str, fields: dict) -> int:
        method = {"lead": "crm.lead.add", "deal": "crm.deal.add", "contact": "crm.contact.add"}[entity]
        res = await self.call(method, {"fields": fields, "params": {"REGISTER_SONET_EVENT": "N"}})
        return int(res)


def describe(p: dict) -> str:
    return (f"ГЦК: источник {p.get('source_phone') or '—'} · {p.get('company') or 'без компании'} · "
            f"{p.get('supplier_label') or ''} · {p.get('operator') or ''} · {p.get('region') or ''} · {p.get('bought_at') or ''}").strip(" ·")


async def push(cfg: dict, p: dict) -> str:
    """Один контакт → Bitrix24. Возвращает, что сделали (для журнала)."""
    bx = Bitrix(cfg["webhook"])
    phone = p["phone"]
    entity = cfg.get("entity") or "lead"
    resp = int(cfg["responsible_id"]) if cfg.get("responsible_id") else None
    title = f"{p.get('company') or 'ГЦК'} · {phone}"
    common = {"SOURCE_DESCRIPTION": describe(p), "COMMENTS": describe(p)}
    if cfg.get("source_id"):
        common["SOURCE_ID"] = cfg["source_id"]
    if resp:
        common["ASSIGNED_BY_ID"] = resp
    phones = [{"VALUE": phone, "VALUE_TYPE": cfg.get("phone_type") or "MOBILE"}]
    dedupe = cfg.get("dedupe", True)
    if entity == "lead":
        if dedupe and await bx.find_by_phone(phone, "LEAD"):
            return "дубль: лид с этим номером уже есть — пропущен"
        fields = {"TITLE": title, "NAME": p.get("company") or "", "PHONE": phones, **common}
        if cfg.get("status_id"):
            fields["STATUS_ID"] = cfg["status_id"]
        lid = await bx.add("lead", fields)
        return f"создан лид #{lid}"
    # сделка: контакт ищем по номеру, не нашли — создаём, потом сделка на него
    cid = await bx.find_by_phone(phone, "CONTACT") if dedupe else None
    if not cid:
        cid = await bx.add("contact", {"NAME": p.get("company") or "Контакт", "PHONE": phones, **common})
        made = f"контакт #{cid} создан"
    else:
        made = f"контакт #{cid} уже был"
    fields = {"TITLE": title, "CONTACT_ID": cid, "CATEGORY_ID": int(cfg.get("category_id") or 0), **common}
    if cfg.get("stage_id"):
        fields["STAGE_ID"] = cfg["stage_id"]
    did = await bx.add("deal", fields)
    return f"создана сделка #{did}, {made}"
