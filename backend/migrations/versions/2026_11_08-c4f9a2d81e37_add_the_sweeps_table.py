"""Add the sweeps table and the sweep of a backtest: a grid of backtests started in one request.

A backtest stored before belongs to no sweep, so its ``sweep_id`` is null. The partial index
finds the backtests of a sweep among all the rows.

Revision ID: c4f9a2d81e37
Revises: 8e4c2a7f1b59
Create Date: 2026-11-08 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c4f9a2d81e37"
down_revision: str | None = "8e4c2a7f1b59"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "ix_backtests_sweep_id"


def upgrade() -> None:
    op.create_table(
        "sweeps",
        sa.Column("id", sa.String(length=14), nullable=False),
        sa.Column("user_id", sa.String(length=14), nullable=False),
        sa.Column("metric", sa.String(length=32), nullable=False),
        sa.Column("grid", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_sweeps_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sweeps")),
    )
    op.create_index(op.f("ix_sweeps_user_id"), "sweeps", ["user_id"], unique=False)
    op.add_column("backtests", sa.Column("sweep_id", sa.String(length=14), nullable=True))
    op.create_foreign_key(
        op.f("fk_backtests_sweep_id_sweeps"),
        "backtests",
        "sweeps",
        ["sweep_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        _INDEX,
        "backtests",
        ["sweep_id"],
        unique=False,
        postgresql_where=sa.text("sweep_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="backtests", postgresql_where=sa.text("sweep_id IS NOT NULL"))
    op.drop_constraint(op.f("fk_backtests_sweep_id_sweeps"), "backtests", type_="foreignkey")
    op.drop_column("backtests", "sweep_id")
    op.drop_index(op.f("ix_sweeps_user_id"), table_name="sweeps")
    op.drop_table("sweeps")
