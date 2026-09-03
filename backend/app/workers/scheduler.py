"""Планировщик фоновых воркеров — точка входа службы baza-worker.

Каждый воркер — отдельный модуль с async-функцией run(). Планировщик
поднимает их как задачи asyncio и держит процесс живым. Падение одного
воркера логируется и перезапускается через паузу, не роняя остальных.
"""
import asyncio
import logging
import signal

from ..config import settings
from . import (
    ai_comments, ai_posts, comments_collect, discovery, donor_intake, inbox_worker, job_runner,
    outbox_worker, posts_sync, probe_feeder,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("scheduler")

RESTART_PAUSE = 15   # секунд между перезапусками упавшего воркера

WORKERS: list[tuple[str, callable]] = [
    ("job_runner", job_runner.run),
    ("discovery", discovery.run),
    ("donor_intake", donor_intake.run),
    ("comments_collect", comments_collect.run),
    ("posts_sync", posts_sync.run),
    ("ai_posts", ai_posts.run),
    ("ai_comments", ai_comments.run),
    ("probe_feeder", probe_feeder.run),
    ("outbox", outbox_worker.run),
    ("inbox", inbox_worker.run),
]


async def _supervise(name: str, factory):
    while True:
        try:
            log.info("▶ %s", name)
            await factory()
            log.warning("%s завершился сам — перезапуск через %ds", name, RESTART_PAUSE)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("%s упал — перезапуск через %ds", name, RESTART_PAUSE)
        await asyncio.sleep(RESTART_PAUSE)


async def main():
    log.info("baza-worker: tz=%s, воркеров %d", settings.tz_display, len(WORKERS))
    tasks = [asyncio.create_task(_supervise(n, f), name=n) for n, f in WORKERS]
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    log.info("baza-worker: остановлен")


if __name__ == "__main__":
    asyncio.run(main())
