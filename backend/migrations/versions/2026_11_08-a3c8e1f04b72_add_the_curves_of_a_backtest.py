"""Add the curves of a backtest: the light series' ``ts``, ``close``, ``equity`` and
``drawdown`` as zstd Parquet, which the comparison draws without loading the candles again.

A backtest that finished before has none: its candles are not stored, so they cannot be worked
out here, and the comparison draws it from its short equity series instead.

Revision ID: a3c8e1f04b72
Revises: 5b1d7e3a9c62
Create Date: 2026-11-08 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3c8e1f04b72"
down_revision: str | None = "5b1d7e3a9c62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("backtests", sa.Column("curves", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    op.drop_column("backtests", "curves")
