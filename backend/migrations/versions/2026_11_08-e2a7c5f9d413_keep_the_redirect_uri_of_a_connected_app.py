"""Keep the redirect URI a connected app's last authorization returned to, so Settings shows
the host the browser really went back to and not the first one the app registered.

An app connected before has none: Settings falls back to the app's first redirect URI.

Revision ID: e2a7c5f9d413
Revises: b7d3e9a1c524
Create Date: 2026-11-08 22:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2a7c5f9d413"
down_revision: str | None = "b7d3e9a1c524"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 512 and a loopback URI's port that came back longer than registered (":65535").
    op.add_column("connected_apps", sa.Column("redirect_uri", sa.String(length=518), nullable=True))


def downgrade() -> None:
    op.drop_column("connected_apps", "redirect_uri")
