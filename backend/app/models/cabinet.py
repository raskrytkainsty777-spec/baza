"""Кабинет закупки номеров (ГЦК): клиенты, их источники и контакты из Leads Factory,
статусы по вебхукам, досбор агентами, интеграции. Своя авторизация — клиенты и агенты
до админского API не дотягиваются. Спецификация — docs/CABINET.md.
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class CabClient(Base):
    """Клиент = один проект Leads Factory. Баланс считаем в контактах: остаток ₽ / цена заявки."""
    __tablename__ = "cab_clients"

    id: Mapped[int] = mapped_column(primary_key=True)
    login: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    lf_crm_id: Mapped[int | None] = mapped_column(Integer, index=True)
    lf_status: Mapped[str | None] = mapped_column(String(20))              # new | active | stop | pause
    lf_answer_cost: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))  # цена заявки в LF
    lf_balance_rub: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    balance_contacts: Mapped[int | None] = mapped_column(Integer)
    balance_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lf_error: Mapped[str | None] = mapped_column(Text)

    # экономика для вкладки «Компании»
    contact_cost: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0, server_default="0")
    handling_cost: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0, server_default="0")

    # умолчания при добавлении источников
    suppliers_default: Mapped[list] = mapped_column(JSONB, default=list)
    limit_default: Mapped[int] = mapped_column(Integer, default=5, server_default="5")

    # расписание по дням недели: 7 флагов, пн..вс
    weekdays: Mapped[list] = mapped_column(JSONB, default=list)
    schedule_applied_day: Mapped[date | None] = mapped_column(Date)

    hook_token: Mapped[str] = mapped_column(String(64), unique=True)     # /api/cab/hook/{token} — статусы
    tg_chat_id: Mapped[str | None] = mapped_column(String(32))
    tg_connect_code: Mapped[str | None] = mapped_column(String(32))

    contacts_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CabSession(Base):
    __tablename__ = "cab_sessions"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(8))                          # client | agent
    subject_id: Mapped[int] = mapped_column(Integer, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CabCompany(Base):
    """Откуда взят номер-источник. Повторяется у многих источников; по ней сводится статистика."""
    __tablename__ = "cab_companies"
    __table_args__ = (UniqueConstraint("client_id", "name", name="uq_cab_company"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("cab_clients.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CabSource(Base):
    """Номер-источник в проекте LF. Всё, что меняет клиент, ложится сюда и помечается lf_dirty —
    воркер cab_sync доносит до LF и снимает флаг."""
    __tablename__ = "cab_sources"
    __table_args__ = (
        UniqueConstraint("client_id", "phone", name="uq_cab_source_phone"),
        Index("ix_cab_sources_client", "client_id", "enabled_by_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("cab_clients.id"))
    company_id: Mapped[int | None] = mapped_column(ForeignKey("cab_companies.id"))
    phone: Mapped[str] = mapped_column(String(32))

    lf_source_id: Mapped[int | None] = mapped_column(Integer, index=True)
    lf_tags: Mapped[dict] = mapped_column(JSONB, default=dict)             # {"B222": tag_id, ...}
    lf_will_work: Mapped[bool | None] = mapped_column(Boolean)
    lf_dirty: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    lf_error: Mapped[str | None] = mapped_column(Text)

    suppliers: Mapped[list] = mapped_column(JSONB, default=list)           # ["B222","B223","B333"]
    limit: Mapped[int] = mapped_column(Integer, default=5, server_default="5")
    geo_ids: Mapped[list] = mapped_column(JSONB, default=list)
    enabled_by_user: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    enabled_by_schedule: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    contacts_total: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    contacts_today: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    repeats_total: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_contact_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lf_sebes_14: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    lf_success_14: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))

    found_by_agent_id: Mapped[int | None] = mapped_column(ForeignKey("cab_agents.id"))
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CabContact(Base):
    """Купленный номер — копия заявки LF. Дубли между поставщиками не чистим: база = копия LF."""
    __tablename__ = "cab_contacts"
    __table_args__ = (
        Index("ix_cab_contacts_client_bought", "client_id", "bought_at"),
        Index("ix_cab_contacts_client_phone", "client_id", "phone"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("cab_clients.id"))
    lf_answer_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    phone: Mapped[str] = mapped_column(String(32))
    operator: Mapped[str | None] = mapped_column(String(60))
    region: Mapped[str | None] = mapped_column(String(120))
    source_tag: Mapped[str | None] = mapped_column(String(80))            # B222_79112508055_28469
    supplier: Mapped[str | None] = mapped_column(String(8))               # B222
    source_id: Mapped[int | None] = mapped_column(ForeignKey("cab_sources.id"), index=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("cab_companies.id"), index=True)
    lf_status: Mapped[str | None] = mapped_column(String(20))             # new | repeat
    bought_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pulled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    hook_status: Mapped[str | None] = mapped_column(String(16))           # unsuccessful | lead | qual
    hook_status_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CabBlacklist(Base):
    __tablename__ = "cab_blacklist"
    __table_args__ = (UniqueConstraint("client_id", "phone", name="uq_cab_blacklist"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("cab_clients.id"))
    phone: Mapped[str] = mapped_column(String(32))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CabInbox(Base):
    """Статусы от сторонних сервисов клиента. Эндпоинт только пишет сюда; разбор — воркером."""
    __tablename__ = "cab_inbox"
    __table_args__ = (Index("ix_cab_inbox_state", "state"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("cab_clients.id"))
    raw: Mapped[dict | None] = mapped_column(JSONB)
    phone: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str | None] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(8), default="pending", server_default="pending")
    error: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CabIntegration(Base):
    """Куда клиент получает контакты: gsheets | bitrix | amo | connector | telegram."""
    __tablename__ = "cab_integrations"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("cab_clients.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    status: Mapped[str] = mapped_column(String(16), default="new", server_default="new")   # new | ok | error
    last_error: Mapped[str | None] = mapped_column(Text)
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CabOutbox(Base):
    """Доставка контакта в интеграцию клиента, с повторами."""
    __tablename__ = "cab_outbox"
    __table_args__ = (Index("ix_cab_outbox_state", "state", "next_try_at"),
                      UniqueConstraint("integration_id", "contact_id", name="uq_cab_outbox_once"))

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("cab_clients.id"))
    integration_id: Mapped[int] = mapped_column(ForeignKey("cab_integrations.id"))
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("cab_contacts.id"))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    state: Mapped[str] = mapped_column(String(8), default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    next_try_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ── досбор ───────────────────────────────────────────────────────────────────

class CabAgent(Base):
    __tablename__ = "cab_agents"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("cab_clients.id"), index=True)
    login: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(120))
    balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, server_default="0")
    requisites: Mapped[dict | None] = mapped_column(JSONB)               # {"kind": "sbp|card", "bank": …, "value": …}
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CabResourceList(Base):
    __tablename__ = "cab_resource_lists"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("cab_clients.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CabResource(Base):
    __tablename__ = "cab_resources"

    id: Mapped[int] = mapped_column(primary_key=True)
    list_id: Mapped[int] = mapped_column(ForeignKey("cab_resource_lists.id"), index=True)
    url: Mapped[str] = mapped_column(String(500))
    company_id: Mapped[int | None] = mapped_column(ForeignKey("cab_companies.id"))


class CabTask(Base):
    __tablename__ = "cab_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("cab_clients.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    list_id: Mapped[int] = mapped_column(ForeignKey("cab_resource_lists.id"))
    price_per_source: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0, server_default="0")
    limit_sources: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    to_purchase: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    purchase_limit: Mapped[int] = mapped_column(Integer, default=5, server_default="5")
    purchase_suppliers: Mapped[list] = mapped_column(JSONB, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CabTaskAgent(Base):
    __tablename__ = "cab_task_agents"

    task_id: Mapped[int] = mapped_column(ForeignKey("cab_tasks.id"), primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("cab_agents.id"), primary_key=True)


class CabFoundSource(Base):
    __tablename__ = "cab_found_sources"
    __table_args__ = (UniqueConstraint("client_id", "phone", name="uq_cab_found_phone"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("cab_clients.id"), index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("cab_tasks.id"), index=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("cab_agents.id"), index=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("cab_companies.id"))
    phone: Mapped[str] = mapped_column(String(32))
    source_id: Mapped[int | None] = mapped_column(ForeignKey("cab_sources.id"))
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CabPayout(Base):
    __tablename__ = "cab_payouts"

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("cab_agents.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    requisites: Mapped[dict | None] = mapped_column(JSONB)
    note: Mapped[str | None] = mapped_column(String(300))
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
