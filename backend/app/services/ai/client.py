"""Вызовы моделей через OpenRouter.

Промпты живут в backend/prompts/*.md и переопределяются из настроек
(ключи prompt.*), чтобы заказчик правил критерии без деплоя. Модель отвечает
JSON — извлекаем первый объект из ответа, потому что response_format у части
моделей игнорируется. Стоимость вызова берём из usage OpenRouter и копим по дням.
"""
import asyncio
import json
import logging
import re
from pathlib import Path

import httpx

from ...config import settings

log = logging.getLogger(__name__)

URL = "https://openrouter.ai/api/v1/chat/completions"
PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts"
TIMEOUT = 90.0

_cache: dict[str, str] = {}


class AiError(Exception):
    pass


def default_prompt(name: str) -> str:
    if name not in _cache:
        path = PROMPTS_DIR / f"{name}.md"
        _cache[name] = path.read_text(encoding="utf-8") if path.exists() else ""
    return _cache[name]


def prompt(name: str, overrides: dict | None = None, **fill) -> str:
    """Текст промпта: из настроек, если задан, иначе встроенный. {{ключ}} → значение."""
    text = ((overrides or {}).get(f"prompt.{name}") or "").strip() or default_prompt(name)
    for k, v in fill.items():
        text = text.replace("{{" + k + "}}", str(v))
    return text


def _extract_json(content: str) -> dict:
    s = (content or "").strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.S)
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end <= start:
        raise AiError(f"модель ответила не JSON: {s[:120]!r}")
    try:
        return json.loads(s[start:end + 1])
    except ValueError as e:
        raise AiError(f"JSON не разобрался: {e}; {s[:120]!r}")


class AiResult(dict):
    """Словарь ответа с полем cost — стоимость вызова в долларах."""
    cost: float = 0.0


async def chat_json(system: str, user: str, *, model: str | None = None,
                    max_tokens: int = 600, retries: int = 3) -> AiResult:
    if not settings.openrouter_key:
        raise AiError("Не задан OPENROUTER_KEY")
    body = {
        "model": model or settings.ai_model_cheap,
        "messages": [
            {"role": "system", "content": system + "\n\nОтвечай только одним JSON-объектом, без пояснений и без markdown."},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        "max_tokens": max_tokens,
        "usage": {"include": True},
    }
    headers = {
        "Authorization": f"Bearer {settings.openrouter_key}",
        "HTTP-Referer": "https://baza.local",
        "X-Title": "baza",
    }
    last = ""
    for attempt in range(retries):
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as cl:
                r = await cl.post(URL, json=body, headers=headers)
        except httpx.HTTPError as e:
            last = f"сеть: {e}"
            await asyncio.sleep(3 * (attempt + 1))
            continue
        if r.status_code in (429, 500, 502, 503, 529):
            last = f"OpenRouter {r.status_code}: {r.text[:200]}"
            await asyncio.sleep(5 * (attempt + 1))
            continue
        if r.status_code >= 400:
            raise AiError(f"OpenRouter {r.status_code}: {r.text[:300]}")
        data = r.json()
        if data.get("error"):
            raise AiError(str(data["error"])[:300])
        choices = data.get("choices") or []
        content = (choices[0].get("message") or {}).get("content") if choices else ""
        out = AiResult(_extract_json(content or ""))
        try:
            out.cost = float((data.get("usage") or {}).get("cost") or 0)
        except (TypeError, ValueError):
            out.cost = 0.0
        return out
    raise AiError(last or "OpenRouter не ответил")
