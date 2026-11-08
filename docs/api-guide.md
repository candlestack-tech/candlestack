# Using the API from a script

How to pull instruments and candles from the CandleStack API into your own code, and save them
as the `candles.parquet` that [`candlestack-bt run`](engine.md) reads. The contract with every
field and rule is [data.md](data.md); the reference of every endpoint is at
https://app.candlestack.tech/api/v1/docs.

The ready example is [examples/download_candles.py](examples/download_candles.py) (httpx and
polars); this guide explains what it does.

## Sign in with an API key

The market data is for signed-in accounts: without a key or a session the instrument list, an
instrument and its candles answer 401 `not-signed-in`. An account exists only for someone on
the beta list, so ask the team to add you if signing in is refused. A script signs in with an
API key:

1. Sign in at https://app.candlestack.tech and open Settings, API and MCP.
2. Create a key with the scope `read` (all this guide needs) and copy it: it starts with `cs_`
   and is shown only once. CandleStack keeps just its SHA-256, so a lost key cannot be shown
   again; revoke it and create a new one.
3. Put it into the environment variable `CANDLESTACK_API_KEY` of the shell that runs the
   script. The script sends it as `Authorization: Bearer cs_...`.

Until the keys card is in Settings, create a key with `POST /api/v1/me/api-keys` from the
browser's developer console on a page of the app, signed in:

```js
await (await fetch("/api/v1/me/api-keys", {method: "POST",
  headers: {"Content-Type": "application/json"},
  body: JSON.stringify({name: "my script", scopes: ["read"]})})).json()
```

The key is your account for what its scopes allow: never commit it, paste it into an issue or
share it. Give a key only the scopes it needs:

| Scope | What the key may do |
| --- | --- |
| `read` | every GET: market data, your models, backtests and their result documents, Experiments |
| `backtests` | start, rename and star backtests |
| `models` | upload models, add their manifests, rename, star and delete them |
| `publish` | publish and unpublish backtests |

Creating, listing and revoking keys, deleting the account, sign-in methods and signing out work
only in the app, never with a key. A key stops working when you revoke it, when it expires (if
you gave it an expiry) and when your account leaves the beta list. A request with a key sends no
cookies: with a cookie, a change is accepted only from the app's own pages.

The stock data comes from Alpaca, whose terms allow it for personal, noncommercial use only: keep
the files you download to yourself, do not publish them or pass them on.

On a local stack (`docker compose up`, see the [README](../README.md)) sign in as the dev user
from the shell and create a key with that session. Both requests must come from the app's
origin:

```sh
curl -si -X POST -H 'Origin: http://localhost:5173' http://localhost:8000/api/v1/auth/dev
curl -s -X POST -H 'Origin: http://localhost:5173' -H 'Cookie: session=<the session cookie>' \
    -H 'Content-Type: application/json' -d '{"name": "local", "scopes": ["read"]}' \
    http://localhost:8000/api/v1/me/api-keys
```

The first prints the cookie `session` in its `Set-Cookie` header; the second answers the new
key in `secret`. Put it into `CANDLESTACK_API_KEY` and pass `--base-url http://localhost:8000`
to the example.

```sh
export CANDLESTACK_API_KEY='cs_...'
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
    headers={"Authorization": f"Bearer {os.environ['CANDLESTACK_API_KEY']}"},
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
instrument search is not counted), and 120 requests a minute with one API key, whatever they
ask for. Past either it answers 429 `rate-limited` with a `Retry-After` header: the seconds
until the next minute. A cold request for a long stock history can also
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

## Compare backtests

`GET /api/v1/compare?ids=bt_7f3k2m9qx4,bt_t9n8v7d6a5` compares 2 to 20 of your backtests and
others' published ones as the app's Compare page does: each statistic of each with the best and
the worst marked (`metrics`), the setups and which fields differ (`differs`), and the equity,
drawdown and buy and hold curves on one time axis of at most 200 times (`times`). A running or failed
backtest comes with its status and no values; an ID you cannot see is a 404 whose `id` names it.
The fields: [architecture.md](architecture.md#compare).

## Upload a model through a link

A script can send the file itself (`POST /api/v1/models?name=lstm.keras` with the file as the
body). An agent that cannot pass a file through a chat asks for a one-time link instead, with a
key of the scope `models`:

```sh
curl -s -X POST -H "Authorization: Bearer $CANDLESTACK_API_KEY"     -H 'Content-Type: application/json' -d '{"name": "lstm.keras", "size_bytes": 14889779}'     https://app.candlestack.tech/api/v1/models/uploads
```

The answer holds the link's `id`, its `url`, `expires_at` (15 minutes) and the `command` that
sends the file from the folder it is in:
`curl --fail-with-body -T lstm.keras https://app.candlestack.tech/api/v1/uploads/<token>`
(it exits with an error and prints the problem when the upload is refused). That request needs
no key: the token in the link is its only sign-in, so treat the link like a
key until it is used. A link takes one file of the name's format, no larger than `size_bytes`,
and the file is checked as any upload. `GET /api/v1/models/uploads/<id>` says how it stands:
`waiting`, `checking`, `done` with the `model_id`, `failed` with the `problem` and the `check`
that failed (`upload-interrupted` when the upload stopped midway), or `expired`. A used or expired link answers 410 `upload-link-gone`: ask for a new
one.

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
| 401 | `not-signed-in` | no `Authorization` header came with the request: set `CANDLESTACK_API_KEY` |
| 401 | `invalid-api-key` | the key is wrong, revoked or expired, or your account left the beta list: create a new key |
| 403 | `missing-scope` | the key lacks the scope `scope` names: create a key with it |
| 403 | `session-required` | keys cannot do this: do it in the app |
| 404 | `instrument-not-found` | check the id with the instrument search |
| 404 | `upload-link-not-found` | no upload link has the token: copy the whole link or ask for a new one |
| 410 | `upload-link-gone` | the upload link was used or expired: ask for a new one |
| 413 | `larger-than-link` | the file is larger than the upload link's `size_bytes`: ask for a new link with its size |
| 422 | `validation` | fix the parameters `errors` names |
| 422 | `period-out-of-range` | `start` before the first candle, `end` at or before it (paging past the start of the data) or `end` in the future: `available_from` and `available_to` say what fits |
| 422 | `too-many-candles` | a shorter period, a longer timeframe or paging (`suggestion`) |
| 429 | `rate-limited` | wait `Retry-After` seconds |
| 502 | `source-data-invalid` | the source sent bad data: try later, or report it |
| 503 | `source-unavailable` | wait `Retry-After` seconds when it is there; without it the source is down |

The full list with every field: [data.md](data.md#errors).
