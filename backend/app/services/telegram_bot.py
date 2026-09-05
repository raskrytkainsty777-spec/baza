"""Telegram-бот клиентов ГЦК: токен в настройках (cab_telegram_bot_token), запасной — в .env."""
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..workers.common import settings_all

_ME: dict = {"token": None, "username": None}


async def bot_token(db: AsyncSession) -> str:
    v = await settings_all(db)
    return (v.get("cab_telegram_bot_token") or "").strip() or (settings.cab_telegram_bot_token or "").strip()


async def api(token: str, method: str, **params):
    async with httpx.AsyncClient(timeout=35) as cl:
        r = await cl.post(f"https://api.telegram.org/bot{token}/{method}", json=params)
    d = r.json()
    if not d.get("ok"):
        raise RuntimeError(f"Telegram {method}: {d.get('description') or r.status_code}")
    return d.get("result")


async def bot_username(token: str) -> str | None:
    if not token:
        return None
    if _ME["token"] == token and _ME["username"]:
        return _ME["username"]
    try:
        me = await api(token, "getMe")
    except Exception:   # noqa: BLE001
        return None
    _ME.update(token=token, username=me.get("username"))
    return _ME["username"]


async def send(token: str, chat_id: str, text: str) -> None:
    await api(token, "sendMessage", chat_id=chat_id, text=text, parse_mode="HTML", disable_web_page_preview=True)
