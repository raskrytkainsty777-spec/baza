"""Расписание из настроек: «09:00,21:00» по Москве.

Слот считается наступившим, когда его время сегодня уже прошло и последний
запуск (last_run.<имя> в lg_settings) старше этого слота. Пропущенный из-за
простоя слот отработает при первом же проходе — лучше поздно, чем никогда.
"""
from datetime import datetime, time
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")


def parse_slots(schedule: str) -> list[time]:
    out = []
    for part in (schedule or "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            h, m = part.split(":")
            out.append(time(int(h), int(m)))
        except ValueError:
            continue
    return sorted(out)


def slot_key(day: datetime, t: time) -> str:
    return f"{day:%Y-%m-%d} {t:%H:%M}"


def due_slot(schedule: str, last_run: str | None, now: datetime | None = None) -> str | None:
    """Ключ последнего прошедшего сегодня слота, если он ещё не отработан."""
    now = now or datetime.now(MSK)
    passed = [slot_key(now, t) for t in parse_slots(schedule) if t <= now.time()]
    if not passed:
        return None
    latest = max(passed)
    return latest if (last_run or "") < latest else None
