"""Свой порог первого сбора у ниши: у мебельщиков мало комментариев, берём все.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-09-28 14:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("lg_cities", sa.Column("min_comments_first", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("lg_cities", "min_comments_first")
