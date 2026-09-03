"""Комментарии — сырой лог и защита от повторов.

ig_comment_id уникален: parser.im на досборе отдаёт пост целиком, Apify — свежие
с запасом; повторы просто не вставляются. Текст у мусора чистится через 30 дней,
строка остаётся ради дедупа.
"""
from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class LgComment(Base):
    __tablename__ = "lg_comments"
    __table_args__ = (
        Index("ix_comments_post", "post_id"),
        Index("ix_comments_city_written", "city_id", "written_at"),
        Index("ix_comments_qualification", "qualification"),
        Index("ix_comments_author", "author_username"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ig_comment_id: Mapped[str] = mapped_column(String(40), unique=True)

    post_id: Mapped[int] = mapped_column(ForeignKey("lg_posts.id"))
    city_id: Mapped[int | None] = mapped_column(ForeignKey("lg_cities.id"))
    author_account_id: Mapped[int | None] = mapped_column(ForeignKey("ig_accounts.id"))
    author_username: Mapped[str] = mapped_column(String(150))
    author_ig_id: Mapped[str | None] = mapped_column(String(40))

    text: Mapped[str | None] = mapped_column(Text)
    written_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    source: Mapped[str] = mapped_column(String(10), default="parserim", server_default="parserim")  # parserim | apify
    job_id: Mapped[int | None] = mapped_column(ForeignKey("lg_jobs.id"))

    # ответ самого донора — до ИИ не доходит
    is_donor_reply: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    # pending → lead | ignore | prefiltered
    qualification: Mapped[str] = mapped_column(String(12), default="pending", server_default="pending")
    ai_reason: Mapped[str | None] = mapped_column(Text)
    ai_summary: Mapped[str | None] = mapped_column(String(300))
    ai_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # оператор нажал «Исключить» / «Это лид» — идёт в примеры для промпта
    manual_override: Mapped[str | None] = mapped_column(String(8))

    age_distance_days: Mapped[int | None] = mapped_column(Integer)
    text_purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
