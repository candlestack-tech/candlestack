"""Add the part of a model's check that failed.

Revision ID: 3f8b6d2a9c41
Revises: 7a3e5c1d2b94
Create Date: 2026-10-27 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3f8b6d2a9c41"
down_revision: str | None = "7a3e5c1d2b94"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("models", sa.Column("check_failed", sa.String(length=16), nullable=True))
    # A failed check kept before does not say which part failed: it is forgotten, so the API
    # checks the model again at its next start (ml/checks.py, recover) and keeps the part.
    op.execute(
        "UPDATE models SET checked_at = NULL, check_error = NULL WHERE check_error IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column("models", "check_failed")
