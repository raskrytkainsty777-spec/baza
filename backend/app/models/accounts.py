"""Инстаграм-аккаунты, доноры, отклонённые.

ig_accounts — единый справочник на всех, кого мы видели: и доноров, и комментаторов.
Именно он даёт «уже пробитых отдаём сразу»: если phone заполнен, на пробив не шлём.
Телефон здесь — только из пробива; из инстаграма номера не берём (могут быть
администраторы), см. docs/DECISIONS.md.
"""
from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class IgAccount(Base):
    __tablename__ = "ig_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(150), unique=True)
    ig_id: Mapped[str | None] = mapped_column(String(40), index=True)

    full_name: Mapped[str | None] = mapped_column(String(300))
    bio: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(String(300))
    followers: Mapped[int | None] = mapped_column(Integer)
    following: Mapped[int | None] = mapped_column(Integer)
    posts_count: Mapped[int | None] = mapped_column(Integer)
    last_post_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_private: Mapped[bool | None] = mapped_column(Boolean)
    is_business: Mapped[bool | None] = mapped_column(Boolean)

    # donor | commenter | both — кем этот аккаунт для нас является
    roles: Mapped[str] = mapped_column(String(12), default="commenter", server_default="commenter")

    # для доноров: город и откуда он взялся (ai | f1 | manual); вид деятельности из ИИ
    city_id: Mapped[int | None] = mapped_column(ForeignKey("lg_cities.id"), index=True)
    city_source: Mapped[str | None] = mapped_column(String(10))
    activity_kind: Mapped[str | None] = mapped_column(String(30))

    # результат пробива — единственный источник номера
    phone: Mapped[str | None] = mapped_column(String(32), index=True)
    phone_source: Mapped[str | None] = mapped_column(String(10))       # probe | manual
    probe_status: Mapped[str | None] = mapped_column(String(16))       # done | not_found | error
    probed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    probe_raw: Mapped[dict | None] = mapped_column(JSONB)               # что вернул бот целиком

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LgDonor(Base):
    """Донор в городе. Один аккаунт может быть донором в двух городах —
    тогда две строки. Неразобранный донор — city_id пуст, его продающие посты
    получают город от ИИ каждый по отдельности."""
    __tablename__ = "lg_donors"
    __table_args__ = (
        UniqueConstraint("account_id", "city_id", name="uq_donor_account_city"),
        Index("ix_donors_city_status", "city_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("ig_accounts.id"), index=True)
    city_id: Mapped[int | None] = mapped_column(ForeignKey("lg_cities.id"))

    # new — идёт разовая петля; monitored — на ежедневном мониторе;
    # paused — снят с монитора; unclassified — город неясен, посты раскладываются по городам сами
    status: Mapped[str] = mapped_column(String(14), default="new", server_default="new")
    # для new: posts → ai → comments → done
    intake_stage: Mapped[str | None] = mapped_column(String(10))

    found_via: Mapped[str] = mapped_column(String(16), default="manual", server_default="manual")
    search_task_id: Mapped[int | None] = mapped_column(ForeignKey("lg_search_tasks.id"))

    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status_reason: Mapped[str | None] = mapped_column(String(300))


class LgReject(Base):
    """Кого смотрели и не взяли — чтобы следующий обход не тратил на них строки."""
    __tablename__ = "lg_rejects"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(150), unique=True)
    reason: Mapped[str] = mapped_column(String(40))        # inactive | activity | small | manual | …
    detail: Mapped[str | None] = mapped_column(String(300))
    search_task_id: Mapped[int | None] = mapped_column(ForeignKey("lg_search_tasks.id"))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
