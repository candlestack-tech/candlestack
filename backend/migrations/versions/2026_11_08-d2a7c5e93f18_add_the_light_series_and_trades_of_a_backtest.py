"""Add the light series and trades of a backtest: those of its light result document as zstd
Parquet, so the light view and get_backtest answer without loading the candles or running the
engine again.

A backtest that finished before has none: its first read builds them from the candles, as every
read did before, and stores them.

Revision ID: d2a7c5e93f18
Revises: e2a7c5f9d413
Create Date: 2026-11-08 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d2a7c5e93f18"
down_revision: str | None = "e2a7c5f9d413"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("backtests", sa.Column("series", sa.LargeBinary(), nullable=True))
    op.add_column("backtests", sa.Column("trades", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    op.drop_column("backtests", "trades")
    op.drop_column("backtests", "series")
