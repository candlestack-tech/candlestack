"""Add the name of a backtest.

Revision ID: 9c2e4f7a1b36
Revises: 3f8b6d2a9c41
Create Date: 2026-11-01 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9c2e4f7a1b36"
down_revision: str | None = "3f8b6d2a9c41"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("backtests", sa.Column("name", sa.String(length=80), nullable=True))


def downgrade() -> None:
    op.drop_column("backtests", "name")
