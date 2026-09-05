"""AmoCRM API v4: поддомен + долгоживущий токен (amoCRM → Настройки → Интеграции → создать
интеграцию → «Долгосрочный токен»). Контакт ищем по номеру, сделку создаём на него.
"""
import re

import httpx

PHONE_ENUMS = [("MOB", "Мобильный"), ("WORK", "Рабочий"), ("WORKDD", "Рабочий прямой"), ("HOME", "Домашний"), ("OTHER", "Другой")]


class AmoError(Exception):
    pass


def norm_subdomain(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"^https?://", "", s).split(".amocrm")[0].split("/")[0]
    if not re.fullmatch(r"[a-z0-9-]{2,64}", s):
        raise AmoError("Поддомен — это часть адреса до .amocrm.ru, например «mycompany»")
    return s


class Amo:
    def __init__(self, subdomain: str, token: str):
        self.sub = norm_subdomain(subdomain)
        self.base = f"https://{self.sub}.amocrm.ru/api/v4"
        self.h = {"Authorization": f"Bearer {(token or '').strip()}"}

    async def _req(self, method: str, path: str, params: dict | None = None, json_body=None):
        async with httpx.AsyncClient(timeout=25) as cl:
            try:
                r = await cl.request(method, self.base + path, params=params, json=json_body, headers=self.h)
            except httpx.HTTPError as e:
                raise AmoError(f"AmoCRM недоступен: {e}")
        if r.status_code == 204:
            return None
        if r.status_code == 401:
            raise AmoError("AmoCRM: токен не принят (401) — проверьте поддомен и долгосрочный токен")
        if r.status_code == 402:
            raise AmoError("AmoCRM: аккаунт не оплачен (402)")
        if r.status_code >= 400:
            try:
                d = r.json()
                msg = d.get("detail") or d.get("title") or str(d)[:200]
                errs = d.get("validation-errors")
                if errs:
                    msg += " " + str(errs)[:200]
            except ValueError:
                msg = r.text[:200]
            raise AmoError(f"AmoCRM {r.status_code}: {msg}")
        try:
            return r.json()
        except ValueError:
            return None

    async def check(self) -> dict:
        d = await self._req("GET", "/account")
        return {"ok": True, "account": d.get("name"), "subdomain": self.sub}

    async def refs(self) -> dict:
        d = await self._req("GET", "/leads/pipelines")
        pipelines = []
        for pl in (d or {}).get("_embedded", {}).get("pipelines", []):
            sts = [{"id": s["id"], "name": s["name"]} for s in pl.get("_embedded", {}).get("statuses", []) if s["id"] not in (142, 143)]
            pipelines.append({"id": pl["id"], "name": pl["name"], "statuses": sts})
        users = []
        d = await self._req("GET", "/users", params={"limit": 250})
        for u in (d or {}).get("_embedded", {}).get("users", []):
            users.append({"id": u["id"], "name": u.get("name") or u.get("email") or f"#{u['id']}"})
        return {"pipelines": pipelines, "users": users, "phone_types": [{"id": k, "name": v} for k, v in PHONE_ENUMS]}

    async def find_contact(self, phone: str) -> int | None:
        d = await self._req("GET", "/contacts", params={"query": phone, "limit": 5})
        items = (d or {}).get("_embedded", {}).get("contacts", []) if d else []
        return int(items[0]["id"]) if items else None

    async def add_contact(self, name: str, phone: str, enum: str, responsible: int | None) -> int:
        body = {"name": name, "custom_fields_values": [{"field_code": "PHONE", "values": [{"value": phone, "enum_code": enum}]}]}
        if responsible:
            body["responsible_user_id"] = responsible
        d = await self._req("POST", "/contacts", json_body=[body])
        return int(d["_embedded"]["contacts"][0]["id"])

    async def add_lead(self, name: str, pipeline_id: int | None, status_id: int | None, responsible: int | None, contact_id: int, tag: str | None) -> int:
        body = {"name": name, "_embedded": {"contacts": [{"id": contact_id}]}}
        if pipeline_id:
            body["pipeline_id"] = pipeline_id
        if status_id:
            body["status_id"] = status_id
        if responsible:
            body["responsible_user_id"] = responsible
        if tag:
            body["_embedded"]["tags"] = [{"name": tag}]
        d = await self._req("POST", "/leads", json_body=[body])
        return int(d["_embedded"]["leads"][0]["id"])

    async def add_note(self, lead_id: int, text: str) -> None:
        await self._req("POST", f"/leads/{lead_id}/notes", json_body=[{"note_type": "common", "params": {"text": text}}])


def describe(p: dict) -> str:
    return (f"ГЦК: источник {p.get('source_phone') or '—'} · {p.get('company') or 'без компании'} · "
            f"{p.get('supplier_label') or ''} · {p.get('operator') or ''} · {p.get('region') or ''} · {p.get('bought_at') or ''}").strip(" ·")


async def push(cfg: dict, p: dict) -> str:
    amo = Amo(cfg["subdomain"], cfg["token"])
    phone = p["phone"]
    resp = int(cfg["responsible_id"]) if cfg.get("responsible_id") else None
    cid = await amo.find_contact(phone) if cfg.get("dedupe", True) else None
    if cid:
        made = f"контакт #{cid} уже был"
    else:
        cid = await amo.add_contact(p.get("company") or "Контакт", phone, cfg.get("phone_type") or "MOB", resp)
        made = f"контакт #{cid} создан"
    lid = await amo.add_lead(f"{p.get('company') or 'ГЦК'} · {phone}", int(cfg["pipeline_id"]) if cfg.get("pipeline_id") else None,
                             int(cfg["status_id"]) if cfg.get("status_id") else None, resp, cid, (cfg.get("tag") or "").strip() or None)
    try:
        await amo.add_note(lid, describe(p))
    except AmoError:
        pass
    return f"создана сделка #{lid}, {made}"
