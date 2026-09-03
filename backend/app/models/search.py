"""Задачи поиска доноров и их кандидаты.

Задача идёт по этапам сама: collecting → filtering → classifying → ready →
distributed. Кандидаты живут внутри задачи до распределения; город у них —
результат f1 + ИИ, а не вход. Поиск глобальный, к городу не привязан.
"""
from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class LgSearchTask(Base):
    __tablename__ = "lg_search_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))          # hashtag | keyword | recommendation
    # теги/ключи списком или список account_id сидов
    input: Mapped[dict] = mapped_column(JSONB)
    title: Mapped[str] = mapped_column(String(300))        # что показываем в карточке

    stage: Mapped[str] = mapped_column(String(14), default="collecting", server_default="collecting")
    error: Mapped[str | None] = mapped_column(Text)

    collected: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    passed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    rejected_inactive: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    rejected_activity: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    confident: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    unclear: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    distributed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    stage_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LgCandidate(Base):
    __tablename__ = "lg_candidates"
    __table_args__ = (
        UniqueConstraint("task_id", "username", name="uq_candidate_task_username"),
        Index("ix_candidates_task_state", "task_id", "state"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("lg_search_tasks.id"))
    username: Mapped[str] = mapped_column(String(150))
    ig_id: Mapped[str | None] = mapped_column(String(40))
    found_by: Mapped[str | None] = mapped_column(String(200))   # тег / слово / сид, откуда пришёл

    # данные f1
    full_name: Mapped[str | None] = mapped_column(String(300))
    bio: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(String(300))
    followers: Mapped[int | None] = mapped_column(Integer)
    posts_count: Mapped[int | None] = mapped_column(Integer)
    last_post_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # ИИ «кто и где»
    activity_kind: Mapped[str | None] = mapped_column(String(30))
    activity_ok: Mapped[bool | None] = mapped_column(Boolean)
    city_id: Mapped[int | None] = mapped_column(ForeignKey("lg_cities.id"))
    city_name_raw: Mapped[str | None] = mapped_column(String(80))   # что сказала ИИ, даже если такого города у нас нет
    city_confidence: Mapped[float | None] = mapped_column(Float)
    ai_reason: Mapped[str | None] = mapped_column(Text)

    # collected → filtered → classified → distributed | rejected | unclear
    state: Mapped[str] = mapped_column(String(12), default="collected", server_default="collected")
    reject_reason: Mapped[str | None] = mapped_column(String(40))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
