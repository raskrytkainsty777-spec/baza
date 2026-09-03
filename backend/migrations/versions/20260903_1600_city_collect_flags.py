"""Флаги сбора на уровне города: посты новых доноров и комментарии.

Revision ID: b1c2d3e4f5a6
Revises: 4a895d484258
Create Date: 2026-09-03 16:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, None] = "4a895d484258"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("lg_cities", sa.Column("collect_posts", sa.Boolean(), server_default="false", nullable=False))
    op.add_column("lg_cities", sa.Column("collect_comments", sa.Boolean(), server_default="false", nullable=False))


def downgrade() -> None:
    op.drop_column("lg_cities", "collect_comments")
    op.drop_column("lg_cities", "collect_posts")
