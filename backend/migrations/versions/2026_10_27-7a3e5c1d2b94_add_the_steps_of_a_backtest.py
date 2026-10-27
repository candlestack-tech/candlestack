"""Add the steps of a backtest's run to the backtests.

Revision ID: 7a3e5c1d2b94
Revises: 4c7d2a9e1f05
Create Date: 2026-10-27 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7a3e5c1d2b94"
down_revision: str | None = "4c7d2a9e1f05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "backtests", sa.Column("steps", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("backtests", "steps")
