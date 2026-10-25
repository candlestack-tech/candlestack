"""Add the check of a model.

Revision ID: 6b2e9f4d1c83
Revises: d56ee4f30929
Create Date: 2026-10-25 18:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6b2e9f4d1c83"
down_revision: str | None = "d56ee4f30929"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("models", sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("models", sa.Column("check_error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("models", "check_error")
    op.drop_column("models", "checked_at")
