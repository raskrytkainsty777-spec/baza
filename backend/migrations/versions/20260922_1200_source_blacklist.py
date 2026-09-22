"""Чёрный список источников кабинета: номера, которые нельзя включать никогда.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-09-22 12:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cab_source_blacklist",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.Integer(), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=False),
        sa.Column("note", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["cab_clients.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_id", "phone", name="uq_cab_source_blacklist"),
    )
    op.create_index("ix_cab_source_blacklist_client_id", "cab_source_blacklist", ["client_id"])


def downgrade() -> None:
    op.drop_index("ix_cab_source_blacklist_client_id", table_name="cab_source_blacklist")
    op.drop_table("cab_source_blacklist")
