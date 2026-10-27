"""The models table in Postgres: the queries the service needs, in the request's session.
Nothing here commits; the service does, once a change is complete."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, literal_column, select, text, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import with_expression

from candlestack.ml.models import Model
from candlestack.ml.schemas import CheckPart
from candlestack.ml.usage import ModelUsage, ModelUsageStats, no_usage

# The manifest without its example and notes (``Model.manifest_spec``): a few KB where the
# manifest may be 1 MB, so a list of 100 models never reads their examples.
_WITH_SPEC = with_expression(
    Model.manifest_spec,
    Model.manifest.op("-", return_type=JSONB)(literal_column("ARRAY['example', 'about']")),
)


class ModelStore:
    def __init__(self, db: AsyncSession, backtests: ModelUsage = no_usage) -> None:
        self._db = db
        self._backtests = backtests

    def add(self, model: Model) -> None:
        self._db.add(model)

    async def flush(self) -> None:
        await self._db.flush()

    async def commit(self) -> None:
        await self._db.commit()

    async def lock_user(self, user_id: str) -> None:
        """Takes the lock of the user's models until the transaction ends, so the uploads,
        renames, manifests and deletions of one user run one after the other: two uploads
        cannot both pass the quota, nor take the same name, and nothing updates a row a
        deletion removed. An advisory lock, as the rows to lock may not exist yet."""
        await self._db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"ml:models:{user_id}"},
        )

    async def models(self, user_id: str) -> list[Model]:
        """The user's models, oldest first."""
        rows = await self._db.scalars(
            select(Model)
            .options(_WITH_SPEC)
            .where(Model.user_id == user_id)
            .order_by(Model.created_at, Model.id)
        )
        return list(rows)

    async def model(self, user_id: str, model_id: str) -> Model | None:
        """The user's model with the ID; None also when it is another user's."""
        return await self._db.scalar(
            select(Model).options(_WITH_SPEC).where(Model.id == model_id, Model.user_id == user_id)
        )

    async def manifest(self, user_id: str, model_id: str) -> tuple[dict[str, Any] | None] | None:
        """The manifest of the user's model, in a row of one (None in it without one); None
        when the user has no model with the ID."""
        row = (
            await self._db.execute(
                select(Model.manifest).where(Model.id == model_id, Model.user_id == user_id)
            )
        ).one_or_none()
        return None if row is None else (row[0],)

    async def check_input(
        self, user_id: str, model_id: str
    ) -> tuple[bytes, dict[str, Any], str] | None:
        """The SHA-256 of the user's model file, its manifest and its format, what a check
        runs with; None when the user has no model with the ID or it has no manifest."""
        row = (
            await self._db.execute(
                select(Model.sha256, Model.manifest, Model.framework).where(
                    Model.id == model_id, Model.user_id == user_id
                )
            )
        ).one_or_none()
        return None if row is None or row[1] is None else (row[0], row[1], row[2])

    async def unchecked(self) -> list[tuple[str, str]]:
        """The user and model IDs of the models of every user whose manifest no check came to
        an end for, oldest first."""
        rows = await self._db.execute(
            select(Model.user_id, Model.id)
            .where(Model.manifest.is_not(None), Model.checked_at.is_(None))
            .order_by(Model.created_at, Model.id)
        )
        return [(row[0], row[1]) for row in rows]

    async def record_check(
        self,
        model_id: str,
        *,
        checked_at: datetime,
        verified: bool,
        failed: CheckPart | None,
        error: str | None,
    ) -> None:
        """Keeps what the check of the model's manifest came to: verified now, or not, with
        the part that failed and the error of a failed one."""
        await self._db.execute(
            update(Model)
            .where(Model.id == model_id)
            .values(
                checked_at=checked_at,
                verified_at=checked_at if verified else None,
                check_failed=failed,
                check_error=error,
            )
        )

    async def backtest_usage(
        self, user_id: str, model_ids: Sequence[str]
    ) -> Mapping[str, ModelUsageStats]:
        """How much the backtests used the user's models with the IDs."""
        return await self._backtests(self._db, user_id, model_ids) if model_ids else {}

    async def usage(self, user_id: str) -> tuple[int, int]:
        """The bytes the user's models take, and how many they are."""
        row = (
            await self._db.execute(
                select(func.coalesce(func.sum(Model.size_bytes), 0), func.count()).where(
                    Model.user_id == user_id
                )
            )
        ).one()
        return int(row[0]), int(row[1])

    async def lowered(self, text: str) -> str:
        """`text` lowercased as Postgres does it, as the unique index of the names does."""
        lowered = await self._db.scalar(select(func.lower(text)))
        assert lowered is not None  # lower() of a string is a string
        return lowered

    async def names_starting(self, user_id: str, prefix: str) -> list[str]:
        """The names of the user's models that start with `prefix` in any case, lowercased
        by Postgres."""
        pattern = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rows = await self._db.scalars(
            select(func.lower(Model.name)).where(
                Model.user_id == user_id,
                func.lower(Model.name).like(func.lower(pattern), escape="\\"),
            )
        )
        return list(rows)

    async def name_taken(self, user_id: str, name: str, *, other_than: str | None = None) -> bool:
        """Whether a model of the user (other than `other_than`) has `name`, in any case. Postgres
        compares, as its unique index does."""
        query = select(Model.id).where(
            Model.user_id == user_id, func.lower(Model.name) == func.lower(name)
        )
        if other_than is not None:
            query = query.where(Model.id != other_than)
        return await self._db.scalar(query.limit(1)) is not None

    async def delete(self, user_id: str, model_id: str) -> bool:
        """Deletes the user's model; False when the user has none with the ID."""
        deleted = await self._db.scalar(
            delete(Model).where(Model.id == model_id, Model.user_id == user_id).returning(Model.id)
        )
        return deleted is not None

    async def delete_all(self, user_id: str) -> None:
        await self._db.execute(delete(Model).where(Model.user_id == user_id))
