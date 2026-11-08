"""The accounts tables: users, the GitHub and Google identities they sign in with, their
browser sessions, their settings and their API keys. Changed only through migrations
(``backend/migrations``)."""

import secrets
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, LargeBinary, String, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from candlestack.core import Base

# Lowercase RFC 4648 base32: no 0, 1, 8 or 9, so an ID read aloud or typed cannot mix up
# similar characters.
_ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"
_ID_LENGTH = 10
# The column's room; a name is at most ``schemas.DISPLAY_NAME_MAX`` long.
DISPLAY_NAME_LENGTH = 200


def _random_id(prefix: str) -> str:
    return prefix + "".join(secrets.choice(_ID_ALPHABET) for _ in range(_ID_LENGTH))


def new_user_id() -> str:
    """A public user ID such as ``usr_7f3k2m9qx4`` (50 random bits)."""
    return _random_id("usr_")


def new_api_key_id() -> str:
    """A public API key ID such as ``key_7f3k2m4qx4`` (50 random bits)."""
    return _random_id("key_")


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(14), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(DISPLAY_NAME_LENGTH))
    # Lowercased, like the providers' emails it is compared with.
    email: Mapped[str] = mapped_column(String(320), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_sign_in_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Identity(Base):
    """An account at GitHub or Google that signs in as a user: the provider's own ID of the
    account (``subject``), which stays when its email or login changes, names it."""

    __tablename__ = "identities"

    provider: Mapped[str] = mapped_column(String(16), primary_key=True)
    subject: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(320))
    login: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # Never loaded implicitly (that would be I/O on attribute access): only set, so a new
    # identity and its new user are inserted in the right order.
    user: Mapped[User] = relationship(lazy="raise")


class UserSession(Base):
    """A signed-in browser. Only the SHA-256 of the cookie's token is stored, so the table
    cannot be used to sign in."""

    __tablename__ = "sessions"

    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(lazy="raise")


class UserPreferences(Base):
    """The settings a user saved, one JSONB document per section of Settings. A user who never
    saved any has no row, and a document holds only what was saved: what is missing reads as
    the default of its model (``schemas.BacktestDefaults``, ``schemas.Appearance``), so a new
    setting needs no data migration."""

    __tablename__ = "user_preferences"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    backtest_defaults: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    appearance: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ApiKey(Base):
    """A key a script or an agent signs in with as its user (``Authorization: Bearer cs_...``).
    Only the SHA-256 of the key is stored, so the table cannot be used to sign in; ``prefix``,
    the key's start, finds its row and tells keys apart. A revoked key keeps its row."""

    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(14), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    # The values of ``schemas.Scope``, in their order.
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String(16)))
    prefix: Mapped[str] = mapped_column(String(11), unique=True)
    secret_hash: Mapped[bytes] = mapped_column(LargeBinary(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
