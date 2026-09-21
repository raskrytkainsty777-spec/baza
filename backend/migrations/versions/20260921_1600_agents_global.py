"""Агенты досбора общие для всех проектов: кабинет-создатель у агента больше не обязателен.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-21 16:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("cab_agents", "client_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    op.alter_column("cab_agents", "client_id", existing_type=sa.Integer(), nullable=False)
