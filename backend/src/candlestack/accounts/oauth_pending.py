"""What the authorization of an MCP client keeps between its requests, in Redis: the request
waiting for the user's decision (``oauth:request:<sha256 of its id>``, 10 minutes, enough for a
sign-in on the way), the code the client trades for tokens (``oauth:code:<sha256>``, used once)
and, from the moment it is taken, which tokens it gives (``oauth:used-code:<sha256>``), so a
second use of the code ends them (RFC 6749, section 4.1.2); a second use also leaves
``oauth:reused-code:<sha256>``, which the first exchange checks once its tokens are committed,
so they end even when both meet. The keys hold hashes only, like ``pending``."""

import hashlib
from datetime import datetime

from pydantic import BaseModel, Field
from redis.asyncio import Redis

from candlestack.accounts.schemas import Scope

# Seconds a request waits for the decision.
REQUEST_TTL = 600
# Seconds Redis keeps a code beyond its own expiry, which the server checks itself.
_CODE_SLACK = 60
# Takes the code (KEYS[1]) and, when it was there, remembers the family it gives (KEYS[2],
# ARGV[1]) for ARGV[2] seconds, in one step: no exchange can come between the two.
_TAKE_CODE = """
local value = redis.call('GETDEL', KEYS[1])
if value then
    redis.call('SET', KEYS[2], ARGV[1], 'EX', ARGV[2])
end
return value
"""
# The family a used code (KEYS[1]) gave and, when there is one, the mark of its reuse (KEYS[2])
# for ARGV[1] seconds.
_REUSE_CODE = """
local family = redis.call('GET', KEYS[1])
if family then
    redis.call('SET', KEYS[2], '1', 'EX', ARGV[1])
end
return family
"""


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
    # The SHA-256 (hex) of the cookie of the browser the request started in: only that browser
    # may see and decide it.
    browser: str = Field(repr=False)


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

    async def take_code(self, code: str, family: str, ttl: int) -> PendingCode | None:
        """The code, once (a second take finds nothing); taking it remembers for `ttl` seconds
        that it gives the tokens of `family`."""
        value = await self._redis.eval(
            _TAKE_CODE,
            2,
            _key("code", code),
            _key("used-code", code),
            family,
            ttl + _CODE_SLACK,
        )
        return None if value is None else PendingCode.model_validate_json(value)

    async def reuse_code(self, code: str, ttl: int) -> str | None:
        """The family of tokens `code` gave when it was taken lately, marking for `ttl` seconds
        that it came again."""
        value = await self._redis.eval(
            _REUSE_CODE, 2, _key("used-code", code), _key("reused-code", code), ttl + _CODE_SLACK
        )
        if value is None:
            return None
        return value.decode() if isinstance(value, bytes) else str(value)

    async def code_reused(self, code: str) -> bool:
        """Whether `code` came again after it was taken."""
        return bool(await self._redis.exists(_key("reused-code", code)))
