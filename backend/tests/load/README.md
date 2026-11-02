# Load test

`load.py` puts the API under the load of many people using the chart at once (the market
scenario, the default) or the pages of their account (the account scenario, below) and prints a
Markdown report: requests per second, latency percentiles and answers per endpoint, the peak
memory of the backend container and whether the rate limit answers 429. It is run by hand, never
in CI, and never against stage or prod without the tech lead's OK.

## What a virtual user does

Each user repeats one session, with a pause of about `--think` seconds between steps:

1. **search**: types a crypto base asset letter by letter, `GET /instruments?q=b&market=crypto`,
   then `bt`, `btc`;
2. **detail**: opens the first result, `GET /instruments/{id}`;
3. **candles**: loads the 300 candles the chart shows first, in a random timeframe (`15m`, `1h`,
   `4h` or `1d`), `GET /instruments/{id}/candles`;
4. **scroll back**: loads the 300 candles before them, three times.

Only crypto, so no Alpaca keys are needed. Every user sends its own `CF-Connecting-IP`, as
Cloudflare does in front of stage and prod, so the per-client limit (`CLIENT_RATE_LIMIT`, 60 a
minute) counts each user apart. With `--rate-limit-probe`, after the run one more address sends
detail requests without a pause until it gets 429, and then `/api/health` must still answer.

## Run it

The market data is for signed-in accounts, so the script signs in with the dev sign-in, which
only `APP_ENV` `local` (the default of `.env.example`) and `test` have. From the repository
root, start the local stack and wait until the catalog is loaded:

```sh
docker compose up -d
curl -s -c /tmp/candlestack.cookies -H 'Sec-Fetch-Site: same-origin' -X POST http://127.0.0.1:8000/api/v1/auth/dev
curl -s -b /tmp/candlestack.cookies 'http://127.0.0.1:8000/api/v1/data/instruments?market=crypto&limit=1'
```

Then from `backend/`:

```sh
uv run python tests/load/load.py --users 50 --duration 120 --ramp 20 \
  --container candlestack-dev-backend-1 --rate-limit-probe
```

| Option | Default | Meaning |
| --- | --- | --- |
| `--base-url` | `http://127.0.0.1:8000` | the backend |
| `--scenario` | `market` | `market` for the chart, `account` for the signed-in pages |
| `--users` | 20 | virtual users at once |
| `--duration` | 60 | seconds of load |
| `--ramp` | 10 | seconds until all users are in |
| `--think` | 1 | scale of the pauses, 0 for none |
| `--container` | none | backend container whose memory `docker stats` samples every second |
| `--rate-limit-probe` | off | check the 429 afterwards |

## Reading the report

- An error is a 5xx or a request with no answer (timeout, refused connection); 4xx are counted
  apart. A 503 `source-unavailable` usually means our Binance weight budget
  (`BINANCE_WEIGHT_LIMIT`) is spent: the backend protects Binance's limit rather than crashing.
- The first requests for a window download Binance archives; later ones come from Redis, so the
  first minute is slower than the rest.
- The dev backend runs with `--reload`, which adds a watcher process to the container's memory:
  the peak is an upper bound of what the image uses. Compare it with the backend's limit in
  `infra/README.md` (`320m`).
- The load generator runs on the same machine as the backend and takes CPU from it.

## The account scenario

The Dashboard, Backtests and Models pages read the rows of one user from Postgres, so they are
measured with one full account. Every virtual user is the dev account and repeats one session,
with the same pauses as above:

1. **dashboard**: `GET /dashboard`;
2. **backtests**: the list as it opens, `GET /backtests`;
3. **backtests search**: a symbol, a model name, a number or a word that matches nothing,
   `GET /backtests?q=aapl`;
4. **backtests sort**: a random sort, order and filter, `GET /backtests?sort=sharpe&order=asc&show=starred`;
5. **backtests page**: a random later page, `GET /backtests?page=73`;
6. **models**: `GET /models`.

These endpoints have no per-client rate limit. First give the dev account about 1000 backtests
and 100 models: `fill.py` adds them straight to the database (models as rows only, without
files) and replaces its own earlier rows on a second run. From `backend/`, after the dev sign-in
above has made the account:

```sh
uv run python tests/load/fill.py --backtests 1000 --models 100
uv run python tests/load/load.py --scenario account --users 50 --duration 120 --ramp 20 \
  --container candlestack-dev-backend-1
```

To see the plans of the queries, let Postgres log each one with `EXPLAIN ANALYZE`
(`auto_explain`), restart the backend so its connections load it, open the pages once, and turn
it off again:

```sh
docker compose exec postgres psql -U candlestack -c "ALTER SYSTEM SET session_preload_libraries = 'auto_explain'" \
  -c "ALTER SYSTEM SET auto_explain.log_min_duration = 0" -c "ALTER SYSTEM SET auto_explain.log_analyze = on" \
  -c "SELECT pg_reload_conf()"
docker compose restart backend
docker compose logs postgres | grep -A40 "duration: .* plan"
docker compose exec postgres psql -U candlestack -c "ALTER SYSTEM RESET ALL" -c "SELECT pg_reload_conf()"
```
