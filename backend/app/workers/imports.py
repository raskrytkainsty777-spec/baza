"""Разбор выгрузок parser.im и Apify в наши таблицы.

Каждая функция получает задание и строки, кладёт то, чего ещё нет, и возвращает
число новых строк. Повторный импорт того же результата ничего не дублирует:
кандидаты уникальны в задаче, посты — по shortcode, комментарии — по id.
"""
import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    IgAccount, LgCandidate, LgCity, LgComment, LgDonor, LgJob, LgPost, LgReject, LgSearchTask,
)
from ..services.parserim.client import parse_date, unescape_url
from .common import chunks, log_event, norm_login, shortcode_of, to_int, utcnow

log = logging.getLogger(__name__)


# ── кандидаты ────────────────────────────────────────────────────────────────

async def known_usernames(db: AsyncSession) -> set[str]:
    """Кого не надо заводить кандидатом: уже доноры и уже отклонённые."""
    donors = (await db.execute(
        select(IgAccount.username).join(LgDonor, LgDonor.account_id == IgAccount.id))).scalars().all()
    rejects = (await db.execute(select(LgReject.username))).scalars().all()
    return {u.lower() for u in donors} | {u.lower() for u in rejects}


async def add_candidates(db: AsyncSession, task: LgSearchTask, entries: list[dict]) -> int:
    """entries: {username, ig_id?, found_by?}. Возвращает число новых кандидатов."""
    known = await known_usernames(db)
    seen: dict[str, dict] = {}
    for e in entries:
        u = norm_login(e.get("username") or "")
        if not u and e.get("ig_id"):
            # web-сбор parser.im отдаёт только id; логин подтянет f1 по id задания-источника
            u = f"id:{str(e['ig_id']).strip()}"
        if u and u not in known and u not in seen:
            seen[u] = e
    inserted = 0
    for batch in chunks(seen.items(), 500):
        stmt = insert(LgCandidate).values([{
            "task_id": task.id, "username": u, "ig_id": (e.get("ig_id") or None),
            "found_by": (e.get("found_by") or "")[:200] or None, "state": "collected",
        } for u, e in batch]).on_conflict_do_nothing(index_elements=["task_id", "username"]).returning(LgCandidate.id)
        inserted += len((await db.execute(stmt)).scalars().all())
    task.collected = (await db.execute(
        select(func.count()).select_from(LgCandidate).where(LgCandidate.task_id == task.id))).scalar() or 0
    return inserted


async def import_search(db: AsyncSession, job: LgJob, rows: list[dict]) -> int:
    task = await db.get(LgSearchTask, job.search_task_id) if job.search_task_id else None
    if not task:
        return 0
    entries = [{"username": r.get("login") or r.get("username") or "", "ig_id": r.get("id"),
                "found_by": r.get("source")} for r in rows]
    return await add_candidates(db, task, entries)


F1_MAP = {
    "login": "username", "username": "username", "id": "ig_id",
    "name": "full_name", "full_name": "full_name", "fullname": "full_name", "full name": "full_name",
    "city": "address", "address": "address", "city_address": "address", "location": "address", "city address": "address",
    "description": "bio", "bio": "bio", "biography": "bio", "desc": "bio",
    "followers": "followers", "subscribers": "followers", "followers_count": "followers", "fol_cnt": "followers",
    "posts": "posts_count", "media": "posts_count", "posts_count": "posts_count", "publications": "posts_count", "post_cnt": "posts_count",
    "last_post": "last_post_at", "lastpost": "last_post_at", "last_post_date": "last_post_at",
    "date_last_post": "last_post_at", "last_publication": "last_post_at", "last post": "last_post_at", "post_date": "last_post_at",
}


async def import_filter(db: AsyncSession, job: LgJob, rows: list[dict]) -> int:
    """f1: догрузить данные кандидатам, прошедшим фильтр. Не прошедших отсеет discovery,
    когда закончатся все f1-задания задачи."""
    if not job.search_task_id or not rows:
        return 0
    keys = list(rows[0].keys())
    unknown = [k for k in keys if k.strip().lower() not in F1_MAP and k.strip().lower() != "source"]
    if unknown:
        await log_event(db, "parserim.f1_header", f"f1: незнакомые колонки {unknown} — заголовок {keys}",
                        entity="job", entity_id=job.id, level="warn", payload={"header": keys})
    all_cands = (await db.execute(select(LgCandidate).where(LgCandidate.task_id == job.search_task_id))).scalars().all()
    cands = {c.username.lower(): c for c in all_cands}
    by_id = {str(c.ig_id): c for c in all_cands if c.ig_id}
    taken = {c.username.lower() for c in all_cands}
    n = 0
    for r in rows:
        mapped = {}
        for k, v in r.items():
            f = F1_MAP.get((k or "").strip().lower())
            if f:
                mapped[f] = v
        u = norm_login(mapped.get("username") or "")
        c = cands.get(u) or by_id.get(str(mapped.get("ig_id") or "").strip())
        if not c:
            continue
        c.ig_id = c.ig_id or (mapped.get("ig_id") or None)
        if u and c.username.startswith("id:") and u not in taken:
            c.username = u          # заглушка id:<id> → настоящий логин
            taken.add(u)
        c.full_name = (mapped.get("full_name") or "")[:300] or c.full_name
        c.bio = mapped.get("bio") or c.bio
        addr = (mapped.get("address") or "").strip()
        c.address = (addr[:300] if addr and addr != "0" else None) or c.address   # parser.im: пустой город = "0"
        c.followers = to_int(mapped.get("followers")) if mapped.get("followers") is not None else c.followers
        c.posts_count = to_int(mapped.get("posts_count")) if mapped.get("posts_count") is not None else c.posts_count
        if mapped.get("last_post_at"):
            c.last_post_at = parse_date(mapped["last_post_at"]) or c.last_post_at
        if c.state == "collected":
            c.state = "filtered"
        n += 1
    return n


# ── посты ────────────────────────────────────────────────────────────────────

async def import_posts(db: AsyncSession, job: LgJob, rows: list[dict], intake_days: int) -> int:
    """p1: посты доноров из задания. Окно intake_days режем здесь — parser.im отдаёт
    закреплённые старые посты первыми."""
    payload = job.payload or {}
    donor_ids = payload.get("donor_ids") or []
    if not donor_ids:
        return 0
    donors = {}
    for d, username in (await db.execute(
            select(LgDonor, IgAccount.username).join(IgAccount, IgAccount.id == LgDonor.account_id)
            .where(LgDonor.id.in_(donor_ids)))).all():
        donors[username.lower()] = d
    since = utcnow() - timedelta(days=intake_days)
    inserted = skipped_old = 0
    for r in rows:
        d = donors.get(norm_login(r.get("source") or ""))
        url = unescape_url(r.get("post_url") or "")
        sc = shortcode_of(url)
        if not d or not sc:
            continue
        published = parse_date(r.get("post_date") or "")
        if published and published < since:
            skipped_old += 1
            continue
        cc = to_int(r.get("post_comment")) or 0
        stmt = insert(LgPost).values(
            shortcode=sc, ig_post_id=(r.get("post_id") or None), donor_id=d.id, account_id=d.account_id,
            city_id=d.city_id, city_source="donor" if d.city_id else None, url=url[:300],
            caption=(r.get("post_text") or "").strip() or None, published_at=published,
            product_type="clips" if "/reel" in url else "feed", likes=to_int(r.get("post_likes")),
            comments_count=cc, comments_count_prev=cc, comments_delta=0,
        ).on_conflict_do_nothing(index_elements=["shortcode"]).returning(LgPost.id)
        if (await db.execute(stmt)).scalar():
            inserted += 1
    if skipped_old:
        log.info("posts job %s: %d постов старше %d дн пропущено", job.id, skipped_old, intake_days)
    return inserted


async def import_apify_posts(db: AsyncSession, job: LgJob, items: list[dict], donors_by_username: dict) -> int:
    """Новые посты из apify/instagram-scraper (resultsType=posts)."""
    inserted = 0
    for it in items:
        sc = it.get("shortCode") or shortcode_of(it.get("url") or "")
        owner = norm_login(it.get("ownerUsername") or "")
        d = donors_by_username.get(owner)
        if not sc or not d:
            continue
        published = None
        ts = it.get("timestamp")
        if ts:
            try:
                published = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            except ValueError:
                published = None
        cc = to_int(it.get("commentsCount")) or 0
        ptype = (it.get("productType") or it.get("type") or "").lower()
        stmt = insert(LgPost).values(
            shortcode=sc, ig_post_id=str(it.get("id") or "") or None, donor_id=d.id, account_id=d.account_id,
            city_id=d.city_id, city_source="donor" if d.city_id else None,
            url=(it.get("url") or f"https://www.instagram.com/p/{sc}/")[:300],
            caption=(it.get("caption") or "").strip() or None, published_at=published,
            product_type="clips" if "clip" in ptype or "video" in ptype else ("carousel" if "sidecar" in ptype else "feed"),
            views=to_int(it.get("videoViewCount") or it.get("videoPlayCount")), likes=to_int(it.get("likesCount")),
            comments_count=cc, comments_count_prev=0, comments_delta=cc,
        ).on_conflict_do_nothing(index_elements=["shortcode"]).returning(LgPost.id)
        if (await db.execute(stmt)).scalar():
            inserted += 1
    return inserted


# ── комментарии ──────────────────────────────────────────────────────────────

async def fresh_days_by_city(db: AsyncSession, default_days: int) -> dict[int | None, int]:
    out: dict[int | None, int] = {None: default_days}
    for c in (await db.execute(select(LgCity))).scalars().all():
        out[c.id] = c.comment_fresh_days or default_days
    return out


async def insert_comments(db: AsyncSession, job: LgJob | None, entries: list[dict],
                          default_fresh_days: int, source: str) -> int:
    """entries: {post: LgPost, cid, author, author_ig_id, text, written: datetime|None}.
    Старше окна свежести города — не храним; ответы донора — храним, но в ИИ не отдаём."""
    if not entries:
        return 0
    fresh = await fresh_days_by_city(db, default_fresh_days)
    account_ids = {e["post"].account_id for e in entries}
    donor_logins = {aid: u.lower() for aid, u in (await db.execute(
        select(IgAccount.id, IgAccount.username).where(IgAccount.id.in_(account_ids)))).all()}
    now = utcnow()
    rows, seen = [], set()
    for e in entries:
        p, cid = e["post"], (e.get("cid") or "").strip()
        if not cid or cid in seen:
            continue
        written = e.get("written")
        if written and written < now - timedelta(days=fresh.get(p.city_id, default_fresh_days)):
            continue
        seen.add(cid)
        author = norm_login(e.get("author") or "")
        is_reply = bool(author) and author == donor_logins.get(p.account_id)
        txt = (e.get("text") or "").strip()
        rows.append({
            "ig_comment_id": cid[:40], "post_id": p.id, "city_id": p.city_id,
            "author_username": author[:150], "author_ig_id": (str(e.get("author_ig_id") or "")[:40] or None),
            "text": txt or None, "written_at": written, "source": source, "job_id": job.id if job else None,
            "is_donor_reply": is_reply,
            "qualification": "prefiltered" if (is_reply or not txt) else "pending",
            "age_distance_days": (written - p.published_at).days if (written and p.published_at) else None,
        })
    inserted = 0
    for batch in chunks(rows, 1000):
        stmt = insert(LgComment).values(batch).on_conflict_do_nothing(
            index_elements=["ig_comment_id"]).returning(LgComment.id)
        inserted += len((await db.execute(stmt)).scalars().all())
    # авторы — в справочник аккаунтов (комментаторы), потом привязка id одним UPDATE
    authors = sorted({r["author_username"] for r in rows if r["author_username"] and not r["is_donor_reply"]})
    for batch in chunks(authors, 1000):
        await db.execute(insert(IgAccount).values([{"username": u, "roles": "commenter"} for u in batch])
                         .on_conflict_do_nothing(index_elements=["username"]))
    post_ids = sorted({e["post"].id for e in entries})
    await db.execute(text("""
        UPDATE lg_comments c SET author_account_id = a.id
        FROM ig_accounts a
        WHERE c.author_account_id IS NULL AND c.post_id = ANY(:ids) AND c.author_username = a.username
    """), {"ids": post_ids})
    return inserted


async def mark_posts_collected(db: AsyncSession, post_ids: list[int]) -> None:
    """После сбора: счётчик «сколько у нас есть», прирост обнулён — он был выбран."""
    if not post_ids:
        return
    now = utcnow()
    counts = dict((await db.execute(
        select(LgComment.post_id, func.count()).where(LgComment.post_id.in_(post_ids))
        .group_by(LgComment.post_id))).all())
    for pid in post_ids:
        await db.execute(update(LgPost).where(LgPost.id == pid).values(
            collected_comments=counts.get(pid, 0), last_collected_at=now,
            comments_count_prev=LgPost.comments_count, comments_delta=0))


async def import_comments(db: AsyncSession, job: LgJob, rows: list[dict], default_fresh_days: int) -> int:
    """p2: строки `source:id:login:id_comment:text_comment:date_comment`."""
    payload = job.payload or {}
    post_ids = payload.get("post_ids") or []
    if not post_ids:
        return 0
    posts = {p.shortcode: p for p in (await db.execute(select(LgPost).where(LgPost.id.in_(post_ids)))).scalars().all()}
    entries = []
    for r in rows:
        p = posts.get(shortcode_of(unescape_url(r.get("source") or "")) or "")
        if not p:
            continue
        entries.append({"post": p, "cid": r.get("id_comment"), "author": r.get("login"),
                        "author_ig_id": r.get("id"), "text": r.get("text_comment"),
                        "written": parse_date(r.get("date_comment") or "")})
    n = await insert_comments(db, job, entries, default_fresh_days, "parserim")
    await mark_posts_collected(db, post_ids)
    return n


def apify_comment_entries(post: LgPost, items: list[dict]) -> list[dict]:
    """apify/instagram-comment-scraper → наши записи (ответы разворачиваем)."""
    out = []
    flat = []
    for c in items:
        flat.append(c)
        flat.extend(c.get("replies") or [])
    for c in flat:
        cid = c.get("id")
        if not cid:
            continue
        written = None
        ts = c.get("timestamp")
        if ts:
            try:
                written = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            except ValueError:
                written = None
        owner = c.get("owner") or {}
        out.append({"post": post, "cid": str(cid), "author": c.get("ownerUsername") or owner.get("username"),
                    "author_ig_id": owner.get("id") or c.get("ownerId"), "text": c.get("text"), "written": written})
    return out
