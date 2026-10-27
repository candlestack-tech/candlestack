"""Load test of the data API: virtual users who search, open an instrument and scroll its chart.

Each user repeats one session: types a crypto base asset letter by letter into the search
(``/instruments?q=``), opens a result (``/instruments/{id}``), loads the candles the chart shows
first (``/instruments/{id}/candles``) and scrolls back a few windows, with a pause between steps.
Every user sends its own ``CF-Connecting-IP``, so the per-client rate limit counts it as one
person, as it would behind Cloudflare. All share one session of the dev sign-in, as the market
data is for signed-in accounts, so the backend must run with ``APP_ENV`` ``local``. With
``--rate-limit-probe``, one more client sends detail requests without pause until it gets 429,
and the backend must still answer afterwards.

The report is Markdown on stdout: requests per second, latency percentiles and the answers
per endpoint, the peak memory of ``--container`` (sampled with ``docker stats``) and the probe.
See README.md next to this file for how to run it; never against stage or prod without the
tech lead's OK.

    uv run python tests/load/load.py --users 50 --duration 120 --container candlestack-dev-backend-1
"""

import argparse
import asyncio
import contextlib
import math
import random
import re
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

import httpx

API = "/api/v1/data"
# Timeframes the chart opens with and the candles it shows per window.
TIMEFRAMES = ("15m", "1h", "4h", "1d")
SECONDS = {"15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
WINDOW = 300
SCROLLS = 3
_MEMORY = re.compile(r"([\d.]+)\s*([KMG]i?B)")
_UNITS = {"KB": 1e3, "MB": 1e6, "GB": 1e9, "KiB": 2**10, "MiB": 2**20, "GiB": 2**30}


@dataclass
class Stats:
    """Latencies (seconds) and answers (status code, or the transport error) per endpoint."""

    latencies: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    answers: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))

    def add(self, endpoint: str, seconds: float, answer: str) -> None:
        self.latencies[endpoint].append(seconds)
        self.answers[endpoint][answer] += 1


def percentile(values: Sequence[float], share: float) -> float:
    """The nearest-rank percentile: the smallest value with at least ``share`` of all at or
    below it; 0 for no values."""
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(share * len(ordered)) - 1)]


def is_error(answer: str) -> bool:
    """A server error or a request that got no answer; 4xx are the client's doing."""
    return not answer.isdigit() or int(answer) >= 500


def parse_memory(usage: str) -> float:
    """Bytes in use from a ``docker stats`` MemUsage cell such as ``"91.3MiB / 320MiB"``."""
    found = _MEMORY.match(usage.strip())
    if found is None:
        raise ValueError(f"cannot read memory usage {usage!r}")
    return float(found[1]) * _UNITS[found[2]]


def window_start(end: int, timeframe: str, available_from: int | None) -> int:
    """Where the window of ``WINDOW`` candles before ``end`` starts; not before the first
    candle, as the chart does for a young pair (the API answers 422 before it)."""
    start = end - WINDOW * SECONDS[timeframe]
    return start if available_from is None else max(start, available_from)


def report(stats: Stats, seconds: float, peak_memory: float | None, probe: str | None) -> str:
    """The Markdown report of a run that lasted ``seconds``."""
    lines = [
        "| Endpoint | Requests | Req/s | p50 ms | p95 ms | p99 ms | Max ms | Answers |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    every: list[float] = []
    errors = 0
    for endpoint in sorted(stats.latencies):
        values = stats.latencies[endpoint]
        every += values
        answers = stats.answers[endpoint]
        errors += sum(count for answer, count in answers.items() if is_error(answer))
        lines.append(_row(endpoint, values, seconds, ", ".join(_answers(answers))))
    lines.append(_row("all", every, seconds, f"{errors} errors (5xx or no answer)"))
    lines.append("")
    if peak_memory is not None:
        lines.append(f"Peak backend memory: {peak_memory / 2**20:.0f} MiB")
    if probe is not None:
        lines.append(f"Rate limit probe: {probe}")
    return "\n".join(lines) + "\n"


def _row(name: str, values: Sequence[float], seconds: float, answers: str) -> str:
    cells = [percentile(values, share) * 1000 for share in (0.5, 0.95, 0.99, 1.0)]
    timings = " | ".join(f"{cell:.0f}" for cell in cells)
    return f"| {name} | {len(values)} | {len(values) / seconds:.1f} | {timings} | {answers} |"


def _answers(answers: Counter[str]) -> Iterable[str]:
    return (f"{answer}: {count}" for answer, count in sorted(answers.items()))


class User:
    """One virtual user with its own client address."""

    def __init__(self, client: httpx.AsyncClient, stats: Stats, address: str, think: float):
        self._client = client
        self._stats = stats
        self._headers = {"CF-Connecting-IP": address}
        self._think = think

    async def get(self, endpoint: str, path: str, **params: str | int) -> httpx.Response | None:
        """The answer, or ``None`` when there is none (timeout, refused connection)."""
        began = time.perf_counter()
        try:
            response = await self._client.get(path, params=params, headers=self._headers)
        except httpx.HTTPError as exc:
            self._stats.add(endpoint, time.perf_counter() - began, type(exc).__name__)
            return None
        self._stats.add(endpoint, time.perf_counter() - began, str(response.status_code))
        return response

    async def pause(self) -> None:
        await asyncio.sleep(random.uniform(0.5, 1.5) * self._think)

    async def session(self, bases: Sequence[str]) -> None:
        """Search while typing, open the first result, load its chart and scroll back."""
        base = random.choice(bases).lower()
        found = None
        for end in range(1, min(len(base), 4) + 1):
            found = await self.get("search", f"{API}/instruments", q=base[:end], market="crypto")
            await asyncio.sleep(0.15 * self._think)  # typing
        if found is None or found.status_code != 200 or not found.json()["items"]:
            return
        instrument = found.json()["items"][0]["id"]
        await self.pause()
        detail = await self.get("detail", f"{API}/instruments/{instrument}")
        if detail is None or detail.status_code != 200:
            return
        info = detail.json()
        timeframe = random.choice(TIMEFRAMES)
        end = info["available_to"]
        for step in range(SCROLLS + 1):
            start = window_start(end, timeframe, info["available_from"])
            if start >= end:
                return
            await self.get(
                "candles" if step == 0 else "scroll back",
                f"{API}/instruments/{instrument}/candles",
                timeframe=timeframe,
                start=start,
                end=end,
            )
            end = start
            await self.pause()

    async def run(self, bases: Sequence[str], until: float) -> None:
        while time.monotonic() < until:
            await self.session(bases)
            await self.pause()


async def probe_rate_limit(client: httpx.AsyncClient, instrument: str) -> str:
    """Sends detail requests from one address until 429, then checks that the API answers."""
    headers = {"CF-Connecting-IP": "203.0.113.250"}
    for sent in range(1, 1001):
        response = await client.get(f"{API}/instruments/{instrument}", headers=headers)
        if response.status_code == 429:
            retry = response.headers.get("retry-after")
            health = await client.get("/api/health")
            return (
                f"429 after {sent} requests from one address, Retry-After {retry}; "
                f"/api/health then {health.status_code}"
            )
    return "no 429 after 1000 requests from one address (CLIENT_RATE_LIMIT=0?)"


async def sample_memory(container: str, peak: list[float], stop: asyncio.Event) -> None:
    """Keeps the highest ``docker stats`` memory usage of ``container`` in ``peak[0]``."""
    command = ("docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", container)
    while not stop.is_set():
        process = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        output, _ = await process.communicate()
        if process.returncode == 0:
            peak[0] = max(peak[0], parse_memory(output.decode()))
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=1)


async def crypto_bases(client: httpx.AsyncClient) -> list[str]:
    """The base assets of the first crypto pairs in the catalog, the users' search words."""
    response = await client.get(f"{API}/instruments", params={"market": "crypto", "limit": 100})
    response.raise_for_status()
    bases = {item["base"] for item in response.json()["items"] if item["base"]}
    if not bases:
        raise SystemExit(
            "The catalog has no crypto pairs: is the backend up and Binance reachable?"
        )
    return sorted(bases)


async def main(args: argparse.Namespace) -> str:
    limits = httpx.Limits(max_connections=args.users + 2)
    async with httpx.AsyncClient(base_url=args.base_url, timeout=30, limits=limits) as client:
        # The market data is for signed-in accounts: one session of the dev sign-in for all.
        signed_in = await client.post("/api/v1/auth/dev", headers={"Sec-Fetch-Site": "same-origin"})
        signed_in.raise_for_status()
        bases = await crypto_bases(client)
        stats = Stats()
        stop = asyncio.Event()
        peak = [0.0]
        sampler = None
        if args.container:
            sampler = asyncio.create_task(sample_memory(args.container, peak, stop))
        began = time.monotonic()
        users = [
            User(client, stats, f"198.18.{n // 250}.{n % 250 + 1}", args.think)
            for n in range(args.users)
        ]
        async with asyncio.TaskGroup() as group:
            for n, user in enumerate(users):
                # Users arrive over the ramp-up, not all in the same second.
                await asyncio.sleep(args.ramp / args.users if n else 0)
                group.create_task(user.run(bases, began + args.duration))
        seconds = time.monotonic() - began
        probe = await probe_rate_limit(client, "crypto:BTCUSDT") if args.rate_limit_probe else None
        stop.set()
        if sampler is not None:
            await sampler
        return report(stats, seconds, peak[0] if args.container else None, probe)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load test of the data API.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--users", type=int, default=20, help="virtual users at once")
    parser.add_argument("--duration", type=float, default=60, help="seconds of load")
    parser.add_argument("--ramp", type=float, default=10, help="seconds until all users are in")
    parser.add_argument("--think", type=float, default=1, help="pause scale, 0 for none")
    parser.add_argument("--container", help="docker container of the backend, for its memory")
    parser.add_argument(
        "--rate-limit-probe", action="store_true", help="afterwards, check 429 from one address"
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.stdout.write(asyncio.run(main(parse_args())))
