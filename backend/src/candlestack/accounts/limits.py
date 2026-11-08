"""The rate limits of API keys and connected apps (docs/architecture.md, "Accounts").

A request signed in with an API key or an OAuth access token counts against two budgets a
minute: its key's or app's (``dependencies.API_KEY_RATE_LIMIT``) and its account's, shared by
all the account's keys and apps (``USER_RATE_LIMIT``), so more keys or apps do not mean more
requests. A request costs 1; a heavy one costs ``HEAVY``: a route marked ``heavy`` at its
sign-in, an MCP tool with that cost when it runs (``charge``, after the sign-in counted 1).
A route or a tool may cost ``HEAVY`` only for some requests: a short answer from stored data
costs 1. What only connects or lists on the MCP server counts against a looser limit of the key
or app of its own instead (``spend_light``), which the account's budget leaves out.
The browser's session has no budget.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass

from starlette.requests import Request

from candlestack.core import ProblemError, RateLimiter

# Requests per minute and account across all its API keys and connected apps: two and a half
# keys or apps at their own limit.
USER_RATE_LIMIT = 300
# What a request that builds or reruns much costs: a result document, a download, a comparison,
# a start of a backtest or a sweep.
HEAVY = 5
# What only connects or lists (the MCP server's initialize, tools/list, ping) counts against a
# limit of its own this many times looser, so it neither spends the key's limit nor goes free.
LIGHT_RATE_LIMIT_FACTOR = 10
_COST = "__candlestack_rate_cost__"


def heavy[Endpoint: Callable[..., object]](endpoint: Endpoint) -> Endpoint:
    """Marks a route's function as a heavy request, which an API key pays ``HEAVY`` for."""
    setattr(endpoint, _COST, HEAVY)
    return endpoint


def heavy_unless(
    light: Callable[[Request], bool],
) -> Callable[[Callable[..., object]], Callable[..., object]]:
    """Marks a route's function as a heavy request unless `light` says the request asks for
    little (a summary read from stored data), which then costs 1."""

    def mark[Endpoint: Callable[..., object]](endpoint: Endpoint) -> Endpoint:
        setattr(endpoint, _COST, lambda request: 1 if light(request) else HEAVY)
        return endpoint

    return mark


def request_cost(request: Request) -> int:
    """What the request costs: ``HEAVY`` on a route marked ``heavy`` (or ``heavy_unless`` and
    not light), else 1."""
    route = request.scope.get("route")
    cost = getattr(getattr(route, "endpoint", None), _COST, 1)
    return cost(request) if callable(cost) else cost


@dataclass(frozen=True, slots=True)
class Budget:
    """Where a caller's requests count: the Redis counter of its key or app, its limit, what
    the 429 calls it, its account, and the counter of its light requests."""

    counter: str
    limit: int
    what: str
    user_id: str
    light_counter: str


def rate_limited(wait: float, limit: int, where: str, *, light: bool = False) -> ProblemError:
    """429 ``rate-limited``: more than ``limit`` a minute ``where`` (of the requests that
    connect or list when `light`), with ``Retry-After`` and ``retry_after_s``."""
    seconds = max(1, math.ceil(wait))
    if light:
        what = f"More than {limit} requests that connect or list in one minute {where}."
    else:
        what = (
            f"More than {limit} requests in one minute {where}, and a heavy request (a result "
            f"document, a download, a comparison, a start) counts as {HEAVY}."
        )
    return ProblemError(
        429,
        "rate-limited",
        "Too many requests",
        f"{what} Retry in {seconds} seconds.",
        headers={"Retry-After": str(seconds)},
        extensions={"limit": limit, "retry_after_s": seconds},
    )


async def spend(limiter: RateLimiter, budget: Budget, cost: int = 1) -> None:
    """Counts ``cost`` against the key's or app's budget, then the account's.

    Raises:
        ProblemError: 429 ``rate-limited`` for the first budget it passes (the account's is
            not counted when the key's is spent).
    """
    wait = await limiter.hit(budget.counter, budget.limit, cost)
    if wait > 0:
        raise rate_limited(wait, budget.limit, f"with this {budget.what}")
    wait = await limiter.hit(f"accounts:user-rl:{budget.user_id}", USER_RATE_LIMIT, cost)
    if wait > 0:
        raise rate_limited(
            wait, USER_RATE_LIMIT, "for this account, across all its API keys and connected apps"
        )


async def spend_light(limiter: RateLimiter, budget: Budget) -> None:
    """Counts a request that only connects or lists against the key's or app's light limit,
    ``LIGHT_RATE_LIMIT_FACTOR`` times its own; the account's budget does not count it.

    Raises:
        ProblemError: 429 ``rate-limited``.
    """
    allowed = budget.limit * LIGHT_RATE_LIMIT_FACTOR
    wait = await limiter.hit(budget.light_counter, allowed)
    if wait > 0:
        raise rate_limited(wait, allowed, f"with this {budget.what}", light=True)


async def charge(limiter: RateLimiter, budget: Budget | None, cost: int) -> None:
    """Counts what a call costs beyond the 1 its sign-in counted; nothing for the session.

    Raises:
        ProblemError: 429 ``rate-limited``.
    """
    if budget is not None and cost > 1:
        await spend(limiter, budget, cost - 1)
