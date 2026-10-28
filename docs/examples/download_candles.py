# /// script
# requires-python = ">=3.13"
# dependencies = ["httpx>=0.28.1", "polars>=1.44.2"]
# ///
"""Downloads the candles of one instrument from the CandleStack API into a Parquet file that
`candlestack-bt run --candles` reads: `ts` (open time, UTC epoch seconds), `open`, `high`,
`low`, `close` and `volume`. The guide: docs/api-guide.md.

The market data is for signed-in accounts, so the script sends your session cookie: copy its
value from the browser into CANDLESTACK_SESSION and never commit it.

    CANDLESTACK_SESSION=... uv run docs/examples/download_candles.py stock:AAPL 1h --since 2024-01-01
"""

import argparse
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import polars as pl

BASE_URL = "https://app.candlestack.tech"
PAGE = 5000  # the most candles one request with `limit` returns
RETRIES = 5  # waits for `Retry-After` before giving up on a request
COLUMNS = {"t": "ts", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}
SCHEMA = {"t": pl.Int64, **dict.fromkeys("ohlcv", pl.Float64)}


class ApiError(Exception):
    """A problem+json answer of the API: its `title` and `detail` say what to change."""


def client(base_url: str, session: str) -> httpx.Client:
    """A client signed in with the session cookie, named `__Host-session` over https."""
    name = "__Host-session" if base_url.startswith("https://") else "session"
    return httpx.Client(base_url=base_url, cookies={name: session}, timeout=60)


def get(
    api: httpx.Client, path: str, params: dict[str, str | int] | None = None
) -> dict:
    """The JSON of a GET; waits and asks again while the answer has `Retry-After` (429 when
    our 60 requests a minute are spent, 503 while the source's budget is)."""
    for _ in range(RETRIES):
        response = api.get(path, params=params)
        wait = response.headers.get("Retry-After")
        if response.status_code in (429, 503) and wait is not None:
            time.sleep(int(wait))
            continue
        if response.is_error:
            if not response.headers.get("Content-Type", "").startswith(
                "application/problem+json"
            ):
                raise ApiError(f"{response.status_code}: {response.text[:200]}")
            problem = response.json()
            raise ApiError(
                f"{response.status_code} {problem['title']}: {problem['detail']}"
            )
        return response.json()
    raise ApiError(f"Still limited after {RETRIES} waits: try again later")


def download(
    api: httpx.Client, instrument: str, timeframe: str, since: int | None
) -> pl.DataFrame:
    """The candles from `since` (the instrument's first candle without it) to now, paged back
    from now: each page is the `PAGE` candles before the start of the one after it."""
    first = get(api, f"/api/v1/data/instruments/{instrument}")["available_from"]
    since = max(since or 0, first or 0)
    pages = [pl.DataFrame(schema=SCHEMA)]
    params: dict[str, str | int] = {"timeframe": timeframe, "limit": PAGE}
    while True:
        page = get(api, f"/api/v1/data/instruments/{instrument}/candles", params)
        meta = page["meta"]
        pages.append(pl.DataFrame({name: page[name] for name in SCHEMA}, schema=SCHEMA))
        sys.stderr.write(f"{meta['count']} candles from {meta['start']}\n")
        # Missing candles (gaps) count towards `limit`: a shorter page is where the data starts.
        if meta["start"] <= since or meta["count"] + meta["gaps_total"] < PAGE:
            break
        params["end"] = meta["start"]
    candles = pl.concat(reversed(pages)).rename(COLUMNS)
    return candles.filter(pl.col("ts") >= since)


def epoch(text: str) -> int:
    """Epoch seconds, or an ISO 8601 date or time read as UTC."""
    if text.isdigit():
        return int(text)
    moment = datetime.fromisoformat(text)
    return int((moment if moment.tzinfo else moment.replace(tzinfo=UTC)).timestamp())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Downloads the candles of an instrument to Parquet."
    )
    parser.add_argument("instrument", help="for example stock:AAPL or crypto:BTCUSDT")
    parser.add_argument(
        "timeframe", choices=["1m", "5m", "15m", "30m", "1h", "4h", "1d"]
    )
    parser.add_argument(
        "--since", type=epoch, help="UTC date or epoch seconds; default: all"
    )
    parser.add_argument("--out", type=Path, default=Path("candles.parquet"))
    parser.add_argument("--base-url", default=BASE_URL)
    args = parser.parse_args(argv)
    session = os.environ.get("CANDLESTACK_SESSION")
    if not session:
        parser.error(
            "set CANDLESTACK_SESSION to your session cookie (docs/api-guide.md)"
        )
    with client(args.base_url, session) as api:
        try:
            candles = download(api, args.instrument, args.timeframe, args.since)
        except ApiError as error:
            sys.stderr.write(f"{error}\n")
            return 1
    candles.write_parquet(args.out)
    sys.stderr.write(f"Wrote {candles.height} candles to {args.out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
