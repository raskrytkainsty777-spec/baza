"""Клиент parser.im — https://parser.im/api.php.

Что важно знать про этот API (все квирки проверены, подробности в docs/PARSERIM.md):

* Любой не-ASCII символ в параметрах роняет их бэкенд: имя задания транслитерируем,
  кириллицу в фильтры не шлём вообще.
* Ошибка на `create` не значит, что задание не создалось — сверяемся со списком.
* `mode=result` — не JSON: текст с BOM, первая строка — заголовок колонок через `:`,
  двоеточия внутри значений они экранируют сами.
* Сбор комментариев — только `web=1` и без `dop`/фильтров: с ними скорость падает
  со 318 до 3.6 комментариев в минуту.
* Тариф — N одновременных строк (логинов / тегов / ссылок), сверх — очередь.
  Планировщик режет задания по `settings.parserim_lines`.
"""
import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx

from ...config import settings

log = logging.getLogger(__name__)

BASE_URL = "https://parser.im/api.php"
TIMEOUT = 180.0
MSK = ZoneInfo("Europe/Moscow")

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
    "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


class ParserImError(Exception):
    pass


def translit(s: str) -> str:
    out = []
    for ch in s or "":
        low = ch.lower()
        if low in _TRANSLIT:
            t = _TRANSLIT[low]
            out.append(t.upper() if ch.isupper() and t else t)
        elif ord(ch) < 128:
            out.append(ch)
        else:
            out.append(" ")
    return re.sub(r"\s+", " ", "".join(out)).strip()[:80]


def is_ascii(s: str) -> bool:
    return all(ord(ch) < 128 for ch in s or "")


# ── транспорт ────────────────────────────────────────────────────────────────

async def _call(params: dict) -> str:
    if not settings.parserim_key:
        raise ParserImError("Не задан PARSERIM_KEY")
    q = {"key": settings.parserim_key, **{k: v for k, v in params.items() if v not in (None, "")}}
    for k, v in q.items():
        if k != "key" and isinstance(v, str) and not is_ascii(v):
            raise ParserImError(f"parser.im не принимает не-ASCII в параметре {k}")
    async with httpx.AsyncClient(timeout=TIMEOUT) as cl:
        r = await cl.get(BASE_URL, params=q)
    if r.status_code == 429:
        raise ParserImError("parser.im: превышен лимит запросов в минуту")
    return r.text


def _html_error(body: str) -> str:
    m = re.search(r"<title>(.*?)</title>", body, re.S | re.I)
    title = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    return f"parser.im вернул ошибку ({title or 'не-JSON ответ'})"


async def _json(params: dict) -> dict:
    body = (await _call(params)).lstrip("﻿").lstrip()
    if not body.startswith("{"):
        raise ParserImError(_html_error(body))
    try:
        data = json.loads(body)
    except ValueError:
        raise ParserImError("parser.im вернул нечитаемый ответ")
    if data.get("status") != "ok":
        raise ParserImError(data.get("text") or data.get("details") or "parser.im: ошибка")
    return data


# ── задания ──────────────────────────────────────────────────────────────────

async def list_tasks(status: int = 7) -> list[dict]:
    return (await _json({"mode": "status", "status": status})).get("tasks") or []


async def task_status(tid: str) -> dict:
    return await _json({"mode": "status", "tid": tid})


async def finish_task(tid: str) -> None:
    await _json({"mode": "finish", "tid": tid})


async def delete_task(tid: str) -> None:
    await _json({"mode": "delete", "tid": tid})


async def _create(name: str, params: dict) -> list[str]:
    """Создать задание, вернуть список tid. Если их бэкенд ответил HTML-ошибкой,
    задание всё-таки могло создаться — ищем по имени в списке, чтобы не плодить сирот."""
    name = translit(name) or "baza"
    since = int(datetime.now(timezone.utc).timestamp()) - 120
    try:
        data = await _json({"mode": "create", "name": name, **params})
    except ParserImError as e:
        await asyncio.sleep(2)
        try:
            found = [str(t["tid"]) for t in await list_tasks()
                     if t.get("name") == name and int(t.get("add_time") or 0) >= since]
        except ParserImError:
            found = []
        if found:
            log.warning("parser.im: create вернул ошибку (%s), но задание создалось: %s", e, found)
            return found
        raise
    tid = str(data.get("tid") or "").strip()
    if not tid:
        raise ParserImError("parser.im не вернул id задания")
    return [t.strip() for t in tid.split(",") if t.strip()]


async def create_authors_by_hashtags(name: str, hashtags: list[str]) -> list[str]:
    """p3 act=5 — авторы постов по хэштегам. Без фильтров: они на парсинге медленные."""
    tags = [t.lstrip("#").strip() for t in hashtags if t.strip()]
    return await _create(name, {"type": "p3", "act": 5, "links": ",".join(tags),
                                "collect_source": 1, "spec": "1,2", "unique": 1})


async def create_authors_by_keywords(name: str, words: list[str]) -> list[str]:
    """p5 act=5 — авторы постов по ключевым словам."""
    return await _create(name, {"type": "p5", "act": 5, "links": ",".join(w.strip() for w in words if w.strip()),
                                "collect_source": 1, "spec": "1,2"})


async def create_filter(name: str, source: str | list[str], *, lastpost_days: int = 30,
                        followers_from: int = 0, followers_to: int = 0) -> list[str]:
    """f1 — фильтрация и догрузка данных. `source` — tid задания-источника или список логинов.
    dop: 3 имя, 5 город+адрес, 6 описание, 8 подписчики, 21 постов, 22 дата последнего поста.
    Телефон (1) и email (2) не берём — решение заказчика."""
    links = source if isinstance(source, str) else ",".join(source)
    p = {"type": "f1", "links": links, "spec": "1,2", "dop": "3,5,6,8,21,22",
         "lastpost": lastpost_days, "private": 1}
    if followers_from:
        p["followers1"] = followers_from
    if followers_to:
        p["followers2"] = followers_to
    return await _create(name, p)


async def create_posts(name: str, logins: list[str], per_account: int = 60) -> list[str]:
    """p1 act=6 — посты аккаунтов. Сверху приходят закреплённые (старые), поэтому
    окно по дате режем у себя, а лимит берём с запасом."""
    return await _create(name, {"type": "p1", "act": 6, "links": ",".join(logins),
                                "collect_source": 1, "spec": "1,2,4,5,6,7", "limit2": per_account})


async def create_comments(name: str, post_urls: list[str]) -> list[str]:
    """p2 act=3 — комментарии по ссылкам на посты. Только web=1, без dop и фильтров."""
    return await _create(name, {"type": "p2", "act": 3, "links": ",".join(post_urls),
                                "collect_source": 1, "spec": "1,2,3,4,6", "web": 1, "unique": 1})


# ── результат ────────────────────────────────────────────────────────────────

_DATE_FORMATS = ("%d.%m.%Y_%H.%M.%S", "%d.%m.%Y_%H.%M", "%d.%m.%Y")


def parse_date(raw: str) -> datetime | None:
    """`23.07.2026_21.28` → aware datetime. Их время — московское."""
    raw = (raw or "").strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=MSK)
        except ValueError:
            continue
    return None


def unescape_url(v: str) -> str:
    """`https_//www.instagram.com/p/X/` → нормальный URL."""
    return re.sub(r"^(https?)_//", r"\1://", v or "")


def parse_result(body: str) -> list[dict]:
    """Текст выгрузки → список словарей по заголовку первой строки."""
    body = (body or "").lstrip("﻿")
    lines = [ln for ln in body.replace("\r\n", "\n").split("\n") if ln.strip()]
    if not lines or ":" not in lines[0]:
        return []
    keys = [h.strip() for h in lines[0].split(":")]
    out = []
    for ln in lines[1:]:
        parts = ln.split(":")
        if len(parts) < len(keys):
            parts += [""] * (len(keys) - len(parts))
        elif len(parts) > len(keys):
            parts = parts[:len(keys) - 1] + [":".join(parts[len(keys) - 1:])]
        out.append(dict(zip(keys, parts)))
    return out


async def fetch_result(tid: str) -> list[dict]:
    body = await _call({"mode": "result", "tid": tid})
    stripped = body.lstrip("﻿").lstrip()
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
        except ValueError:
            data = {}
        if data.get("status") == "error":
            raise ParserImError(data.get("text") or "parser.im: ошибка выгрузки")
        return []
    if stripped.startswith("<"):
        raise ParserImError(_html_error(stripped))
    return parse_result(body)
