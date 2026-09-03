"""Лиды и две прокладки — outbox на отправку, inbox на приём.

Лид создаётся, когда ИИ отметила комментарий лидом. Дальше: номер из базы или
пробив → отправка в CRM → статусы обратно по lead_id. Цены кладутся снимком
на момент создания, чтобы смена тарифа не переписала историю.
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime, ForeignKey, Index, Integer, Numeric, String, Text, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class LgLead(Base):
    __tablename__ = "lg_leads"
    __table_args__ = (
        Index("ix_leads_city_created", "city_id", "created_at"),
        Index("ix_leads_account", "account_id"),
        Index("ix_leads_probe", "city_id", "probe_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    comment_id: Mapped[int] = mapped_column(ForeignKey("lg_comments.id"), unique=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("lg_posts.id"))
    account_id: Mapped[int] = mapped_column(ForeignKey("ig_accounts.id"))
    city_id: Mapped[int] = mapped_column(ForeignKey("lg_cities.id"))

    # номер: probe — свежий пробив, base — уже был в ig_accounts, manual
    phone: Mapped[str | None] = mapped_column(String(32))
    phone_from: Mapped[str | None] = mapped_column(String(10))
    # pending → queued → sent → done | not_found | error ; skipped — номер взят из базы
    probe_status: Mapped[str] = mapped_column(String(12), default="pending", server_default="pending")
    probed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    probe_ref: Mapped[str | None] = mapped_column(String(80))       # ref на стороне сервиса пробива

    # выдача в CRM
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outbound_status: Mapped[str] = mapped_column(String(10), default="pending", server_default="pending")

    # статусы из CRM
    crm_status: Mapped[str] = mapped_column(String(14), default="new", server_default="new")
    crm_comment: Mapped[str | None] = mapped_column(Text)
    crm_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    cost_contact: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0, server_default="0")
    cost_handling: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0, server_default="0")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LgOutbox(Base):
    """Что надо отправить наружу. Пишется в одной транзакции с лидом; воркер
    шлёт и повторяет с растущей паузой, пока не получит 200."""
    __tablename__ = "lg_outbox"
    __table_args__ = (Index("ix_outbox_state_next", "state", "next_try_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("lg_leads.id"))
    target: Mapped[str] = mapped_column(String(10))                  # crm | probe
    url: Mapped[str] = mapped_column(String(500))
    payload: Mapped[dict] = mapped_column(JSONB)

    state: Mapped[str] = mapped_column(String(8), default="pending", server_default="pending")  # pending|sent|failed|dead
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    next_try_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_code: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LgInbox(Base):
    """Что пришло снаружи. Эндпоинт только пишет сюда и отвечает 200;
    разбор — воркером, чтобы его падение не теряло событие."""
    __tablename__ = "lg_inbox"
    __table_args__ = (Index("ix_inbox_state", "state"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(120), unique=True)   # от отправителя или sha тела
    source: Mapped[str] = mapped_column(String(10))                    # crm | probe
    raw_body: Mapped[dict | None] = mapped_column(JSONB)
    raw_text: Mapped[str | None] = mapped_column(Text)                  # если тело не JSON
    remote_ip: Mapped[str | None] = mapped_column(String(64))

    state: Mapped[str] = mapped_column(String(8), default="pending", server_default="pending")   # pending|done|error
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)

    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
