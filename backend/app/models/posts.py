"""Посты и рилсы доноров.

Сердце суточного цикла — пара comments_count / comments_count_prev: Apify
раз в день обновляет счётчик, разница решает, идёт ли пост в досбор.
У неразобранного донора город поста ставит ИИ (city_source = ai).
"""
from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class LgPost(Base):
    __tablename__ = "lg_posts"
    __table_args__ = (
        Index("ix_posts_city_published", "city_id", "published_at"),
        Index("ix_posts_donor_published", "donor_id", "published_at"),
        Index("ix_posts_monitor", "monitor_status", "is_selling"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    shortcode: Mapped[str] = mapped_column(String(40), unique=True)   # DbGIUPvBK0Z
    ig_post_id: Mapped[str | None] = mapped_column(String(60))

    donor_id: Mapped[int] = mapped_column(ForeignKey("lg_donors.id"))
    account_id: Mapped[int] = mapped_column(ForeignKey("ig_accounts.id"))
    city_id: Mapped[int | None] = mapped_column(ForeignKey("lg_cities.id"))
    city_source: Mapped[str | None] = mapped_column(String(10))       # donor | ai | manual

    url: Mapped[str] = mapped_column(String(300))
    caption: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    product_type: Mapped[str | None] = mapped_column(String(20))      # feed | clips | carousel
    views: Mapped[int | None] = mapped_column(Integer)
    likes: Mapped[int | None] = mapped_column(Integer)

    # счётчики: вчера → сегодня
    comments_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    comments_count_prev: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    comments_delta: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_growth_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    zero_growth_days: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # разметка ИИ — один раз на пост
    is_selling: Mapped[bool | None] = mapped_column(Boolean)
    offer: Mapped[str | None] = mapped_column(String(300))
    hook: Mapped[str | None] = mapped_column(String(80))
    category: Mapped[str | None] = mapped_column(String(40))
    cta_type: Mapped[str | None] = mapped_column(String(60))
    code_word: Mapped[str | None] = mapped_column(String(60))
    ai_summary: Mapped[str | None] = mapped_column(Text)
    ai_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # active — в обходе; frozen — нет прироста N дней; excluded — оператор снял;
    # forced — оператор включил непродающий
    monitor_status: Mapped[str] = mapped_column(String(10), default="active", server_default="active")
    # сколько комментариев мы уже собрали с этого поста (для решения про Apify-досбор)
    collected_comments: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
