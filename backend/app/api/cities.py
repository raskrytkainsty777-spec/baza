"""Города — они же проекты. Список со счётчиками, создание, настройки города, выгрузки."""
import csv
import io
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import case, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db

from ..models import IgAccount, LgCity, LgDonor, LgLead, LgPost
from ..services.outbound import MSK_TZ
from ..workers.common import as_int, settings_all
from ..workers.common import city_sort_key
from .deps import require_token

router = APIRouter(prefix="/api/cities", tags=["cities"], dependencies=[Depends(require_token)])


class CityCreate(BaseModel):
    name: str


class CityPatch(BaseModel):
    name: str | None = None
    is_active: bool | None = None
    collect_posts: bool | None = None
    collect_comments: bool | None = None
    cost_per_contact: Decimal | None = None
    cost_per_handling: Decimal | None = None
    comment_fresh_days: int | None = None
    post_freeze_days: int | None = None
    donor_pause_days: int | None = None
    resend_after_days: int | None = None
    probe_mode: str | None = None
    probe_enabled: bool | None = None
    probe_hook_token: str | None = None
    crm_webhook_url: str | None = None
    crm_secret: str | None = None
    send_mode: str | None = None
    # ниша: пусто / 0 — общие значения
    posts_per_account: int | None = None
    intake_days: int | None = None
    prompt_post: str | None = None
    prompt_comment: str | None = None


# поля, которые можно очистить: null → вернуть общее значение
NULLABLE = ("crm_webhook_url", "crm_secret", "probe_hook_token",
            "posts_per_account", "intake_days", "prompt_post", "prompt_comment")


def _dto(c: LgCity, extra: dict | None = None) -> dict:
    d = {
        "id": c.id, "name": c.name, "is_active": c.is_active,
        "collect_posts": c.collect_posts, "collect_comments": c.collect_comments,
        "cost_per_contact": float(c.cost_per_contact or 0),
        "cost_per_handling": float(c.cost_per_handling or 0),
        "comment_fresh_days": c.comment_fresh_days, "post_freeze_days": c.post_freeze_days,
        "donor_pause_days": c.donor_pause_days, "resend_after_days": c.resend_after_days,
        "probe_mode": c.probe_mode, "probe_enabled": c.probe_enabled,
        "probe_hook_token_set": bool(c.probe_hook_token),
        "crm_webhook_url": c.crm_webhook_url or "", "crm_secret_set": bool(c.crm_secret),
        "send_mode": c.send_mode, "created_at": c.created_at,
        "posts_per_account": c.posts_per_account, "intake_days": c.intake_days,
        "prompt_post": c.prompt_post or "", "prompt_comment": c.prompt_comment or "",
    }
    if extra:
        d.update(extra)
    return d


async def _counts(db: AsyncSession) -> dict[int, dict]:
    """Счётчики по городам одним проходом: доноры по статусам, посты, лиды."""
    out: dict[int, dict] = {}
    rows = (await db.execute(
        select(LgDonor.city_id, LgDonor.status, func.count()).group_by(LgDonor.city_id, LgDonor.status)
    )).all()
    for city_id, status, n in rows:
        if city_id is None:
            continue
        out.setdefault(city_id, {})[f"donors_{status}"] = n
    rows = (await db.execute(
        select(LgPost.city_id,
               func.count(),
               func.count().filter(LgPost.monitor_status == "active"),
               func.count().filter(LgPost.is_selling.is_(True)))
        .group_by(LgPost.city_id)
    )).all()
    for city_id, total, active, selling in rows:
        if city_id is not None:
            out.setdefault(city_id, {}).update(posts=total, posts_active=active, posts_selling=selling)
    # первый сбор: продающие посты за окно, у которых комментарии ещё ни разу не забирали;
    # окно — своё у ниши, иначе общее
    default_days = as_int(await settings_all(db), "intake_days", 45)
    window = func.make_interval(0, 0, 0, func.coalesce(LgCity.intake_days, default_days))
    rows = (await db.execute(
        select(LgPost.city_id,
               func.count().filter(LgPost.last_collected_at.is_(None)),
               func.count().filter(LgPost.last_collected_at.isnot(None)),
               func.coalesce(func.sum(LgPost.collected_comments), 0))
        .join(LgCity, LgCity.id == LgPost.city_id)
        .where(LgPost.is_selling.is_(True), LgPost.monitor_status.in_(["active", "forced"]),
               LgPost.published_at >= func.now() - window)
        .group_by(LgPost.city_id))).all()
    for city_id, pending, collected, comments in rows:
        if city_id is not None:
            out.setdefault(city_id, {}).update(posts_pending_first=pending, posts_collected=collected, comments_collected=int(comments))
    rows = (await db.execute(
        select(LgLead.city_id,
               func.count(),
               func.count().filter(LgLead.probe_status.in_(["pending", "queued"])),
               func.count().filter(LgLead.phone.isnot(None)),
               func.count().filter(LgLead.outbound_status == "sent"))
        .group_by(LgLead.city_id)
    )).all()
    for city_id, total, unprobed, with_phone, sent in rows:
        out.setdefault(city_id, {}).update(leads=total, leads_unprobed=unprobed,
                                           leads_with_phone=with_phone, leads_sent=sent)
    return out


@router.get("")
async def list_cities(db: AsyncSession = Depends(get_db)):
    cities = sorted((await db.execute(select(LgCity))).scalars().all(), key=city_sort_key)
    counts = await _counts(db)
    unclassified = (await db.execute(
        select(func.count()).select_from(LgDonor).where(LgDonor.city_id.is_(None)))).scalar() or 0
    return {
        "cities": [_dto(c, {"donors_new": 0, "donors_monitored": 0, "donors_paused": 0,
                            "posts": 0, "posts_active": 0, "posts_selling": 0,
                            "leads": 0, "leads_unprobed": 0, "leads_with_phone": 0, "leads_sent": 0,
                            **counts.get(c.id, {})}) for c in cities],
        "unclassified_donors": unclassified,
    }


@router.post("", status_code=201)
async def create_city(body: CityCreate, db: AsyncSession = Depends(get_db)):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Название пустое")
    exists = (await db.execute(select(LgCity).where(func.lower(LgCity.name) == name.lower()))).scalar_one_or_none()
    if exists:
        raise HTTPException(409, f"Город «{exists.name}» уже есть")
    city = LgCity(name=name, is_active=True)
    db.add(city)
    await db.commit()
    await db.refresh(city)
    return _dto(city)


@router.get("/{city_id}")
async def get_city(city_id: int, db: AsyncSession = Depends(get_db)):
    city = await db.get(LgCity, city_id)
    if not city:
        raise HTTPException(404, "Город не найден")
    data = {k: 0 for k in ("donors_new", "donors_monitored", "donors_paused", "posts", "posts_selling",
                          "posts_active", "leads", "leads_unprobed", "leads_with_phone", "leads_sent",
                          "posts_pending_first", "posts_collected", "comments_collected")}
    data.update((await _counts(db)).get(city_id, {}))
    return _dto(city, data)


@router.patch("/{city_id}")
async def patch_city(city_id: int, body: CityPatch, db: AsyncSession = Depends(get_db)):
    city = await db.get(LgCity, city_id)
    if not city:
        raise HTTPException(404, "Город не найден")
    data = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None or k in NULLABLE}
    if "probe_mode" in data and data["probe_mode"] not in ("manual", "auto"):
        raise HTTPException(400, "probe_mode: manual | auto")
    if "send_mode" in data and data["send_mode"] not in ("manual", "auto"):
        raise HTTPException(400, "send_mode: manual | auto")
    for k in ("posts_per_account", "intake_days"):
        if k in data and data[k] is not None:
            if data[k] <= 0:
                data[k] = None                     # 0 — вернуть общее значение
            elif data[k] > (200 if k == "posts_per_account" else 365):
                raise HTTPException(400, f"{k}: слишком много")
    for k, v in data.items():
        if isinstance(v, str):
            v = v.strip()
            if k in ("prompt_post", "prompt_comment") and not v:
                v = None                           # пустой промпт — общий из Настроек
        setattr(city, k, v)
    await db.commit()
    await db.refresh(city)
    return _dto(city)


# ── выгрузки ─────────────────────────────────────────────────────────────────

def _csv(rows: list[list], header: list[str], filename: str) -> StreamingResponse:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(header)
    w.writerows(rows)
    data = ("﻿" + buf.getvalue()).encode("utf-8")      # BOM — чтобы Excel не ломал кириллицу
    return StreamingResponse(iter([data]), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename={filename}"})


def _msk(v, fmt: str = "%d.%m.%Y %H:%M") -> str:
    return v.astimezone(MSK_TZ).strftime(fmt) if v else ""


def _flat(s: str | None) -> str:
    return (s or "").replace("\r", " ").replace("\n", " ").strip()


@router.get("/{city_id}/posts.csv")
async def posts_csv(city_id: int, min_comments: int = 0, selling: bool = False, db: AsyncSession = Depends(get_db)):
    """Посты города: источник, ссылка, дата, число комментариев, продающий ли, оффер, описание."""
    if not await db.get(LgCity, city_id):
        raise HTTPException(404, "Город не найден")
    stmt = (select(LgPost, IgAccount.username).join(IgAccount, IgAccount.id == LgPost.account_id)
            .where(LgPost.city_id == city_id))
    if min_comments > 0:
        stmt = stmt.where(LgPost.comments_count >= min_comments)
    if selling:
        stmt = stmt.where(LgPost.is_selling.is_(True))
    rows = (await db.execute(stmt.order_by(LgPost.comments_count.desc(), LgPost.published_at.desc()))).all()
    out = [[u, p.url, _msk(p.published_at, "%d.%m.%Y"), p.comments_count or 0, p.collected_comments or 0,
            {True: "да", False: "нет"}.get(p.is_selling, "ждёт ИИ"), p.offer or "", p.category or "",
            p.code_word or "", _flat(p.caption)] for p, u in rows]
    return _csv(out, ["источник", "ссылка на пост", "дата поста", "комментариев", "собрано комментариев", "продающий",
                      "предложение", "категория", "кодовое слово", "описание поста"],
                f"posts_{city_id}{'_selling' if selling else ''}{f'_from{min_comments}' if min_comments else ''}.csv")


COMMENTERS_SQL = text("""
    WITH c AS (
        SELECT author_username AS u, text, written_at, qualification AS q, post_id
        FROM lg_comments
        WHERE city_id = :cid AND NOT is_donor_reply AND author_username <> ''
    ), agg AS (
        SELECT u, count(*) AS n, count(DISTINCT post_id) AS posts,
               bool_or(q = 'lead') AS lead, bool_or(q = 'pending') AS pending,
               min(written_at) AS first_at, max(written_at) AS last_at
        FROM c GROUP BY u
    ), pick AS (
        -- показываем самый говорящий комментарий: лид, если был, иначе последний
        SELECT DISTINCT ON (u) u, text, post_id FROM c
        ORDER BY u, (q = 'lead') DESC, written_at DESC NULLS LAST
    )
    SELECT agg.u, agg.n, agg.posts, agg.lead, agg.pending, agg.first_at, agg.last_at,
           pick.text, p.url, a.username AS donor
    FROM agg JOIN pick USING (u)
    JOIN lg_posts p ON p.id = pick.post_id
    JOIN ig_accounts a ON a.id = p.account_id
    ORDER BY agg.lead DESC, agg.n DESC, agg.u
""")


@router.get("/{city_id}/commenters.csv")
async def commenters_csv(city_id: int, db: AsyncSession = Depends(get_db)):
    """Уникальные комментаторы города (без ответов самих доноров) с оценкой интереса ИИ."""
    if not await db.get(LgCity, city_id):
        raise HTTPException(404, "Город не найден")
    rows = (await db.execute(COMMENTERS_SQL, {"cid": city_id})).all()
    out = [[u, f"https://instagram.com/{u}", n, posts, "да" if lead else ("ждёт ИИ" if pending else "нет"),
            _msk(first_at), _msk(last_at), _flat(txt), url, donor]
           for u, n, posts, lead, pending, first_at, last_at, txt, url, donor in rows]
    return _csv(out, ["логин", "профиль", "комментариев", "постов", "интерес по ИИ", "первый комментарий",
                      "последний комментарий", "комментарий", "пост", "источник"], f"commenters_{city_id}.csv")
