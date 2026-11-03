"""Add the publication of a backtest, the return of its benchmark and its model's input.

The return of the benchmark of the backtests stored before is buy and hold of their
instrument, which is what their benchmark was unless it was an index fund: the fund's return
is not stored, so those stay null. The model's input comes from the copy of the manifest a
backtest made in the app stored with its run.

Revision ID: e4b7a2c9d013
Revises: 9c2e4f7a1b36
Create Date: 2026-11-02 23:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e4b7a2c9d013"
down_revision: str | None = "9c2e4f7a1b36"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("backtests", sa.Column("benchmark_return", sa.Float(), nullable=True))
    op.add_column(
        "backtests",
        sa.Column("input_shape", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("backtests", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        "UPDATE backtests SET benchmark_return = buy_and_hold_return "
        "WHERE setup->>'benchmark' = 'bh'"
    )
    op.execute(
        "UPDATE backtests SET input_shape = spec #> '{model,manifest,signature,input,shape}' "
        "WHERE jsonb_typeof(spec #> '{model,manifest,signature,input,shape}') = 'array'"
    )
    op.create_index(
        "ix_backtests_published_at",
        "backtests",
        ["published_at"],
        unique=False,
        postgresql_where=sa.text("published_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_backtests_published_at",
        table_name="backtests",
        postgresql_where=sa.text("published_at IS NOT NULL"),
    )
    op.drop_column("backtests", "published_at")
    op.drop_column("backtests", "input_shape")
    op.drop_column("backtests", "benchmark_return")
