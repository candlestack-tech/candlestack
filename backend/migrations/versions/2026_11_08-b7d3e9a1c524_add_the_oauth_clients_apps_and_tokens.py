"""Add the OAuth clients, connected apps and tokens.

Revision ID: b7d3e9a1c524
Revises: c4f9a2d81e37
Create Date: 2026-11-08 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7d3e9a1c524"
down_revision: str | None = "c4f9a2d81e37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "oauth_clients",
        sa.Column("client_id", sa.String(length=512), nullable=False),
        sa.Column("metadata_document", sa.Boolean(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=True),
        sa.Column("client_uri", sa.String(length=512), nullable=True),
        sa.Column("logo_uri", sa.String(length=512), nullable=True),
        sa.Column("redirect_uris", postgresql.ARRAY(sa.String(length=512)), nullable=False),
        sa.Column("token_endpoint_auth_method", sa.String(length=32), nullable=False),
        sa.Column("secret_hash", sa.LargeBinary(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("client_id", name=op.f("pk_oauth_clients")),
    )
    op.create_table(
        "connected_apps",
        sa.Column("id", sa.String(length=14), nullable=False),
        sa.Column("user_id", sa.String(length=14), nullable=False),
        sa.Column("client_id", sa.String(length=512), nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.String(length=16)), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["oauth_clients.client_id"],
            name=op.f("fk_connected_apps_client_id_oauth_clients"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_connected_apps_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_connected_apps")),
        sa.UniqueConstraint(
            "user_id", "client_id", name=op.f("uq_connected_apps_user_id_client_id")
        ),
    )
    op.create_index(
        op.f("ix_connected_apps_client_id"), "connected_apps", ["client_id"], unique=False
    )
    op.create_index(op.f("ix_connected_apps_user_id"), "connected_apps", ["user_id"], unique=False)
    op.create_table(
        "oauth_tokens",
        sa.Column("token_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("app_id", sa.String(length=14), nullable=False),
        sa.Column("family", sa.String(length=32), nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.String(length=16)), nullable=False),
        sa.Column("resource", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["app_id"],
            ["connected_apps.id"],
            name=op.f("fk_oauth_tokens_app_id_connected_apps"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("token_hash", name=op.f("pk_oauth_tokens")),
    )
    op.create_index(op.f("ix_oauth_tokens_app_id"), "oauth_tokens", ["app_id"], unique=False)
    op.create_index(op.f("ix_oauth_tokens_family"), "oauth_tokens", ["family"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_oauth_tokens_family"), table_name="oauth_tokens")
    op.drop_index(op.f("ix_oauth_tokens_app_id"), table_name="oauth_tokens")
    op.drop_table("oauth_tokens")
    op.drop_index(op.f("ix_connected_apps_user_id"), table_name="connected_apps")
    op.drop_index(op.f("ix_connected_apps_client_id"), table_name="connected_apps")
    op.drop_table("connected_apps")
    op.drop_table("oauth_clients")
