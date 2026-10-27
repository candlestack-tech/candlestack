# Load test

`load.py` puts the data API under the load of many people using the chart at once and prints a
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
