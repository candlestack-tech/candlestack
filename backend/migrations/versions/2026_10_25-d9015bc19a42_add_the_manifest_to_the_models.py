"""Add the manifest to the models.

Revision ID: d9015bc19a42
Revises: a3f1c8e2b7d4
Create Date: 2026-10-25 10:46:32.375898
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d9015bc19a42"
down_revision: str | None = "a3f1c8e2b7d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "models", sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.add_column("models", sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("models", "verified_at")
    op.drop_column("models", "manifest")
