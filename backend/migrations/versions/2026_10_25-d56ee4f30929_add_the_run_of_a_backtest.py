"""Add the run of a backtest to the backtests.

Revision ID: d56ee4f30929
Revises: d9015bc19a42
Create Date: 2026-10-25 15:26:18.153863
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d56ee4f30929"
down_revision: str | None = "d9015bc19a42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "backtests", sa.Column("spec", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.add_column(
        "backtests", sa.Column("report", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.add_column(
        "backtests", sa.Column("stats", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.add_column("backtests", sa.Column("predictions", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    op.drop_column("backtests", "predictions")
    op.drop_column("backtests", "stats")
    op.drop_column("backtests", "report")
    op.drop_column("backtests", "spec")
