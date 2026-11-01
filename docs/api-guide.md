# Using the API from a script

How to pull instruments and candles from the CandleStack API into your own code, and save them
as the `candles.parquet` that [`candlestack-bt run`](engine.md) reads. The contract with every
field and rule is [data.md](data.md); the reference of every endpoint is at
https://app.candlestack.tech/api/v1/docs.

The ready example is [examples/download_candles.py](examples/download_candles.py) (httpx and
polars); this guide explains what it does.

## Sign in

The market data is for signed-in accounts: without a session the instrument list, an instrument
and its candles answer 401 `not-signed-in`. An account exists only for someone on the beta list,
so ask the team to add you if signing in is refused. The API has no keys; a script sends the
session cookie of your browser:

1. Sign in at https://app.candlestack.tech.
2. In the browser's developer tools (Application, then Cookies, in Chrome; Storage in Firefox)
   copy the value of the cookie `__Host-session`.
3. Put it into the environment variable `CANDLESTACK_SESSION` of the shell that runs the script.

The cookie is your account: never commit it, paste it into an issue or share it. A session lasts
30 days from its last use; signing out ends it.

The stock data comes from Alpaca, whose terms allow it for personal, noncommercial use only: keep
the files you download to yourself, do not publish them or pass them on.

On a local stack (`docker compose up`, see the [README](../README.md)) sign in as the dev user
from the shell instead. The request must come from the app's origin:

```sh
curl -si -X POST -H 'Origin: http://localhost:5173' http://localhost:8000/api/v1/auth/dev
```

Copy the value of `session` from its `Set-Cookie` header into `CANDLESTACK_SESSION` and pass
`--base-url http://localhost:8000` to the example.

```sh
export CANDLESTACK_SESSION='...'
uv run docs/examples/download_candles.py stock:AAPL 1h --since 2024-01-01 --out candles.parquet
uv run --project backend candlestack-bt run --candles candles.parquet \
    --predictions predictions.parquet --out run
```

`uv run` installs httpx and polars for the example from the header of the file.

## Find an instrument

An instrument id is `<market>:<symbol>`: `crypto:BTCUSDT`, `stock:AAPL`. Search the catalog with
`GET /api/v1/data/instruments`, one page at a time (`limit` up to 100, `offset` to go on):

```python
import os

import httpx

api = httpx.Client(
    base_url="https://app.candlestack.tech",
    cookies={"__Host-session": os.environ["CANDLESTACK_SESSION"]},
    timeout=60,  # a cold request can take longer than httpx's default of 5 seconds
)
page = api.get("/api/v1/data/instruments", params={"q": "apple", "market": "stock"}).json()
for item in page["items"]:
    print(item["id"], item["name"], item["price"])
```

`total` counts every match; `unavailable` names a market whose catalog could not be loaded yet,
so the page holds the other one only. `GET /api/v1/data/instruments/{id}` returns one instrument
with its `timeframes` and `available_from`, the open time of its first 1m candle (a longer
timeframe's first candle opens at the start of the bin that holds it).

## Page through candles

`GET /api/v1/data/instruments/{id}/candles?timeframe=1h&end=<time>&limit=<n>` returns the last
`limit` candles (1 to 5000, default 500) that open before `end` (default: now). The arrays are
columns: index `i` of `t`, `o`, `h`, `l`, `c` and `v` is one candle, `t` its open time in UTC
epoch seconds.

To get more than 5000 candles, page back in time: the next request's `end` is `meta.start` of
the page you got, where the page starts (`end` is exclusive, so no candle comes twice). Stop when
`meta.start` reaches the time you want, or when a page is shorter than `limit`: that is where
the data starts. Missing candles (`meta.gaps`, `meta.gaps_total`) count towards `limit`, so
compare `meta.count + meta.gaps_total`, not `meta.count` alone. Start `stop` at
`available_from` at the earliest: when the history is an exact multiple of `limit`, no page is
short, and a request that ends at the first candle is refused with 422 `period-out-of-range`.
The pages can hold candles before `since`; the next section drops them:

```python
instrument, since = "stock:AAPL", 1_704_067_200  # 2024-01-01T00:00Z
first = api.get(f"/api/v1/data/instruments/{instrument}").json()["available_from"]
stop = max(since, first or 0)
params = {"timeframe": "1h", "limit": 5000}
pages = []
while True:
    page = api.get(f"/api/v1/data/instruments/{instrument}/candles", params=params).json()
    pages.append(page)
    meta = page["meta"]
    if meta["start"] <= stop or meta["count"] + meta["gaps_total"] < 5000:
        break
    params["end"] = meta["start"]
```

A request for a period instead (`start` and `end`, no `limit`) works too, up to 50000 candles.
Stocks have candles only in the regular session (09:30-16:00 New York), so nights, weekends and
holidays are no gaps.

## Save them for the backtest

`candlestack-bt run --candles` reads a Parquet file with the time column `ts` (integer epoch
seconds, the open time) and `open`, `high`, `low`, `close`. Join the pages oldest first and
rename the columns:

```python
import polars as pl

columns = {"t": "ts", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}
candles = pl.concat(
    pl.DataFrame({name: page[name] for name in columns}) for page in reversed(pages)
).rename(columns)
candles.filter(pl.col("ts") >= since).write_parquet("candles.parquet")
```

The times are open times, the default of `--labels open`. The predictions file of the run needs
the same `ts` ([engine.md](engine.md)).

## Rate limit

The API takes 60 candle and instrument detail requests a minute from one IP address (the
instrument search is not counted). Past that it answers 429 `rate-limited` with a `Retry-After`
header: the seconds until the next minute. A cold request for a long stock history can also
answer 503 `source-unavailable` with `Retry-After` while the source's budget is spent; what it
fetched so far stays cached, so the same request continues from there. In both cases wait `Retry-After` seconds and send it again:

```python
import time

def get(path, params=None):
    while True:
        response = api.get(path, params=params)
        wait = response.headers.get("Retry-After")
        if response.status_code in (429, 503) and wait is not None:
            time.sleep(int(wait))
            continue
        response.raise_for_status()
        return response.json()
```

The example gives up after 5 waits.

## Errors

Every error is an RFC 9457 `application/problem+json` body: `type`
(`https://candlestack.tech/problems/<slug>`), `title`, `status` and a `detail` that says what to
change. Some add fields, such as `errors` of a 422 `validation`:

```json
{
  "type": "https://candlestack.tech/problems/rate-limited",
  "title": "Too many requests",
  "status": 429,
  "detail": "More than 60 candle and instrument requests in one minute from this address. Retry in 12 seconds.",
  "limit": 60
}
```

| Status | Slug | What to do |
| --- | --- | --- |
| 401 | `not-signed-in` | the cookie is missing, wrong or its session ended: copy it again |
| 404 | `instrument-not-found` | check the id with the instrument search |
| 422 | `validation` | fix the parameters `errors` names |
| 422 | `period-out-of-range` | `start` before the first candle, `end` at or before it (paging past the start of the data) or `end` in the future: `available_from` and `available_to` say what fits |
| 422 | `too-many-candles` | a shorter period, a longer timeframe or paging (`suggestion`) |
| 429 | `rate-limited` | wait `Retry-After` seconds |
| 502 | `source-data-invalid` | the source sent bad data: try later, or report it |
| 503 | `source-unavailable` | wait `Retry-After` seconds when it is there; without it the source is down |

The full list with every field: [data.md](data.md#errors).
