"""Квалификация комментариев: лид или мусор, с сутью.

Три ступени, от дешёвой к дорогой:
  1. правила — голый «+», кодовое слово поста, одни эмодзи, одно @упоминание: без ИИ;
  2. ИИ пачкой — до ai_batch_size комментариев одного поста в одном вызове: инструкция
     и разбор поста уходят один раз (замер 04.09: gemini-2.5-flash-lite пачкой — 98% согласия
     с haiku при $0.025 за тысячу против $0.91);
  3. лид сразу получает строку в lg_leads со снимком цен города; номер уже в базе — пробив не нужен.
Модель — настройка ai_model.comments, правится без деплоя.
"""
import asyncio
import json
import logging
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal
from ..models import IgAccount, LgCity, LgComment, LgLead, LgPost
from ..services.ai.client import chat_json, prompt
from ..services.outbound import queue_crm
from .common import add_ai_cost, ai_on, as_int, heartbeat, log_event, settings_all, utcnow

log = logging.getLogger("ai_comments")

POLL = 10
POSTS_PER_PASS = 8          # постов за проход; в каждом — до ai_batch_size комментариев
CONCURRENCY = 4
MAX_RETRIES = 3             # после стольких пустых ответов ИИ — «не лид», чтобы не крутиться вечно
BATCH_FORMAT = ("\n\nТебе дан список комментариев под одним постом, каждый с номером i. Верни JSON "
                "{\"items\": [{\"i\": номер, \"is_lead\": true, \"summary\": \"суть в 5–10 словах\"}, …]} — "
                "ровно по одному объекту на каждый номер, без пропусков.")
FORMAT = "\n\nФормат ответа: {\"is_lead\": true, \"summary\": \"…\", \"reason\": \"…\"}"   # для bench

_LETTERS = re.compile(r"[A-Za-zА-Яа-яЁё0-9]")
_MENTION_ONLY = re.compile(r"^(\s*@[\w.]+\s*)+$")
_PLUS_ONLY = re.compile(r"^[\s+＋]+$")


def rule(text: str, code_word: str | None) -> tuple[str, str] | None:
    """Решение без ИИ: (qualification, summary) или None, если нужна модель."""
    t = (text or "").strip()
    if not t:
        return "ignore", "пусто"
    if _PLUS_ONLY.match(t):
        return "lead", "плюс"
    if code_word:
        norm = re.sub(r"[^\w]+", "", t, flags=re.U).lower()
        if norm and norm == re.sub(r"[^\w]+", "", code_word, flags=re.U).lower():
            return "lead", f"кодовое слово {code_word.upper()}"
    if not _LETTERS.search(t):
        return "ignore", "только эмодзи или знаки"
    if _MENTION_ONLY.match(t):
        return "ignore", "только упоминание"
    return None


async def run():
    while True:
        try:
            async with SessionLocal() as db:
                if await ai_on(db):
                    await _pass(db)
                await _leads_for_late_cities(db)
                await heartbeat(db, "ai_comments")
        except Exception:
            log.exception("проход не удался")
        await asyncio.sleep(POLL)


async def _pass(db: AsyncSession) -> None:
    values = await settings_all(db)
    batch_size = max(5, min(40, as_int(values, "ai_batch_size", 20)))
    model = (values.get("ai_model.comments") or "").strip() or None
    post_ids = (await db.execute(
        select(LgComment.post_id).where(LgComment.qualification == "pending", LgComment.is_donor_reply.is_(False))
        .group_by(LgComment.post_id).order_by(LgComment.post_id).limit(POSTS_PER_PASS))).scalars().all()
    if not post_ids:
        return
    system = prompt("comment", values) + BATCH_FORMAT
    sem = asyncio.Semaphore(CONCURRENCY)
    total_rules = total_ai = leads = 0
    cost, failed = 0.0, 0

    for pid in post_ids:
        p = await db.get(LgPost, pid)
        comments = (await db.execute(
            select(LgComment).where(LgComment.post_id == pid, LgComment.qualification == "pending",
                                    LgComment.is_donor_reply.is_(False)).order_by(LgComment.id).limit(batch_size))).scalars().all()
        need_ai: list[LgComment] = []
        for c in comments:
            r = rule(c.text, p.code_word if p else None)
            if r:
                c.qualification, c.ai_summary, c.ai_reason, c.ai_at = r[0], r[1], "правило", utcnow()
                total_rules += 1
                if c.qualification == "lead" and await make_lead(db, c, p):
                    leads += 1
            else:
                need_ai.append(c)
        if not need_ai:
            continue
        user = json.dumps({
            "post": {"offer": p.offer, "hook": p.hook, "category": p.category, "cta": p.cta_type,
                     "code_word": p.code_word, "summary": p.ai_summary},
            "comments": [{"i": i + 1, "author": c.author_username, "text": (c.text or "")[:1000],
                          "days_after_post": c.age_distance_days} for i, c in enumerate(need_ai)],
        }, ensure_ascii=False)
        async with sem:
            try:
                r = await chat_json(system, user, model=model, max_tokens=70 * len(need_ai) + 150)
            except Exception as e:   # noqa: BLE001 — любой отказ ИИ считаем попыткой
                failed += 1
                log.warning("пост %s: %s", p.shortcode if p else pid, e)
                _note_retry(need_ai)
                continue
        cost += getattr(r, "cost", 0.0)
        got = {}
        for it in (r.get("items") or []):
            try:
                got[int(it.get("i"))] = it
            except (TypeError, ValueError):
                continue
        missing = []
        for i, c in enumerate(need_ai):
            it = got.get(i + 1)
            if not it or "is_lead" not in it:
                missing.append(c)
                continue
            c.qualification = "lead" if it.get("is_lead") else "ignore"
            c.ai_summary = (str(it.get("summary") or ""))[:300] or None
            c.ai_reason = None
            c.ai_at = utcnow()
            total_ai += 1
            if c.qualification == "lead" and await make_lead(db, c, p):
                leads += 1
        if missing:
            _note_retry(missing)
    await add_ai_cost(db, cost)
    await db.commit()
    if total_rules or total_ai or leads:
        log.info("правилами %d, ИИ %d, лидов %d, постов %d, $%.4f", total_rules, total_ai, leads, len(post_ids), cost)
    if failed == len(post_ids):
        await log_event(db, "ai.error", f"Квалификация: ИИ не ответила ни по одному из {failed} постов подряд", level="error")
        await db.commit()
        await asyncio.sleep(60)


def _note_retry(comments: list[LgComment]) -> None:
    """Пустой ответ по комментарию: считаем попытки в ai_reason, после MAX_RETRIES — «не лид»."""
    for c in comments:
        n = 0
        if c.ai_reason and c.ai_reason.startswith("retry:"):
            try:
                n = int(c.ai_reason.split(":", 1)[1])
            except ValueError:
                n = 0
        n += 1
        if n >= MAX_RETRIES:
            c.qualification, c.ai_summary, c.ai_reason, c.ai_at = "ignore", "ИИ не ответила", f"retry:{n} — сдались", utcnow()
        else:
            c.ai_reason = f"retry:{n}"


async def make_lead(db: AsyncSession, c: LgComment, p: LgPost) -> bool:
    """Лид из комментария. Без города — не создаём (вернётся позже, когда пост получит город)."""
    city_id = c.city_id or (p.city_id if p else None)
    if not city_id or not p:
        return False
    if (await db.execute(select(LgLead.id).where(LgLead.comment_id == c.id))).scalar():
        return False
    city = await db.get(LgCity, city_id)
    acc = None
    if c.author_account_id:
        acc = await db.get(IgAccount, c.author_account_id)
    if acc is None and c.author_username:
        acc = (await db.execute(select(IgAccount).where(IgAccount.username == c.author_username))).scalar_one_or_none()
        if acc is None:
            acc = IgAccount(username=c.author_username, roles="commenter")
            db.add(acc)
            await db.flush()
        c.author_account_id = acc.id
    if acc is None:
        return False
    lead = LgLead(comment_id=c.id, post_id=p.id, account_id=acc.id, city_id=city_id,
                  cost_contact=city.cost_per_contact if city else 0,
                  cost_handling=city.cost_per_handling if city else 0)
    if acc.phone:
        lead.phone, lead.phone_from, lead.probe_status, lead.probed_at = acc.phone, "base", "skipped", utcnow()
    db.add(lead)
    await db.flush()
    if lead.phone and city:
        await queue_crm(db, lead, city)
    return True


async def _leads_for_late_cities(db: AsyncSession) -> None:
    """Комментарии-лиды, оставшиеся без лида (пост тогда не имел города)."""
    rows = (await db.execute(
        select(LgComment, LgPost).join(LgPost, LgPost.id == LgComment.post_id)
        .outerjoin(LgLead, LgLead.comment_id == LgComment.id)
        .where(LgComment.qualification == "lead", LgLead.id.is_(None), LgPost.city_id.isnot(None))
        .limit(200))).all()
    for c, p in rows:
        c.city_id = c.city_id or p.city_id
        await make_lead(db, c, p)
    if rows:
        await db.commit()
