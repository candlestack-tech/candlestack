"""Let ONNX models have no Keras version.

Revision ID: 4c7d2a9e1f05
Revises: 6b2e9f4d1c83
Create Date: 2026-10-26 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4c7d2a9e1f05"
down_revision: str | None = "6b2e9f4d1c83"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("models", "keras_version", existing_type=sa.String(32), nullable=True)


def downgrade() -> None:
    # The code before this revision knows no ONNX models: their rows keep an empty version.
    op.execute("UPDATE models SET keras_version = '' WHERE keras_version IS NULL")
    op.alter_column("models", "keras_version", existing_type=sa.String(32), nullable=False)
