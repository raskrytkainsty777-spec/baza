"""Google Sheets через сервисный аккаунт: один наш аккаунт на всех клиентов.

Ключ (JSON) админ вставляет в ГЦК → настройка google_sa_json (запасной путь — файл из .env
GOOGLE_SA_JSON). Клиент даёт таблице доступ редактора на почту аккаунта и вставляет URL.
Дальше — REST Sheets API v4 через httpx, токен обмениваем через google-auth в отдельном потоке.
"""
import asyncio
import json
import re
import time

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import LgSetting

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
API = "https://sheets.googleapis.com/v4/spreadsheets"
_token_cache: dict = {"email": None, "token": None, "exp": 0.0}


class GSError(Exception):
    pass


async def sa_info(db: AsyncSession) -> dict | None:
    raw = (await db.execute(select(LgSetting.value).where(LgSetting.key == "google_sa_json"))).scalar()
    raw = (raw or "").strip()
    if not raw and settings.google_sa_json:
        try:
            with open(settings.google_sa_json, encoding="utf-8") as f:
                raw = f.read()
        except OSError:
            raw = ""
    if not raw:
        return None
    try:
        info = json.loads(raw)
    except ValueError as e:
        raise GSError(f"JSON ключа не разбирается: {e}")
    if info.get("type") != "service_account" or not info.get("client_email") or not info.get("private_key"):
        raise GSError("Это не ключ сервисного аккаунта: нужны поля type=service_account, client_email, private_key")
    return info


def _fresh_token(info: dict) -> tuple[str, float]:
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    creds.refresh(Request())
    exp = creds.expiry.timestamp() if creds.expiry else time.time() + 3000
    return creds.token, exp


async def access_token(info: dict) -> str:
    if _token_cache["email"] == info["client_email"] and _token_cache["token"] and _token_cache["exp"] - time.time() > 120:
        return _token_cache["token"]
    try:
        token, exp = await asyncio.to_thread(_fresh_token, info)
    except Exception as e:   # noqa: BLE001
        raise GSError(f"Google не выдал токен по ключу: {str(e)[:200]}")
    _token_cache.update(email=info["client_email"], token=token, exp=exp)
    return token


async def check_key(info: dict) -> dict:
    await access_token(info)
    return {"ok": True, "client_email": info["client_email"], "project_id": info.get("project_id")}


def spreadsheet_id(url_or_id: str) -> str:
    s = (url_or_id or "").strip()
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", s)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{20,}", s):
        return s
    raise GSError("Не похоже на ссылку на Google Таблицу")


async def _req(info: dict, method: str, path: str, params: dict | None = None, json_body=None):
    token = await access_token(info)
    async with httpx.AsyncClient(timeout=30) as cl:
        r = await cl.request(method, API + path, params=params, json=json_body, headers={"Authorization": f"Bearer {token}"})
    if r.status_code == 403:
        raise GSError(f"Нет доступа к таблице: дайте доступ редактора аккаунту {info['client_email']}")
    if r.status_code == 404:
        raise GSError("Таблица не найдена — проверьте ссылку")
    if r.status_code >= 400:
        raise GSError(f"Google Sheets {r.status_code}: {r.text[:200]}")
    return r.json()


async def spreadsheet_info(info: dict, sid: str) -> dict:
    d = await _req(info, "GET", f"/{sid}", params={"fields": "properties.title,sheets.properties.title"})
    sheets = [s["properties"]["title"] for s in d.get("sheets", [])]
    header = []
    if sheets:
        v = await _req(info, "GET", f"/{sid}/values/{_quote(sheets[0])}!1:1")
        header = (v.get("values") or [[]])[0]
    return {"title": d.get("properties", {}).get("title"), "sheets": sheets, "header": header}


def _quote(sheet: str) -> str:
    return "'" + sheet.replace("'", "''") + "'"


async def append_rows(info: dict, sid: str, sheet: str, rows: list[list]) -> int:
    if not rows:
        return 0
    d = await _req(info, "POST", f"/{sid}/values/{_quote(sheet)}!A1:append",
                   params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
                   json_body={"values": rows})
    return int((d.get("updates") or {}).get("updatedRows") or len(rows))


async def write_header(info: dict, sid: str, sheet: str, header: list[str]) -> None:
    await _req(info, "PUT", f"/{sid}/values/{_quote(sheet)}!A1", params={"valueInputOption": "RAW"},
               json_body={"values": [header]})
