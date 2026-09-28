"""Город как отдельная ниша: свой лимит постов при заведении, окно, промпты ИИ.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-09-28 12:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("lg_cities", sa.Column("posts_per_account", sa.Integer(), nullable=True))
    op.add_column("lg_cities", sa.Column("intake_days", sa.Integer(), nullable=True))
    op.add_column("lg_cities", sa.Column("prompt_post", sa.Text(), nullable=True))
    op.add_column("lg_cities", sa.Column("prompt_comment", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("lg_cities", "prompt_comment")
    op.drop_column("lg_cities", "prompt_post")
    op.drop_column("lg_cities", "intake_days")
    op.drop_column("lg_cities", "posts_per_account")
