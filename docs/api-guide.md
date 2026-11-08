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
2. Click Create key, give it a name and the scope `read` (all this guide needs, always on) and
   copy it: it starts with `cs_` and is shown only once. CandleStack keeps just its SHA-256, so
   a lost key cannot be shown again; revoke it and create a new one.
3. Put it into the environment variable `CANDLESTACK_API_KEY` of the shell that runs the
   script. The script sends it as `Authorization: Bearer cs_...`.

The key is your account for what its scopes allow: never commit it, paste it into an issue or
share it. Give a key only the scopes it needs:

| Scope | What the key may do |
| --- | --- |
| `read` | every GET: market data, your models, backtests and their result documents, Experiments |
| `backtests` | start, rename and star backtests, save your backtest defaults |
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

## The same key in an MCP client

The key also signs in an MCP client, with the same scopes: the MCP server is at
`https://app.candlestack.tech/mcp` (Streamable HTTP), locally at `http://localhost:8000/mcp`. In
Claude Code:

```sh
claude mcp add --transport http candlestack https://app.candlestack.tech/mcp \
    --header "Authorization: Bearer $CANDLESTACK_API_KEY"
```

Its tool `get_me` answers who the key signs in as and its scopes. The other clients and every
tool are on https://candlestack.tech/docs/mcp/.

claude.ai and ChatGPT need no key: add `https://app.candlestack.tech/mcp` as a custom connector,
and the app sends you through the CandleStack sign-in (OAuth 2.1) to a page where you choose
what it may do, with the same scopes as keys. Settings, API and MCP lists those connected apps,
and Disconnect ends an app's access at once. An OAuth access token works at `/mcp` only, not on
the HTTP API.

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
instrument search is not counted), 120 requests a minute with one API key, and 300 a minute
across all the keys and connected apps of one account. A heavy request counts as 5: a backtest's
result document (1 with `?view=summary`), its download, a comparison, starting a backtest or a
sweep. Past any of them it answers 429 `rate-limited` with a `Retry-After` header and
`retry_after_s` in the body: the seconds until the next minute. A cold request for a long stock
history can also answer 503 `source-unavailable` with `Retry-After` while the source's budget is spent; what it
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

## Check a backtest before starting it

`POST /api/v1/backtests/check` takes the body of `POST /api/v1/backtests` and runs the same
checks without starting anything: the model is yours and has a manifest, the instrument fits
the model, the period with the candles the model needs before it fits the data, your queue has
room. It answers the same problem a start would, or 200 with what would run: the instrument as
the catalog has it, the model's timeframe and session, the warm-up and where the load starts,
and whether it would start now or wait in your queue (`starts`, `queue_position`). A key needs
only the scope `read`:

```python
setup = {
    "model": {"id": "mdl_k2m9qx47f3"},
    "data": {"instrument": {"id": "stock:AAPL"},
             "period": {"start": 1_717_372_800, "end": 1_725_148_800}},
}
check = api.post("/api/v1/backtests/check", json=setup)
if check.status_code == 200:
    started = api.post("/api/v1/backtests", json=setup).json()
```

A start may time out on your side after the backtest was stored. Send an `Idempotency-Key`
header (any 1 to 255 visible ASCII characters, such as a UUID) with `POST /api/v1/backtests`
and `POST /api/v1/sweeps`, and send the same request again with the same key: within 24 hours
it answers the first answer again, with `Idempotent-Replayed: true`, instead of starting a
second one, also when the first request stopped after it stored the backtest (then the
answer is the backtest as it is now). The same key with another body is 422
`idempotency-key-reused`; while the first request is still being answered and has stored
nothing yet it is 409 `idempotency-key-in-use`: wait `retry_after_s`. Deleting the backtest
does not free its key: the same request with it still answers the first answer (201,
`Idempotent-Replayed: true`) for a backtest that no longer exists, so start it again with a new
key. A start your queue has no room for is 409 `queue-full` with `retry_after_s` (and `Retry-After`),
about when one of your queued backtests has started.

## Compare backtests

`GET /api/v1/compare?ids=bt_7f3k2m9qx4,bt_t9n8v7d6a5` compares 1 to 20 of your backtests and
others' published ones as the app's Compare page does: each statistic of each with the best and
the worst marked (`metrics`), the setups and which fields differ (`differs`), and the equity,
drawdown and buy and hold curves on one time axis of at most 200 times (`times`). A running or failed
backtest comes with its status and no values; an ID you cannot see is a 404 whose `id` names it.
It reads only what each backtest stored, so comparing many loads no candles and spends none of
the data sources' request budget. The fields: [architecture.md](architecture.md#compare).

## Run a sweep

`POST /api/v1/sweeps`, with a key of the scope `backtests`, starts a backtest for every
combination of a base setup (the body of `POST /api/v1/backtests`) and the values to vary:
`models`, `instruments`, `periods` and decision `rules`. Two models and three thresholds make
six backtests:

```json
{
  "base": {
    "model": {"id": "mdl_k2m9qx47f3"},
    "data": {"instrument": {"id": "stock:AAPL"}, "period": {"start": 1704067200, "end": 1735689600}},
    "decision": {"rule": {"name": "threshold", "limit": 0.1}},
    "benchmarks": [{"id": "bh", "kind": "buy_and_hold", "instrument": "stock:AAPL"}]
  },
  "vary": {
    "models": ["mdl_k2m9qx47f3", "mdl_t9n8v7d6a5"],
    "rules": [
      {"name": "threshold", "limit": 0.05},
      {"name": "threshold", "limit": 0.1},
      {"name": "threshold", "limit": 0.2}
    ]
  },
  "metric": "sharpe"
}
```

A sweep runs 2 to 20 backtests, and your queue needs room for all of them (up to 20 wait):
else 422 `validation` or 409 `queue-full`, with `count`. Add `"validate_only": true` to check
every setup without starting anything: the answer is the `count` and each setup. A setup that
fails a check answers with that check's problem and `setup`, its place in the grid from 1.
To vary what a model reads or its timeframe, list models whose manifests read them. The base
still needs a model and an instrument when you vary them. An unknown field, such as a mistyped
`validate_only`, is a 422 `validation`, never a sweep started.

`GET /api/v1/sweeps/<id>` has the `progress`, each backtest's ID, status and setup, and once
`done` the `ranking` by the metric, best first, marked as Compare marks it. Ask with
`?metric=max_drawdown` for another order. The backtests are ordinary ones with `sweep_id`, so
`GET /api/v1/compare` with their IDs compares them in full, and
`GET /api/v1/backtests?sweep_id=<id>` lists them. `GET /api/v1/sweeps` lists your sweeps,
newest first, each with its progress and its best backtest so far. In an MCP client the same is
`validate_sweep`, `run_sweep`, `get_sweep`, `list_sweeps` and `wait_for_sweep`, which waits
until every backtest of the sweep ended and answers the ranking. `validate_sweep` needs only the
scope `read`, while the HTTP dry run needs `backtests`. The fields:
[architecture.md](architecture.md#sweeps).

## Find backtests

`GET /api/v1/backtests` lists your backtests, 10 a page or up to 100 with `per_page`. Besides
the search `q`, it filters by `status` (repeat it for several), `sweep_id`, `model_id`,
`instrument` (`stock:AAPL`), `timeframe`, `representation`, `created_after` and
`created_before`, `min_sharpe`, `min_total_return`, `min_max_drawdown` (the drawdown is 0 or
below, so `-0.2` keeps falls of up to 20 %), `starred` and `public`; every filter given has to
hold. `sort` also takes `win_rate` and `sortino`:

```python
best = api.get("/api/v1/backtests", params={
    "status": "finished", "instrument": "stock:AAPL", "min_max_drawdown": -0.2,
    "sort": "sharpe", "per_page": 50,
}).json()["backtests"]
```

In an MCP client `list_backtests` takes the same filters, and answers the counts and the models
used only on page 1 unless `include_facets` asks.

## Upload a model through a link

A script can send the file itself (`POST /api/v1/models?name=lstm.keras` with the file as the
body). An agent that cannot pass a file through a chat asks for a one-time link instead, with a
key of the scope `models`:

```sh
curl -s -X POST -H "Authorization: Bearer $CANDLESTACK_API_KEY"     -H 'Content-Type: application/json' -d '{"name": "lstm.keras", "size_bytes": 14889779}'     https://app.candlestack.tech/api/v1/models/uploads
```

The answer holds the link's `id`, its `url`, `expires_at` (15 minutes), the `page_url` of its
upload page and the `command` that sends the file from the folder it is in:
`curl --fail-with-body -T lstm.keras https://app.candlestack.tech/api/v1/uploads/<token>`
(it exits with an error and prints the problem when the upload is refused). That request needs
no key: the token in the link is its only sign-in, so treat the link like a
key until it is used. A link takes one file of the name's format, no larger than `size_bytes`,
and the file is checked as any upload. `GET /api/v1/models/uploads/<id>` says how it stands:
`waiting`, `checking`, `done` with the `model_id`, `failed` with the `problem` and the `check`
that failed (`upload-interrupted` when the upload stopped midway), or `expired`. A used or
expired link answers 410 `upload-link-gone`: ask for a new one.

When the agent cannot run `curl` (claude.ai, ChatGPT), it gives the user the `page_url`
instead: signed in to CandleStack, the user drops the file there, sees it upload and pass the
checks, and gets a link to the new model. The page checks the file's size and format before it
sends anything, so a wrong file leaves the link usable.

## Check a manifest before setting it

A model runs once it has a manifest (`PUT /api/v1/models/<id>/manifest` with the manifest as
the body, a key of the scope `models`). To try one first, `POST` it to
`/api/v1/models/<id>/manifest/check` with a `read` key: the same checks run and nothing is
kept. The answer says whether every check passed (`valid`) and, if not, lists what is wrong,
in a form a script or an agent can fix the manifest from:

```json
{
  "valid": false,
  "problems": [
    {
      "check": "schema",
      "field": "input.session",
      "detail": "input.session: Input should be 'regular' or '24/7'.",
      "allowed": ["regular", "24/7"]
    }
  ],
  "more_problems": 0,
  "unknown_blocks": []
}
```

`field` is the path the problem is about (null for the whole manifest) and `allowed` what fits
there when the check knows it (the blocks of the step, the fields of the object, the options of
a choice, the shape to give). A refused `PUT` answers `422 manifest-check-failed` with the same
`problems`. The format: [preprocessing.md](preprocessing.md#the-manifest).

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
  "limit": 60,
  "retry_after_s": 12
}
```

| Status | Slug | What to do |
| --- | --- | --- |
| 401 | `not-signed-in` | no `Authorization` header came with the request: set `CANDLESTACK_API_KEY` |
| 401 | `invalid-api-key` | the key is wrong, revoked or expired, or your account left the beta list: create a new key |
| 403 | `missing-scope` | the key lacks the scope `scope` names: create a key with it |
| 403 | `session-required` | keys cannot do this: do it in the app |
| 404 | `instrument-not-found` | check the id with the instrument search |
| 409 | `queue-full` | your queue holds 20 waiting backtests: try again in `retry_after_s` seconds, or cancel one |
| 409 | `idempotency-key-in-use` | the first request with this `Idempotency-Key` is still being answered: send it again in `retry_after_s` seconds |
| 404 | `upload-link-not-found` | no upload link has the token: copy the whole link or ask for a new one |
| 410 | `upload-link-gone` | the upload link was used or expired: ask for a new one |
| 413 | `larger-than-link` | the file is larger than the upload link's `size_bytes`: ask for a new link with its size |
| 422 | `validation` | fix the parameters `errors` names |
| 422 | `idempotency-key-reused` | the `Idempotency-Key` came with another body in the last 24 hours: use a new key for a new request |
| 422 | `period-out-of-range` | `start` before the first candle, `end` at or before it (paging past the start of the data) or `end` in the future: `available_from` and `available_to` say what fits |
| 422 | `too-many-candles` | a shorter period, a longer timeframe or paging (`suggestion`) |
| 429 | `rate-limited` | wait `Retry-After` (`retry_after_s`) seconds |
| 502 | `source-data-invalid` | the source sent bad data: try later, or report it |
| 503 | `source-unavailable` | wait `Retry-After` seconds when it is there; without it the source is down |

The full list with every field: [data.md](data.md#errors).
