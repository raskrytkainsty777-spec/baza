"""Контрольный прогон моделей на наших задачах — чтобы выбирать модель по цифрам, а не по памяти.

Три задачи, живые данные из базы:
  comments — квалификация комментариев; эталон — решения текущей модели (ai_at не пуст);
  posts    — разметка постов; эталон — ручная разметка заказчика из xlsx (ai_summary пуст: ИИ их не трогала);
  cands    — «кто и где» по кандидатам после f1; эталона нет, сравниваем модели между собой.
Для комментариев ещё и режим «пачкой»: 20 комментариев одного поста в одном вызове.

Запуск на сервере:
  cd /opt/baza/backend && set -a && . ../.env && set +a
  .venv/bin/python -m app.services.ai.bench --models deepseek/deepseek-v4-flash,google/gemini-2.5-flash-lite \
      --comments 300 --posts 60 --cands 60 --out /tmp/bench.json
  --dry — только выборка, без вызовов.
"""
import argparse
import asyncio
import json
import random
import statistics
import time

from sqlalchemy import text

from ...db import SessionLocal
from ...workers.ai_comments import FORMAT as COMMENT_FORMAT
from ...workers.ai_posts import FORMAT as POST_FORMAT
from .client import AiError, chat_json, prompt

DEFAULT_MODELS = [
    "anthropic/claude-haiku-4.5",
    "deepseek/deepseek-v4-flash",
    "google/gemini-2.5-flash-lite",
    "openai/gpt-5-nano",
    "qwen/qwen3-235b-a22b-2507",
    "openai/gpt-5-mini",
]
CONCURRENCY = 6
BATCH_SIZE = 20
CAND_FORMAT = ("\n\nФормат ответа: {\"activity_kind\": \"…\", \"ok\": true, \"city\": \"…\" или null, "
               "\"confidence\": 0.0, \"reason\": \"коротко почему\"}")
BATCH_HINT = ("\n\nТебе дан список комментариев под одним постом, каждый с номером i. Верни JSON "
              "{\"items\": [{\"i\": номер, \"is_lead\": true, \"summary\": \"…\"}, …]} — ровно по одному объекту "
              "на каждый номер, без пропусков и без лишних.")


def _trivial(t: str) -> bool:
    t = (t or "").strip()
    return len(t) <= 3 or not any(ch.isalpha() for ch in t)


# ── выборки ──────────────────────────────────────────────────────────────────

async def sample_comments(n: int, seed: int) -> list[dict]:
    async with SessionLocal() as db:
        rows = (await db.execute(text("""
            select c.id, c.text, c.qualification, c.ai_summary, c.author_username, c.age_distance_days, c.post_id,
                   p.offer, p.hook, p.category, p.cta_type, p.code_word, p.ai_summary as post_summary
            from lg_comments c join lg_posts p on p.id = c.post_id
            where c.ai_at is not null and c.is_donor_reply = false and c.qualification in ('lead', 'ignore')
              and c.text is not null
        """))).mappings().all()
    rnd = random.Random(seed)
    rows = [dict(r) for r in rows]
    rnd.shuffle(rows)
    triv = [r for r in rows if _trivial(r["text"])]
    rest = [r for r in rows if not _trivial(r["text"])]
    leads = [r for r in rest if r["qualification"] == "lead"]
    ignores = [r for r in rest if r["qualification"] == "ignore"]
    n_triv = min(len(triv), max(10, n // 8))
    half = (n - n_triv) // 2
    out = triv[:n_triv] + leads[:half] + ignores[:half]
    # добираем, если одной из групп не хватило
    used = {r["id"] for r in out}
    for r in rest:
        if len(out) >= n:
            break
        if r["id"] not in used:
            out.append(r); used.add(r["id"])
    rnd.shuffle(out)
    return out[:n]


async def sample_posts(n: int, seed: int) -> list[dict]:
    async with SessionLocal() as db:
        rows = (await db.execute(text("""
            select id, shortcode, caption, is_selling, category, cta_type, code_word, hook, offer
            from lg_posts where ai_summary is null and caption is not null and length(caption) > 80
        """))).mappings().all()
    rnd = random.Random(seed)
    rows = [dict(r) for r in rows]
    rnd.shuffle(rows)
    sell = [r for r in rows if r["is_selling"]]
    non = [r for r in rows if not r["is_selling"]]
    k_non = min(len(non), n // 3)
    return (sell[: n - k_non] + non[:k_non])[:n]


async def sample_cands(n: int, seed: int) -> list[dict]:
    async with SessionLocal() as db:
        rows = (await db.execute(text("""
            select id, username, full_name, bio, address, followers, posts_count, last_post_at
            from lg_candidates where state in ('filtered', 'classified', 'unclear', 'distributed')
              and (bio is not null or full_name is not null)
        """))).mappings().all()
        cities = (await db.execute(text("select name from lg_cities order by name"))).scalars().all()
    rnd = random.Random(seed)
    rows = [dict(r) for r in rows]
    rnd.shuffle(rows)
    return rows[:n], ", ".join(cities)


# ── вызовы ───────────────────────────────────────────────────────────────────

async def _timed(coro):
    t0 = time.perf_counter()
    try:
        r = await coro
        return r, time.perf_counter() - t0, None
    except (AiError, Exception) as e:   # noqa: BLE001 — нам важен факт отказа, не тип
        return None, time.perf_counter() - t0, f"{type(e).__name__}: {str(e)[:160]}"


async def run_comments(model: str, items: list[dict], values: dict) -> dict:
    system = prompt("comment", values) + COMMENT_FORMAT
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(c):
        user = json.dumps({
            "post": {"offer": c["offer"], "hook": c["hook"], "category": c["category"], "cta": c["cta_type"],
                     "code_word": c["code_word"], "summary": c["post_summary"]},
            "comment": {"author": c["author_username"], "text": (c["text"] or "")[:1500],
                        "days_after_post": c["age_distance_days"]},
        }, ensure_ascii=False)
        async with sem:
            return await _timed(chat_json(system, user, model=model, max_tokens=300, retries=2))

    res = await asyncio.gather(*(one(c) for c in items))
    return _score_comments(items, [(r.get("is_lead") if r else None, r.get("summary") if r else None, lat, err,
                                    getattr(r, "cost", 0.0) if r else 0.0) for r, lat, err in res])


async def run_comments_batch(model: str, items: list[dict], values: dict) -> dict:
    system = prompt("comment", values) + BATCH_HINT
    by_post: dict[int, list[dict]] = {}
    for c in items:
        by_post.setdefault(c["post_id"], []).append(c)
    chunks = []
    for pid, cs in by_post.items():
        for i in range(0, len(cs), BATCH_SIZE):
            chunks.append(cs[i:i + BATCH_SIZE])
    sem = asyncio.Semaphore(CONCURRENCY)
    answers: dict[int, tuple] = {}

    async def one(chunk):
        p = chunk[0]
        user = json.dumps({
            "post": {"offer": p["offer"], "hook": p["hook"], "category": p["category"], "cta": p["cta_type"],
                     "code_word": p["code_word"], "summary": p["post_summary"]},
            "comments": [{"i": i + 1, "author": c["author_username"], "text": (c["text"] or "")[:1000],
                          "days_after_post": c["age_distance_days"]} for i, c in enumerate(chunk)],
        }, ensure_ascii=False)
        async with sem:
            r, lat, err = await _timed(chat_json(system, user, model=model, max_tokens=70 * len(chunk) + 120, retries=2))
        got = {}
        if r:
            for it in (r.get("items") or []):
                try:
                    got[int(it.get("i"))] = it
                except (TypeError, ValueError):
                    continue
        per_cost = (getattr(r, "cost", 0.0) if r else 0.0) / max(len(chunk), 1)
        for i, c in enumerate(chunk):
            it = got.get(i + 1)
            answers[c["id"]] = (it.get("is_lead") if it else None, it.get("summary") if it else None,
                                lat / len(chunk), err or (None if it else "нет ответа по номеру"), per_cost)

    await asyncio.gather(*(one(ch) for ch in chunks))
    return _score_comments(items, [answers.get(c["id"], (None, None, 0.0, "нет ответа", 0.0)) for c in items],
                           calls=len(chunks))


def _score_comments(items, outs, calls: int | None = None) -> dict:
    n = len(items)
    answered = agree = tp = fp = fn = 0
    fails = []
    lat, cost = [], 0.0
    disagreements = []
    for c, (is_lead, summary, latency, err, cst) in zip(items, outs):
        lat.append(latency); cost += cst or 0.0
        if err or is_lead is None:
            fails.append(err or "пусто")
            continue
        answered += 1
        ref = c["qualification"] == "lead"
        got = bool(is_lead)
        if got == ref:
            agree += 1
        else:
            disagreements.append({"text": (c["text"] or "")[:90], "ref": "lead" if ref else "ignore",
                                  "got": "lead" if got else "ignore", "summary": (summary or "")[:60],
                                  "post": (c["post_summary"] or c["offer"] or "")[:60]})
        tp += got and ref; fp += got and not ref; fn += (not got) and ref
    return {
        "n": n, "answered": answered, "agreement": agree / answered if answered else 0.0,
        "lead_precision": tp / (tp + fp) if (tp + fp) else 0.0, "lead_recall": tp / (tp + fn) if (tp + fn) else 0.0,
        "fails": len(fails), "fail_examples": fails[:3], "latency_avg": statistics.mean(lat) if lat else 0.0,
        "cost": cost, "cost_per_1000": cost / n * 1000 if n else 0.0, "calls": calls or n,
        "disagreements": disagreements[:14],
    }


async def run_posts(model: str, items: list[dict], values: dict) -> dict:
    system = prompt("post", values) + (POST_FORMAT % "")
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(p):
        user = json.dumps({"caption": p["caption"][:6000]}, ensure_ascii=False)
        async with sem:
            return await _timed(chat_json(system, user, model=model, max_tokens=600, retries=2))

    res = await asyncio.gather(*(one(p) for p in items))
    n = len(items); answered = sell_ok = cat_ok = cw_ok = cw_n = 0
    fails, lat, cost, dis = [], [], 0.0, []
    for p, (r, latency, err) in zip(items, res):
        lat.append(latency)
        if not r:
            fails.append(err or "пусто"); continue
        cost += getattr(r, "cost", 0.0); answered += 1
        ref_sell = bool(p["is_selling"]); got_sell = bool(r.get("is_selling"))
        if ref_sell == got_sell:
            sell_ok += 1
        else:
            dis.append({"shortcode": p["shortcode"], "ref": ref_sell, "got": got_sell, "caption": (p["caption"] or "")[:80],
                        "got_offer": (str(r.get("offer") or ""))[:60]})
        if ref_sell and got_sell:
            if (str(r.get("category") or "").strip().lower() == (p["category"] or "").strip().lower()):
                cat_ok += 1
        if p["code_word"]:
            cw_n += 1
            if str(r.get("code_word") or "").strip().upper() == p["code_word"].strip().upper():
                cw_ok += 1
    sell_both = sum(1 for p, (r, _, _) in zip(items, res) if r and p["is_selling"] and r.get("is_selling"))
    return {"n": n, "answered": answered, "selling_agreement": sell_ok / answered if answered else 0.0,
            "category_agreement": cat_ok / sell_both if sell_both else 0.0,
            "code_word_agreement": cw_ok / cw_n if cw_n else None,
            "fails": len(fails), "fail_examples": fails[:3], "latency_avg": statistics.mean(lat) if lat else 0.0,
            "cost": cost, "cost_per_1000": cost / n * 1000 if n else 0.0, "disagreements": dis[:10]}


async def run_cands(model: str, items: list[dict], cities: str, values: dict) -> dict:
    system = prompt("activity", values) + "\n\n" + prompt("city", values, cities=cities) + CAND_FORMAT
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(c):
        user = json.dumps({"username": c["username"], "full_name": c["full_name"], "bio": c["bio"], "address": c["address"],
                           "followers": c["followers"], "posts": c["posts_count"],
                           "last_post": c["last_post_at"].isoformat() if c["last_post_at"] else None}, ensure_ascii=False)
        async with sem:
            return await _timed(chat_json(system, user, model=model, max_tokens=300, retries=2))

    res = await asyncio.gather(*(one(c) for c in items))
    out, fails, lat, cost = {}, [], [], 0.0
    for c, (r, latency, err) in zip(items, res):
        lat.append(latency)
        if not r:
            fails.append(err or "пусто"); continue
        cost += getattr(r, "cost", 0.0)
        try:
            conf = float(r.get("confidence") or 0)
        except (TypeError, ValueError):
            conf = 0.0
        out[c["id"]] = {"ok": bool(r.get("ok")), "kind": str(r.get("activity_kind") or ""),
                        "city": (str(r.get("city") or "")).strip().lower() or None, "conf": conf}
    return {"n": len(items), "answered": len(out), "fails": len(fails), "fail_examples": fails[:3],
            "latency_avg": statistics.mean(lat) if lat else 0.0, "cost": cost,
            "cost_per_1000": cost / len(items) * 1000 if items else 0.0, "answers": out}


def _cands_agreement(a: dict, b: dict) -> tuple[float, float, int]:
    ids = [i for i in a if i in b]
    if not ids:
        return 0.0, 0.0, 0
    ok = sum(1 for i in ids if a[i]["ok"] == b[i]["ok"]) / len(ids)
    city = sum(1 for i in ids if a[i]["city"] == b[i]["city"]) / len(ids)
    return ok, city, len(ids)


# ── отчёт ────────────────────────────────────────────────────────────────────

def _pct(v):
    return "—" if v is None else f"{v * 100:5.1f}%"


def report(results: dict, items_cands: list[dict]) -> str:
    lines = []
    if "comments" in results:
        lines += ["", "КОММЕНТАРИИ — согласие с эталоном (haiku), лиды: точность / полнота", "",
                  f"{'модель':44s} {'режим':7s} {'согл.':>7s} {'точн.':>7s} {'полн.':>7s} {'сбои':>5s} {'сек':>5s} {'$/1000':>8s}"]
        for (model, mode), r in results["comments"].items():
            lines.append(f"{model:44s} {mode:7s} {_pct(r['agreement']):>7s} {_pct(r['lead_precision']):>7s} "
                         f"{_pct(r['lead_recall']):>7s} {r['fails']:5d} {r['latency_avg']:5.1f} {r['cost_per_1000']:8.3f}")
    if "posts" in results:
        lines += ["", "ПОСТЫ — согласие с ручной разметкой заказчика", "",
                  f"{'модель':44s} {'продающ.':>9s} {'категория':>10s} {'кодслово':>9s} {'сбои':>5s} {'сек':>5s} {'$/1000':>8s}"]
        for model, r in results["posts"].items():
            lines.append(f"{model:44s} {_pct(r['selling_agreement']):>9s} {_pct(r['category_agreement']):>10s} "
                         f"{_pct(r['code_word_agreement']):>9s} {r['fails']:5d} {r['latency_avg']:5.1f} {r['cost_per_1000']:8.3f}")
    if "cands" in results:
        base_model = next(iter(results["cands"]))
        base = results["cands"][base_model]["answers"]
        lines += ["", f"КАНДИДАТЫ «кто и где» — согласие с {base_model}", "",
                  f"{'модель':44s} {'деятельн.':>10s} {'город':>7s} {'сбои':>5s} {'сек':>5s} {'$/1000':>8s}"]
        for model, r in results["cands"].items():
            ok, city, _ = _cands_agreement(base, r["answers"])
            lines.append(f"{model:44s} {_pct(ok):>10s} {_pct(city):>7s} {r['fails']:5d} {r['latency_avg']:5.1f} {r['cost_per_1000']:8.3f}")
    return "\n".join(lines)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS))
    ap.add_argument("--comments", type=int, default=300)
    ap.add_argument("--posts", type=int, default=60)
    ap.add_argument("--cands", type=int, default=60)
    ap.add_argument("--no-batch", action="store_true")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="/tmp/bench.json")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    models = [m.strip() for m in a.models.split(",") if m.strip()]

    from ...api.settings import get_all
    async with SessionLocal() as db:
        values = await get_all(db)
    comments = await sample_comments(a.comments, a.seed) if a.comments else []
    posts = await sample_posts(a.posts, a.seed) if a.posts else []
    cands, cities = (await sample_cands(a.cands, a.seed)) if a.cands else ([], "")
    print(f"выборка: комментариев {len(comments)} (лидов по эталону {sum(1 for c in comments if c['qualification'] == 'lead')}, "
          f"тривиальных {sum(1 for c in comments if _trivial(c['text']))}), постов {len(posts)} "
          f"(продающих {sum(1 for p in posts if p['is_selling'])}), кандидатов {len(cands)}; модели: {models}")
    if a.dry:
        return

    results: dict = {"comments": {}, "posts": {}, "cands": {}}
    total_cost = 0.0
    for m in models:
        t0 = time.perf_counter()
        if comments:
            r = await run_comments(m, comments, values); results["comments"][(m, "по одному")] = r; total_cost += r["cost"]
            if not a.no_batch:
                r = await run_comments_batch(m, comments, values); results["comments"][(m, "пачкой")] = r; total_cost += r["cost"]
        if posts:
            r = await run_posts(m, posts, values); results["posts"][m] = r; total_cost += r["cost"]
        if cands:
            r = await run_cands(m, cands, cities, values); results["cands"][m] = r; total_cost += r["cost"]
        print(f"  {m}: готово за {time.perf_counter() - t0:.0f} с, потрачено всего ${total_cost:.3f}")

    print(report(results, cands))
    print(f"\nИТОГО потрачено на тест: ${total_cost:.3f}")
    dump = {"comments": {f"{m} | {mode}": r for (m, mode), r in results["comments"].items()},
            "posts": results["posts"], "cands": {m: {k: v for k, v in r.items()} for m, r in results["cands"].items()},
            "sample_comment_ids": [c["id"] for c in comments], "sample_post_ids": [p["id"] for p in posts],
            "sample_cand_ids": [c["id"] for c in cands]}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(dump, f, ensure_ascii=False, indent=1, default=str)
    print(f"подробности: {a.out}")


if __name__ == "__main__":
    asyncio.run(main())
