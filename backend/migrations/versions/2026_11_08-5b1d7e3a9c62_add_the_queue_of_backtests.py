"""Add the queue of backtests: the status `queued` and when a backtest began to run.

A backtest stored before began to run when it was created, so ``started_at`` is its
``created_at``. The partial index finds the queued backtests of a user, oldest first, among all
the rows.

Revision ID: 5b1d7e3a9c62
Revises: d990b92fe1cd
Create Date: 2026-11-08 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5b1d7e3a9c62"
down_revision: str | None = "d990b92fe1cd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The full name, given with op.f so the naming convention does not prefix it a second time.
_STATUS = "ck_backtests_status"
_INDEX = "ix_backtests_queued"


def upgrade() -> None:
    op.drop_constraint(op.f(_STATUS), "backtests", type_="check")
    op.create_check_constraint(
        op.f(_STATUS), "backtests", "status IN ('queued', 'running', 'finished', 'failed')"
    )
    op.add_column("backtests", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE backtests SET started_at = created_at")
    op.create_index(
        _INDEX,
        "backtests",
        ["user_id", "number"],
        unique=False,
        postgresql_where=sa.text("status = 'queued'"),
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="backtests", postgresql_where=sa.text("status = 'queued'"))
    # A queued backtest has nothing to show without the queue: it fails, as cancelled.
    op.execute(
        "UPDATE backtests SET status = 'failed', finished_at = now(), "
        "error = 'The backtest was cancelled before it ran.' WHERE status = 'queued'"
    )
    op.drop_column("backtests", "started_at")
    op.drop_constraint(op.f(_STATUS), "backtests", type_="check")
    op.create_check_constraint(
        op.f(_STATUS), "backtests", "status IN ('running', 'finished', 'failed')"
    )
