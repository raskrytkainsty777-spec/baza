"""Кандидат: максимум комментариев среди последних постов (поиск по ключам через Apify).

Revision ID: c7d8e9f0a1b2
Revises: 4e5a0de7b980
Create Date: 2026-09-06 12:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c7d8e9f0a1b2"
down_revision: Union[str, None] = "4e5a0de7b980"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("lg_candidates", sa.Column("max_comments", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("lg_candidates", "max_comments")
