"""Пост: суть предложения фразой для подстановки в текст (offer_text).

Revision ID: e9f0a1b2c3d4
Revises: d8e9f0a1b2c3
Create Date: 2026-09-06 17:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e9f0a1b2c3d4"
down_revision: Union[str, None] = "d8e9f0a1b2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("lg_posts", sa.Column("offer_text", sa.String(200), nullable=True))


def downgrade() -> None:
    op.drop_column("lg_posts", "offer_text")
