"""The rate limits of API keys and connected apps (docs/architecture.md, "Accounts").

A request signed in with an API key or an OAuth access token counts against two budgets a
minute: its key's or app's (``dependencies.API_KEY_RATE_LIMIT``) and its account's, shared by
all the account's keys and apps (``USER_RATE_LIMIT``), so more keys or apps do not mean more
requests. A request costs 1; a heavy one costs ``HEAVY``: a route marked ``heavy`` at its
sign-in, an MCP tool with that cost when it runs (``charge``, after the sign-in counted 1).
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
_COST = "__candlestack_rate_cost__"


def heavy[Endpoint: Callable[..., object]](endpoint: Endpoint) -> Endpoint:
    """Marks a route's function as a heavy request, which an API key pays ``HEAVY`` for."""
    setattr(endpoint, _COST, HEAVY)
    return endpoint


def request_cost(request: Request) -> int:
    """What the request costs: ``HEAVY`` on a route marked ``heavy``, else 1."""
    route = request.scope.get("route")
    return getattr(getattr(route, "endpoint", None), _COST, 1)


@dataclass(frozen=True, slots=True)
class Budget:
    """Where a caller's requests count: the Redis counter of its key or app, its limit, what
    the 429 calls it, and its account."""

    counter: str
    limit: int
    what: str
    user_id: str


def rate_limited(wait: float, limit: int, where: str) -> ProblemError:
    """429 ``rate-limited``: more than ``limit`` a minute ``where``, with ``Retry-After``."""
    seconds = max(1, math.ceil(wait))
    return ProblemError(
        429,
        "rate-limited",
        "Too many requests",
        f"More than {limit} requests in one minute {where}, and a heavy request (a result "
        f"document, a download, a comparison, a start) counts as {HEAVY}. Retry in {seconds} "
        "seconds.",
        headers={"Retry-After": str(seconds)},
        extensions={"limit": limit},
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


async def charge(limiter: RateLimiter, budget: Budget | None, cost: int) -> None:
    """Counts what a call costs beyond the 1 its sign-in counted; nothing for the session.

    Raises:
        ProblemError: 429 ``rate-limited``.
    """
    if budget is not None and cost > 1:
        await spend(limiter, budget, cost - 1)
