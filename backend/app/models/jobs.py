"""Задания внешних сервисов, журнал событий, витрина дашборда.

lg_jobs — учёт всего, что заказано у parser.im и Apify: без него не понять,
какой сбор в работе и почему в базе нет ожидаемых данных. Планировщик по этой
же таблице считает занятые строки parser.im.
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class LgJob(Base):
    __tablename__ = "lg_jobs"
    __table_args__ = (
        Index("ix_jobs_provider_state", "provider", "state"),
        Index("ix_jobs_external", "provider", "external_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(10))                  # parserim | apify
    external_id: Mapped[str | None] = mapped_column(String(60))        # tid у parser.im, run id у Apify
    # search | filter | posts_intake | comments | apify_new_posts | apify_counters
    # | apify_recommend | apify_comments
    kind: Mapped[str] = mapped_column(String(24))
    purpose: Mapped[str | None] = mapped_column(String(300))            # человекочитаемо, для экрана «Задания»

    city_id: Mapped[int | None] = mapped_column(ForeignKey("lg_cities.id"))
    donor_id: Mapped[int | None] = mapped_column(ForeignKey("lg_donors.id"))
    search_task_id: Mapped[int | None] = mapped_column(ForeignKey("lg_search_tasks.id"))

    payload: Mapped[dict | None] = mapped_column(JSONB)                 # параметры запроса как отправили
    lines: Mapped[int] = mapped_column(Integer, default=0, server_default="0")   # сколько строк parser.im занимает
    priority: Mapped[int] = mapped_column(Integer, default=50, server_default="50")

    # queued → running → done | error ; finished — принудительно завершено
    state: Mapped[str] = mapped_column(String(10), default="queued", server_default="queued")
    count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")          # по данным провайдера
    rows_imported: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # сколько реально легло в базу
    error: Mapped[str | None] = mapped_column(Text)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LgEvent(Base):
    """«Донор X → пауза: 34 дня без постов». Каждое автоматическое решение —
    строка с причиной; критичные дублируются в Telegram."""
    __tablename__ = "lg_events"
    __table_args__ = (Index("ix_events_at", "at"), Index("ix_events_entity", "entity", "entity_id"))

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    level: Mapped[str] = mapped_column(String(6), default="info", server_default="info")   # info | warn | error
    kind: Mapped[str] = mapped_column(String(40))                      # donor.paused, post.frozen, probe.error, …
    entity: Mapped[str | None] = mapped_column(String(20))             # donor | post | lead | job | city
    entity_id: Mapped[int | None] = mapped_column(Integer)
    message: Mapped[str] = mapped_column(String(500))
    payload: Mapped[dict | None] = mapped_column(JSONB)
    notified: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class LgStatsDaily(Base):
    """Витрина дашборда: пересчёт ночью и по кнопке. Разрезы — город, донор,
    крючок, категория; пустой разрез хранится как 0 / '', а не NULL, чтобы
    уникальный ключ работал."""
    __tablename__ = "lg_stats_daily"
    __table_args__ = (
        UniqueConstraint("day", "city_id", "donor_id", "hook", "category", name="uq_stats_slice"),
        Index("ix_stats_city_day", "city_id", "day"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(Date)
    city_id: Mapped[int] = mapped_column(ForeignKey("lg_cities.id"))
    donor_id: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    hook: Mapped[str] = mapped_column(String(80), default="", server_default="")
    category: Mapped[str] = mapped_column(String(40), default="", server_default="")

    new_posts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    growth_posts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    comments: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    commenters: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    leads: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    probed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    with_phone: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    sent: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    applications: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    quals: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    deals: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    spend_contact: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, server_default="0")
    spend_handling: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, server_default="0")

    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
