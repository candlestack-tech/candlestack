"""The OAuth tables in Postgres (clients, connected apps, tokens): the queries the
authorization server needs, in the request's session. Nothing here commits but ``commit``, and
the last use of a connected app, written in a transaction of its own (``touch_app``)."""

from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from candlestack.accounts.models import AppToken, ConnectedApp, RegisteredClient, User


class OAuthStore:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    def add(self, row: RegisteredClient | ConnectedApp | AppToken) -> None:
        self._db.add(row)

    async def flush(self) -> None:
        await self._db.flush()

    async def commit(self) -> None:
        await self._db.commit()

    async def client(self, client_id: str) -> RegisteredClient | None:
        return await self._db.get(RegisteredClient, client_id)

    async def user(self, user_id: str) -> User | None:
        return await self._db.get(User, user_id)

    async def lock_user(self, user_id: str) -> None:
        """Locks the user's row until the commit, so two authorizations of one client make one
        connected app."""
        await self._db.execute(select(User.id).where(User.id == user_id).with_for_update())

    async def app(self, user_id: str, client_id: str) -> ConnectedApp | None:
        """The user's connected app of the client."""
        return await self._db.scalar(
            select(ConnectedApp).where(
                ConnectedApp.user_id == user_id, ConnectedApp.client_id == client_id
            )
        )

    async def user_app(self, user_id: str, app_id: str) -> ConnectedApp | None:
        return await self._db.scalar(
            select(ConnectedApp).where(ConnectedApp.id == app_id, ConnectedApp.user_id == user_id)
        )

    async def apps(self, user_id: str) -> list[tuple[ConnectedApp, RegisteredClient]]:
        """The user's connected apps with their clients, the most recently connected first."""
        rows = await self._db.execute(
            select(ConnectedApp, RegisteredClient)
            .join(RegisteredClient, RegisteredClient.client_id == ConnectedApp.client_id)
            .where(ConnectedApp.user_id == user_id)
            .order_by(ConnectedApp.created_at.desc(), ConnectedApp.id)
        )
        return [(row[0], row[1]) for row in rows]

    async def delete_app(self, app_id: str) -> None:
        """Deletes the connected app; its tokens go with it (the foreign key cascades)."""
        await self._db.execute(delete(ConnectedApp).where(ConnectedApp.id == app_id))

    async def token(
        self, token_hash: bytes, kind: str, *, lock: bool = False
    ) -> tuple[AppToken, ConnectedApp, User] | None:
        """The token of the hash and kind with its app and user; `lock` locks the token's row
        until the commit, so a refresh token is swapped once however many requests bring it."""
        query = (
            select(AppToken, ConnectedApp, User)
            .join(ConnectedApp, ConnectedApp.id == AppToken.app_id)
            .join(User, User.id == ConnectedApp.user_id)
            .where(AppToken.token_hash == token_hash, AppToken.kind == kind)
        )
        if lock:
            query = query.with_for_update(of=AppToken)
        row = (await self._db.execute(query)).first()
        return None if row is None else (row[0], row[1], row[2])

    async def delete_token(self, token_hash: bytes) -> None:
        await self._db.execute(delete(AppToken).where(AppToken.token_hash == token_hash))

    async def delete_family(self, family: str) -> None:
        """Deletes every token one authorization gave."""
        await self._db.execute(delete(AppToken).where(AppToken.family == family))

    async def granted_scopes(self, app_id: str, now: datetime) -> set[str]:
        """The scopes of the app's authorizations that still work: of its refresh tokens that
        are neither used nor expired."""
        rows = await self._db.scalars(
            select(AppToken.scopes).where(
                AppToken.app_id == app_id,
                AppToken.kind == "refresh",
                AppToken.rotated_at.is_(None),
                AppToken.expires_at > now,
            )
        )
        return {scope for scopes in rows for scope in scopes}

    async def delete_expired_tokens(self, app_id: str, now: datetime) -> None:
        await self._db.execute(
            delete(AppToken).where(AppToken.app_id == app_id, AppToken.expires_at <= now)
        )

    async def touch_app(self, app_id: str, when: datetime) -> None:
        """Writes the app's last use and commits it in a session of its own, on another
        connection of the pool: when that fails, the request's session is left as it was."""
        async with AsyncSession(self._db.bind) as own:
            await own.execute(
                update(ConnectedApp).where(ConnectedApp.id == app_id).values(last_used_at=when)
            )
            await own.commit()
