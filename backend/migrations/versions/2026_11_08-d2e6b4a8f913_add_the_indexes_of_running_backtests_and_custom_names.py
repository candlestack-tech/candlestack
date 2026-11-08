"""Add the indexes of running backtests and of custom names: the scheduler asks whether a user
has a backtest running for every queue it looks at, and a new backtest looks up a few names
among the account's own and generated names; the rows that run and the custom names are a few
among all.

Revision ID: d2e6b4a8f913
Revises: e2a7c5f9d413
Create Date: 2026-11-08 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d2e6b4a8f913"
down_revision: str | None = "e2a7c5f9d413"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RUNNING_INDEX = "ix_backtests_running"
_RUNNING = sa.text("status = 'running'")
_NAME_INDEX = "ix_backtests_user_id_name"
_NAMED = sa.text("name IS NOT NULL")


def upgrade() -> None:
    op.create_index(
        _RUNNING_INDEX, "backtests", ["user_id"], unique=False, postgresql_where=_RUNNING
    )
    op.create_index(
        _NAME_INDEX,
        "backtests",
        ["user_id", sa.text("lower(name)")],
        unique=False,
        postgresql_where=_NAMED,
    )


def downgrade() -> None:
    op.drop_index(_NAME_INDEX, table_name="backtests", postgresql_where=_NAMED)
    op.drop_index(_RUNNING_INDEX, table_name="backtests", postgresql_where=_RUNNING)
