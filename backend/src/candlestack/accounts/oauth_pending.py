"""What the authorization of an MCP client keeps between its requests, in Redis: the request
waiting for the user's decision (``oauth:request:<sha256 of its id>``, 10 minutes, enough for a
sign-in on the way), the code the client trades for tokens (``oauth:code:<sha256>``, used once)
and, once traded, which tokens it gave (``oauth:used-code:<sha256>``), so a second use of the
code ends them (RFC 6749, section 4.1.2). The keys hold hashes only, like ``pending``."""

import hashlib
from datetime import datetime

from pydantic import BaseModel, Field
from redis.asyncio import Redis

from candlestack.accounts.schemas import Scope

# Seconds a request waits for the decision.
REQUEST_TTL = 600
# Seconds Redis keeps a code beyond its own expiry, which the server checks itself.
_CODE_SLACK = 60


def _key(kind: str, value: str) -> str:
    return f"oauth:{kind}:{hashlib.sha256(value.encode()).hexdigest()}"


class PendingAuthorization(BaseModel):
    """An authorize request that passed its checks, waiting for the user's decision."""

    client_id: str
    redirect_uri: str
    state: str | None = Field(repr=False)
    code_challenge: str = Field(repr=False)
    scopes: list[Scope]
    resource: str
    expires_at: datetime


class PendingCode(BaseModel):
    """What an authorization code stands for: the user's consent for the client."""

    client_id: str
    redirect_uri: str
    code_challenge: str = Field(repr=False)
    scopes: list[Scope]
    resource: str
    user_id: str
    expires_at: datetime


class OAuthPending:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def put_request(self, request_id: str, pending: PendingAuthorization) -> None:
        await self._redis.set(
            _key("request", request_id), pending.model_dump_json(), ex=REQUEST_TTL
        )

    async def request(self, request_id: str) -> PendingAuthorization | None:
        value = await self._redis.get(_key("request", request_id))
        return None if value is None else PendingAuthorization.model_validate_json(value)

    async def take_request(self, request_id: str) -> PendingAuthorization | None:
        """The request, once: a second take finds nothing."""
        value = await self._redis.getdel(_key("request", request_id))
        return None if value is None else PendingAuthorization.model_validate_json(value)

    async def put_code(self, code: str, pending: PendingCode, ttl: int) -> None:
        await self._redis.set(_key("code", code), pending.model_dump_json(), ex=ttl + _CODE_SLACK)

    async def take_code(self, code: str) -> PendingCode | None:
        """The code, once: a second take finds nothing."""
        value = await self._redis.getdel(_key("code", code))
        return None if value is None else PendingCode.model_validate_json(value)

    async def mark_code_used(self, code: str, family: str, ttl: int) -> None:
        """Remembers for `ttl` seconds that `code` gave the tokens of `family`."""
        await self._redis.set(_key("used-code", code), family, ex=ttl + _CODE_SLACK)

    async def used_code_family(self, code: str) -> str | None:
        """The family of tokens `code` gave, when it was traded lately."""
        value = await self._redis.get(_key("used-code", code))
        if value is None:
            return None
        return value.decode() if isinstance(value, bytes) else str(value)
