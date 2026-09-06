"""Досбор подписок: отметка на доноре, счётчик источников у кандидата.

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
Create Date: 2026-09-06 15:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d8e9f0a1b2c3"
down_revision: Union[str, None] = "c7d8e9f0a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("lg_donors", sa.Column("followings_collected_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("lg_candidates", sa.Column("sources_count", sa.Integer(), server_default="1", nullable=False))


def downgrade() -> None:
    op.drop_column("lg_candidates", "sources_count")
    op.drop_column("lg_donors", "followings_collected_at")
