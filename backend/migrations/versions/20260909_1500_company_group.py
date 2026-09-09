"""Компания клиента: группа (произвольная метка для выгрузки и массовых действий).

Revision ID: f0a1b2c3d4e5
Revises: e9f0a1b2c3d4
Create Date: 2026-09-09 15:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f0a1b2c3d4e5"
down_revision: Union[str, None] = "e9f0a1b2c3d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("cab_companies", sa.Column("group_name", sa.String(120), nullable=True))


def downgrade() -> None:
    op.drop_column("cab_companies", "group_name")
