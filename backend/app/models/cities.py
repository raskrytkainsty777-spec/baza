"""Города (они же проекты) и общие настройки.

Всё, что различается между городами — цены, пороги, связки с пробивом и CRM —
живёт в городе. Всё общее — в lg_settings как ключ → значение, чтобы новую
настройку можно было завести без миграции.
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class LgCity(Base):
    __tablename__ = "lg_cities"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # что в этом городе делать автоматически: посты новых доноров + суточный обход; сбор комментариев
    collect_posts: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    collect_comments: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    # цены — для расчёта стоимости лида/квала/сделки; в лид кладутся снимком
    cost_per_contact: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0, server_default="0")
    cost_per_handling: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0, server_default="0")

    # правила
    comment_fresh_days: Mapped[int] = mapped_column(Integer, default=30, server_default="30")
    post_freeze_days: Mapped[int] = mapped_column(Integer, default=30, server_default="30")
    donor_pause_days: Mapped[int] = mapped_column(Integer, default=30, server_default="30")
    resend_after_days: Mapped[int] = mapped_column(Integer, default=60, server_default="60")

    # пробив: manual — оператор выбирает даты и жмёт «Отдать»; auto — всё новое уезжает само
    probe_mode: Mapped[str] = mapped_column(String(10), default="manual", server_default="manual")
    probe_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    probe_hook_token: Mapped[str | None] = mapped_column(String(120))

    # выдача в CRM (3-й сервис) — шлём мы, не сервис пробива
    crm_webhook_url: Mapped[str | None] = mapped_column(String(500))
    crm_secret: Mapped[str | None] = mapped_column(String(120))
    send_mode: Mapped[str] = mapped_column(String(10), default="auto", server_default="auto")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LgSetting(Base):
    """Общие настройки: intake_days, parserim_lines, big_post_threshold,
    schedule.*, prompt.* — см. docs/ARCHITECTURE.md."""
    __tablename__ = "lg_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
