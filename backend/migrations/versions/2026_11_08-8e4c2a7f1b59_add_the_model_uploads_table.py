"""Add the model uploads table: the one-time links a model is uploaded through.

Revision ID: 8e4c2a7f1b59
Revises: 5b1d7e3a9c62
Create Date: 2026-11-08 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8e4c2a7f1b59"
down_revision: str | None = "5b1d7e3a9c62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_uploads",
        sa.Column("id", sa.String(length=14), nullable=False),
        sa.Column("user_id", sa.String(length=14), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("framework", sa.String(length=16), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("model_id", sa.String(length=14), nullable=True),
        sa.Column("problem", sa.String(length=64), nullable=True),
        sa.Column("failed_check", sa.String(length=16), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('waiting', 'checking', 'done', 'failed')",
            name=op.f("ck_model_uploads_status"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_model_uploads_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_uploads")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_model_uploads_token_hash")),
    )
    op.create_index(op.f("ix_model_uploads_user_id"), "model_uploads", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_model_uploads_user_id"), table_name="model_uploads")
    op.drop_table("model_uploads")
