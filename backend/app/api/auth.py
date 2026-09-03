"""Проверка токена для формы входа."""
from fastapi import APIRouter, Depends

from .deps import require_token

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/me")
async def me(_: str = Depends(require_token)):
    return {"ok": True, "role": "admin"}
