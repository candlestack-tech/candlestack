"""The table of the models users upload; their files live in the model storage
(``storage.py``). Changed only through migrations (``backend/migrations``)."""

import secrets
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Text,
    false,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, query_expression

from candlestack.core import Base

# The alphabet of the user IDs (lowercase RFC 4648 base32: no 0, 1, 8 or 9), so the IDs of the
# app read alike.
_ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"
_ID_LENGTH = 10


def new_model_id() -> str:
    """A public model ID such as ``mdl_7f3k2m9qx4`` (50 random bits)."""
    return "mdl_" + "".join(secrets.choice(_ID_ALPHABET) for _ in range(_ID_LENGTH))


class Model(Base):
    """An uploaded model: what its file holds, as the upload check read it. The file is at
    ``<user_id>/<id>.keras`` (or ``.onnx``, by its framework) in the model storage."""

    __tablename__ = "models"

    id: Mapped[str] = mapped_column(String(14), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # The file name, extension included.
    name: Mapped[str] = mapped_column(String(255))
    framework: Mapped[str] = mapped_column(String(16))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    # The SHA-256 of the file, which backtests will name the model by.
    sha256: Mapped[bytes] = mapped_column(LargeBinary(32))
    parameters: Mapped[int] = mapped_column(BigInteger)
    input_shape: Mapped[list[int] | None] = mapped_column(JSONB)
    output_shape: Mapped[list[int] | None] = mapped_column(JSONB)
    # The Keras release that saved a Keras file; null for an ONNX file.
    keras_version: Mapped[str | None] = mapped_column(String(32))
    starred: Mapped[bool] = mapped_column(Boolean, server_default=false(), default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # How to build the model's input (docs/preprocessing.md), as the user's manifest gave it;
    # null until one is added. Only a manifest that passed every check that reads the files is
    # kept. It may be large with its example, so it is loaded only where it is asked for.
    manifest: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, deferred=True, deferred_raiseload=True
    )
    # When the worker's trial run gave the output of the manifest's example (#268); null until
    # then, and again when the manifest is replaced.
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When the worker's trial run of this manifest came to an end that says something about the
    # model (passed or failed, not a worker that did not answer), which part of the check failed
    # (`trial_run` or `example`, schemas.CheckPart) and why in plain sentences for the Models
    # page; all null until then, and again when the manifest is replaced. A model with a manifest
    # and no checked_at is checked again at startup (checks.py).
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    check_failed: Mapped[str | None] = mapped_column(String(16))
    check_error: Mapped[str | None] = mapped_column(Text)
    # The manifest without its example and notes, what the model's summary is made of: loaded
    # by the queries of store.py that ask for it, None where they do not.
    manifest_spec: Mapped[dict[str, Any] | None] = query_expression()


# A user's names are unique in any case, as the numbering of uploads compares them so.
Index("uq_models_user_id_name", Model.user_id, func.lower(Model.name), unique=True)
