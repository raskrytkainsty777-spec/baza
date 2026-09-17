"""Автолимит остатка: минимальный остаток актива по дневному расходу.

Revision ID: a1b2c3d4e5f6
Revises: f0a1b2c3d4e5
Create Date: 2026-09-17 19:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "f0a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("cab_clients", sa.Column("min_balance_auto", sa.Boolean(), server_default="true", nullable=False))
    op.add_column("cab_clients", sa.Column("min_balance_contacts", sa.Integer(), nullable=True))
    op.add_column("cab_clients", sa.Column("min_balance_applied_day", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("cab_clients", "min_balance_applied_day")
    op.drop_column("cab_clients", "min_balance_contacts")
    op.drop_column("cab_clients", "min_balance_auto")
