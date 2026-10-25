# Market data contract

The `data` module serves instruments and candles for two markets: crypto from Binance and US
stocks from Alpaca. Nothing is stored: data is fetched from the source on request, validated,
cached in Redis and returned. This document is the contract for the `/api/v1/data` endpoints
and for the code behind them. Architecture and configuration: [architecture.md](architecture.md).

## Sources

| Market | Source | Feed | Instruments | Candles | Auth |
| --- | --- | --- | --- | --- | --- |
| `crypto` | Binance spot | `spot` | `GET /api/v3/exchangeInfo?symbolStatus=TRADING&showPermissionSets=false`: every symbol with `status` `TRADING` | kline archives on `data.binance.vision`, newest days from `GET /api/v3/klines` | none |
| `stock` | Alpaca, paper account | `sip` | `GET /v2/assets?status=active&asset_class=us_equity`: `tradable` true, `exchange` not `OTC` (US equities and ETFs) | `GET /v2/stocks/bars` with `feed=sip` and `adjustment=all`, up to 15 minutes ago; trading days from `GET /v2/calendar` | `APCA-API-KEY-ID`, `APCA-API-SECRET-KEY` headers |

- One source per market; sources are never mixed within one series.
- History: Binance from each pair's listing; Alpaca SIP from 2016 (per instrument: its first
  `1Day` bar, 2016-01-04 for most stocks listed then).
- Binance: spot only (no futures, no margin). Volume is in the base asset (BTC for BTCUSDT).
- Alpaca SIP is the consolidated feed of every US exchange: prices and volumes are the whole
  market's. The free Basic plan, the default of paper accounts, serves it only for a query whose
  `end` is at least 15 minutes old, so **stock data is 15 minutes behind**: no request reaches
  into the last 15 minutes, and their candles come once they are that old (see
  [What is fetched](#what-is-fetched)). The stats come from the snapshots of `feed=delayed_sip`,
  SIP delayed by 15 minutes. `feed=sip` is always sent explicitly. Until #194 stocks came from
  the IEX feed (IEX trades only, from 2020-07-27).
- `adjustment=all` applies splits and dividends to the whole history, so past stock candles
  change after a corporate action (see [Fingerprint](#fingerprint)).
- Instrument icons come from a third source, logo.dev (see [Icons](#icons)); without an icon
  the instruments and candles work the same.
- The instrument list's 24 h stats (last price, change, volume) come from Binance's 24 h ticker
  and Alpaca's snapshots, refreshed in the background (see [Stats](#stats)); its mini charts of
  the last 10 daily candles from Binance's klines and Alpaca's daily bars, refreshed once a day
  (see [Mini charts](#mini-charts)).

## Instruments

Instrument id: `<market>:<symbol>`, for example `crypto:BTCUSDT`, `stock:AAPL`, `stock:BRK.B`.
Parsing trims whitespace, lower-cases the market and upper-cases the symbol
(`crypto:btcusdt` is `crypto:BTCUSDT`). Symbols are spelled as the source spells them: letters
and digits of any script (Binance lists pairs such as `币安人生USDT`) and dots, at most 32
characters.

| Field | Crypto | Stock |
| --- | --- | --- |
| `id` | `crypto:BTCUSDT` | `stock:AAPL` |
| `market` | `crypto` | `stock` |
| `symbol` | `BTCUSDT` | `AAPL` |
| `name` | `BTC/USDT` (base/quote) | Alpaca asset name |
| `source` | `binance` | `alpaca` |
| `feed` | `spot` | `sip` |
| `exchange` | `BINANCE` (`null` before #182) | Alpaca exchange (`NASDAQ`, `NYSE`, `ARCA`, ...); `null` for one outside the `exchange` filter's list |
| `base`, `quote` | `BTC`, `USDT` | `null` |

Since #182 crypto pairs carry `"exchange": "BINANCE"` where they had `"exchange": null`, in
the search, the instrument and every other answer with an instrument. The field stays nullable
(a stock on an exchange outside the list below has `null`), so a client still handles `null`.

`GET /api/v1/data/instruments?q=&market=&exchange=&sort=&limit=&offset=` searches the cached catalogs of both
markets (no source call per item) and returns one page,
`{"items": [<instrument>, ...], "count": <n>, "total": <all matches>, "offset": <offset>, "unavailable": []}`.
Each item is an instrument with its 24 h stats (see [Stats](#stats)): `price`, `change_24h` and
`volume_24h`, each `null` while the instrument has none, its mini chart `spark` (see
[Mini charts](#mini-charts)) and `available_from` (see [First candles in the
list](#first-candles-in-the-list)).

| Parameter | Rule |
| --- | --- |
| `q` | optional, 1-50 characters; without it, every instrument in the order of `sort` (equal symbols: crypto first). A query without letters or digits finds nothing. Case-insensitive; separators are ignored in symbols: `btc/usdt`, `btc-usdt` and `BTC USDT` all match `BTCUSDT`, `brkb` matches `BRK.B` |
| `market` | optional, `crypto` or `stock` |
| `exchange` | optional, `AMEX`, `ARCA`, `BATS`, `BINANCE`, `NASDAQ` or `NYSE` (upper case); only the instruments that trade there. `BINANCE` lists the crypto pairs and the others stocks, so with the other `market` (`market=crypto&exchange=NYSE`) nothing matches. Only the catalog of the exchange's market is searched: while it cannot be loaded, the answer is 503 `source-unavailable`, not 200 with `unavailable`. Works with `q`, `market` and paging. A stock Alpaca reports on an exchange outside this list (or on `BINANCE`) gets `exchange: null`, and the values are logged as a warning when the catalog is fetched from Alpaca |
| `sort` | optional: `volume` (default: 24 h volume in USD, highest first, so the most traded crypto pairs and stocks come first, mixed), `change` (24 h change, highest first: the top gainers), `-change` (lowest first: the top losers) or `symbol` (by symbol, the order before #184). Instruments without stats come last in `volume`, `change` and `-change`, by symbol among themselves. With `q` the ranking below stays first and `sort` only breaks its ties |
| `limit` | default 20, max 100 |
| `offset` | default 0; matches to skip. Past the end, `items` is empty and `total` still counts all matches |

Ranking: the top pair of a crypto base asset equal to the query, the one whose quote asset has
the most pairs in the catalog (USDT, so `btc` finds `crypto:BTCUSDT` first, before the stock
`BTC`); exact symbol; the other pairs of that base asset, most common quote asset first; symbol
prefix; prefix of a word in the name; substring of the symbol; substring of the name. Other
ties: `sort` (by default the higher 24 h volume first; `symbol` adds nothing), then the shorter
symbol, then alphabetical. Before #184 the list without `q` was in symbol order and ties of the
ranking went to the shorter symbol: `sort=symbol` gives both.

Once the catalog of one market is loaded, a search over both markets does not wait for the
other: it is loaded in the background (retried at most every 30 s while its source is down and
Redis does not have it), and until then the search answers from the loaded market and names
the missing one in `unavailable` (`["stock"]`). A search that can reach no requested market is
503 `source-unavailable`.

`GET /api/v1/data/instruments/{id}` returns the instrument plus what a client needs to build a
valid candle request (404 `instrument-not-found` if the id is not in the catalog). It counts
against the client rate limit like candle requests, because its first-candle lookup can call the
source:

```json
{
  "id": "stock:AAPL",
  "market": "stock",
  "symbol": "AAPL",
  "name": "Apple Inc. Common Stock",
  "source": "alpaca",
  "feed": "sip",
  "exchange": "NASDAQ",
  "base": null,
  "quote": null,
  "timeframes": ["1m", "5m", "15m", "30m", "1h", "4h", "1d"],
  "available_from": 1451917800,
  "available_to": 1790380620,
  "max_candles": 50000
}
```

- `available_from`: open time of the instrument's first candle: the first 1m kline of a
  crypto pair, the session open of the first `1Day` bar of a stock; `null` when the source has
  no candle yet or cannot be asked. For a longer timeframe the first candle opens at the start
  of the bin that holds this moment (BTCUSDT: 1m from 2017-08-17T04:00Z, 1d from 00:00Z).
- `available_to`: open time of the newest closed 1m bin when the answer was made; for stocks
  the last minute of the latest session that has begun (15:59 New York after the close). A
  stock's candles of the last 15 minutes are not there yet: they are neither returned nor gaps.
- `max_candles`: `CANDLES_MAX`.

Values in the examples of this document are illustrative.

### First candles in the list

Each item of the list also carries `available_from`, the open time of its first candle in UTC
epoch seconds, as in the instrument detail. It is `null` while the backend does not know it
yet, and for an instrument without any candle yet. Finding it costs one source request per
instrument (Binance `klines`, weight 2; one Alpaca request), so the list never asks a source for
it while it answers:

- The first candles of the page come from Redis (`data:vN:first:<market>:<symbol>`, the keys the
  instrument detail fills) in one `MGET`.
- Up to 5 instruments of the page that Redis does not know yet, its first rows first, are
  looked up in the background without delaying the answer, so a later request has them; one
  already being looked up by this process is skipped, and the single-flight lock stops two
  processes from asking for the same one. A process runs at most 10 such lookups at once, and all
  processes together at most 60 a minute for crypto and 6 a minute for stocks
  (`data:rl:first-warm:<market>`, a tenth of the smallest `ALPACA_RATE_LIMIT`); each also
  counts against the source's own budget, so the charts keep the rest.
- When a lookup finds the source unavailable (down, our budget spent, no Alpaca keys), the
  lookups of that market stop for a minute (or the `Retry-After`, when longer), with one
  warning. When the source refuses one instrument (HTTP 4xx, e.g. a pair delisted since the
  catalog was read) or sends invalid data for it, only that instrument is not looked up for an
  hour, with one warning. Without Redis nothing is looked up, as nothing could be kept.

## Stats

The instrument list shows and sorts by three numbers per instrument, its 24 h stats. A
background task of the backend refreshes them (prod and stage, `STATS_REFRESH`) and keeps them
in Redis, so a search never waits for a source; the other environments have none unless
`STATS_REFRESH=true`.

| Field | Crypto (Binance) | Stock (Alpaca, SIP 15 minutes delayed) |
| --- | --- | --- |
| `price` | `lastPrice`, in the quote asset (USDT for BTCUSDT, BTC for ETHBTC) | the latest trade (the daily bar's close without one), USD, also while the market is closed |
| `change_24h` | `priceChangePercent`: against the price 24 hours ago (rolling) | against the previous daily bar's close: today's session, or the latest one while the market is closed |
| `volume_24h` | `quoteVolume` of the last 24 hours in USD: as is for USDT pairs, otherwise at the USDT price of the quote asset (`<quote>USDT`, or the inverse of `USDT<quote>` such as USDTTRY); `null` for a quote asset without one | the daily bar's volume times its volume-weighted price (its close without one), USD; pre-market and after-hours included |

- Crypto: one `GET /api/v3/ticker/24hr?symbolStatus=TRADING` (weight 80, 0.7 MB) every 5
  minutes for every pair; only the pairs of the catalog are kept. A pair without a positive last
  price has no stats.
- Stocks: `GET /v2/stocks/snapshots?symbols=<500 symbols>&feed=delayed_sip` for every stock of the
  catalog, every 10 minutes: about 27 requests, one after another, at most a fifth of
  `ALPACA_RATE_LIMIT` a minute and never more than 20 (prod and stage 20), so chart
  requests keep the rest; each also counts against `ALPACA_RATE_LIMIT`. A request that fails
  (our whole budget spent by chart requests, an Alpaca error) is tried once more after the
  others. One Alpaca still refuses (a 4xx other than 429) is split in halves, down to single
  symbols, so a symbol it refuses costs only itself its stats (logged once per refresh); the
  stocks of a request that still fails otherwise keep their last stats, each row until 2 hours
  after it was fetched, and the others get new ones. A malformed snapshot is skipped (one
  warning per refresh). A stock without a snapshot or
  a price has no stats. A daily bar more than 5 days old (no trade on any exchange since, a
  halted or suspended stock) gives no change and a volume of 0.
- **The stock stats are 15 minutes behind**: the free plan has SIP snapshots only with that
  delay (`delayed_sip`); a refused snapshot request is logged as an error and never answered
  from another feed. The stock volume is the whole market's (SIP) of the latest session,
  pre-market and after-hours included, not a rolling 24 hours.
- Both markets refresh at the same time, so crypto never waits for the paced stock requests;
  an unexpected failure of one is logged as an error and the other still refreshes.
- Each market's stats are one Redis value (`data:v<N>:stats:<market>`, see [Cache](#cache)) kept
  until 2 hours after their refresh began; every row carries its own fetch time. A refresh runs once per environment at a time (a lock
  in Redis, renewed while the refresh runs), and when a source fails the last stats stay until
  they are 2 hours old. A process checks the stored stats at most once a minute, reading only
  their 8-byte fetch time unless they changed, and keeps its own copy, so stats Redis has
  evicted still serve until then.
  Without stats (a new environment, a long outage) every instrument has `null` stats and sorts
  last, by symbol: the list still works.
- After each stock refresh the backend logs Alpaca's `X-RateLimit-Limit` and
  `X-RateLimit-Remaining` of the last answer next to our own count of the minute.

## Mini charts

Each item of the instrument list carries `spark`, the last 10 daily candles of its market,
oldest first: `[[<open time>, <open>, <high>, <low>, <close>], null, ...]`. The days are the
market's last 10 days with candles: UTC days for crypto (open time 00:00 UTC), sessions for
stocks (open time the session open, as the `1d` candles). A day without a candle of this
instrument (no trade, not listed yet, or carried over from a refresh that could not ask for
it) is `null`; `spark` is `null` when the instrument has none of them. About 0.5 KB per item.

```json
"spark": [[1760313600, 115271.8, 115950.1, 113000.4, 115166.0], null, ...]
```

The same background task as the stats (`STATS_REFRESH`) refreshes them once a day per market,
15 minutes after its daily close (crypto at 00:15 UTC, stocks 15 minutes after the New York
close, when Alpaca serves the session's SIP bars), checking once a minute in its own loop, so
the stats never wait for it. A market without stored candles (a new environment, the key evicted from Redis) or with candles from before its
last daily close is refreshed at once, on the next check:

- Crypto: `GET /api/v3/klines?symbol=<pair>&interval=1d&startTime=&endTime=&limit=10` for
  every pair of the catalog (about 1400, weight 2 each), one after another, at most 300 weight
  a minute (a third of `BINANCE_WEIGHT_LIMIT`, `data:rl:binance-spark`): about 10 minutes,
  2800 weight once a day, each also counting against `BINANCE_WEIGHT_LIMIT`. A pair whose
  request fails is asked once more after the others; one Binance refuses (a 4xx) gets none.
  After 5 failures in a row the pairs left are not asked this time.
- Stocks: `GET /v2/stocks/bars?symbols=<500 symbols>&timeframe=1Day&feed=sip&adjustment=all`
  of the last 10 sessions (about 27 requests), within their own share of the Alpaca budget
  (`data:rl:alpaca-spark`: a twentieth of `ALPACA_RATE_LIMIT`, at most 5 a minute; prod and
  stage 5), so they never slow the stats' share, with the same retry and splitting
  as the snapshots. A malformed bar is skipped (one warning per refresh), not its whole request.
- A pair or batch that still fails keeps its last candles and is asked again every 30 minutes,
  alone, until it is answered or the next daily close; a refresh that gets nothing keeps all of
  them and is tried again 30 minutes later.
- Candles are shown until 12 hours after the market's next daily close settled: about 36 hours
  for crypto, over weekends and holidays until the next session for stocks.
- Each market's candles are one Redis value (`data:v<N>:spark:<market>`, about 4 MB for both
  markets), kept and reloaded as the stats are (at most 7 days); a process keeps its own copy
  (about 7 MB), sorted by symbol, with an index built once per stored frame, so a search looks
  up only the candles of the page it returns.

## Icons

`GET /api/v1/data/instruments/{id}/icon` always answers with an image for an instrument of the
catalog (404 `instrument-not-found` otherwise, 422 for a malformed id), so it can go straight
into an `<img>`; the browser never calls logo.dev itself.

| Instrument | Image | `Cache-Control` |
| --- | --- | --- |
| a logo at logo.dev | that logo, 128x128 PNG (or the JPEG or WebP logo.dev sends) | `public, max-age=86400` |
| no logo at logo.dev, or no `LOGO_DEV_KEY` | the fallback: Lucide's `chart-candlestick` in white on a dark grey square (the frontend rounds it like the logos), SVG | `public, max-age=3600` |
| logo.dev failing, rate limiting us or rejecting our key (then it is not asked for 10 minutes), our budget spent, Redis down, or the catalog not loadable | the fallback | `public, max-age=60` |

- Source: [logo.dev](https://www.logo.dev/docs), `GET https://img.logo.dev/ticker/<symbol>` for
  a stock and `GET https://img.logo.dev/crypto/<base asset>` for a crypto pair (all pairs of
  `BTC` share its logo), with `token=<LOGO_DEV_KEY>`, `fallback=404` (without it logo.dev
  answers 200 with its own monogram), `format=png`, `size=64` and `retina=true`.
- Only `image/png`, `image/jpeg` and `image/webp` up to 256 KB are passed on; any other answer
  of logo.dev counts as a failure (an SVG from a third party is never served from our origin).
- A logo is kept 30 days and "no logo" 7 days (see [Cache](#cache)), so the instrument list
  asks logo.dev at most once per row in that time; a failure is kept a minute and a failed
  request is not repeated before that. Without Redis, logo.dev is not asked at all: neither
  the logos nor our budget would be kept.
- Not counted against `CLIENT_RATE_LIMIT`: a page of the list asks for one icon per row. Every
  call of logo.dev counts against `LOGO_DEV_RATE_LIMIT` (see [Limits](#limits)).
- The free plan of logo.dev allows 500,000 requests a month, 100 a minute and 5 at once for
  the whole account, which all environments share: each environment sends at most 2 at once
  (an icon waits at most 10 s for one) and `LOGO_DEV_RATE_LIMIT` keeps prod and stage under 100
  a minute together. It asks for the attribution "Logos provided by Logo.dev" with a link
  wherever the logos are shown.
- Coverage on 2026-10-12: 305 of the 507 base assets of Binance's trading pairs (951 of 1375
  pairs) have a logo; stocks are covered better, e.g. `AAPL` and `BRK.B`.

## Timeframes

Both markets have the same list.

| Timeframe | Seconds | Crypto | Stock |
| --- | --- | --- | --- |
| `1m` | 60 | Binance `1m` | Alpaca `1Min`, regular session only |
| `5m` | 300 | Binance `5m` | built from 1m |
| `15m` | 900 | Binance `15m` | built from 1m |
| `30m` | 1800 | Binance `30m` | built from 1m, aligned to the session open |
| `1h` | 3600 | Binance `1h` | built from 1m, aligned to the session open |
| `4h` | 14400 | Binance `4h` | built from 1m, two candles per session |
| `1d` | 86400 | Binance `1d` | Alpaca `1Day`, see [4h and 1d](#4h-and-1d) |

## Time

- All times in responses are UTC epoch seconds (integers). Requests accept epoch seconds or
  ISO 8601 dates and date-times (`2024-06-03`, `2024-06-03T13:30:00Z`,
  `2024-06-03T09:30:00-04:00`); a date-time without an offset is UTC, a date is its 00:00
  UTC. In a URL, `+` of an offset must be written as `%2B`.
- A candle is labelled by its open time `t`.
- A request covers `[start, end)`: a candle is returned when `start <= t < end`. `start` and
  `end` do not have to be aligned to the timeframe.
- Only closed candles are returned: the candle's end (`t` + duration, for stocks at most the
  session close) must be at or before the request time. The candle still in progress never
  appears and is not a gap.
- Crypto bins are aligned to UTC epoch multiples of the timeframe (1d starts at 00:00 UTC;
  30m at 00:00, 00:30, ..., 23:30 UTC; 4h at 00:00, 04:00, ..., 20:00 UTC).

## Stock sessions and calendar

- Regular session only: 09:30-16:00 America/New_York. Early-close days end at 13:00 (for
  example 2024-11-29 and 2024-12-24). Holidays have no session. Pre-market and after-hours
  bars are dropped.
- Trading days, opens and closes come from Alpaca `GET /v2/calendar` (`date`, `open`, `close`
  in New York time), cached 7 days, converted to UTC with `zoneinfo`, so DST is handled: the
  open is 14:30 UTC in winter and 13:30 UTC in summer.
- Bins are aligned to the session open. The last bin of a session ends at the close and can be
  shorter than the timeframe.

| Timeframe | Normal day (09:30-16:00), bin opens in New York time | Candles | Early close (09:30-13:00) | Candles |
| --- | --- | --- | --- | --- |
| `1m` | 09:30, 09:31, ..., 15:59 | 390 | 09:30, ..., 12:59 | 210 |
| `5m` | 09:30, 09:35, ..., 15:55 | 78 | 09:30, ..., 12:55 | 42 |
| `15m` | 09:30, 09:45, ..., 15:45 | 26 | 09:30, ..., 12:45 | 14 |
| `30m` | 09:30, 10:00, ..., 15:30 | 13 | 09:30, ..., 12:30 | 7 |
| `1h` | 09:30, 10:30, ..., 14:30, 15:30 (30 min) | 7 | 09:30, 10:30, 11:30, 12:30 (30 min) | 4 |
| `4h` | 09:30-13:30, 13:30-16:00 | 2 | 09:30-13:00 | 1 |
| `1d` | 09:30-16:00 | 1 | 09:30-13:00 | 1 |

## 4h and 1d

| | Crypto | Stock |
| --- | --- | --- |
| `4h` | native Binance 4h, six UTC bins per day | 09:30-13:30 and 13:30-close; one candle on early-close days |
| `1d` | native Binance 1d, 00:00-24:00 UTC | Alpaca `1Day` (SIP), one candle per session, labelled with the session open in UTC |

Stock `1d` is Alpaca's `1Day` bar, because one request returns years of them. Alpaca stamps
it 00:00 New York; it is relabelled to the open of that day's session (2024-06-03 ->
`1717421400`, 09:30 EDT), so the 1d label equals the label of the day's first 1m, 1h and 4h
candle. On the SIP feed it is the day as the exchanges report it, not the aggregate of the
day's 1m candles: its open and close are the official opening and closing auction prices, and
its volume includes pre-market and after-hours trades, which the 1m candles (regular session
only) leave out. So a 1d candle can differ from the 4h candles of its session in open, close
and volume. Building 1d from the 1m bars instead would cost two or three requests per month of
history rather than one for years. On the IEX feed (before #194) the `1Day` bar equalled the
regular-session aggregate (see [Facts](#facts-verified-against-the-sources)).

## What is fetched

A request's period is split into chunks of one instrument and one base timeframe (the requested
timeframe for crypto; 1m, or `1Day` for 1d, for stocks). Each chunk is fetched once, validated
and cached (see [Cache](#cache)); the chunks are joined, resampled for stocks and sliced to
`[start, end)`. Chunk boundaries are UTC calendar days, months and years.

| Chunk | Crypto | Stock |
| --- | --- | --- |
| closed month (1m) or year (1d) | monthly archive; while it is not published yet, built from the daily archives | one request per 10000 bars (1m: a month of SIP bars with extended hours is about 20000, so 2 or 3) |
| current month or year before today | one chunk per day: daily archive, `GET /api/v3/klines` while it is not published | one chunk for all days so far |
| today up to the request (live tail) | `GET /api/v3/klines` | one request, up to 15 minutes before the request |

**Crypto** (`BINANCE_DATA_URL`, `BINANCE_API_URL`)

| Where from | URL |
| --- | --- |
| monthly archive | `data/spot/monthly/klines/<SYMBOL>/<tf>/<SYMBOL>-<tf>-<YYYY-MM>.zip` |
| daily archive | `data/spot/daily/klines/<SYMBOL>/<tf>/<SYMBOL>-<tf>-<YYYY-MM-DD>.zip` |
| REST | `GET /api/v3/klines?symbol=&interval=&startTime=&endTime=&limit=1000`, paged by 1000 |

- Every archive is verified against its `<file>.CHECKSUM` (SHA-256) before it is read; a 404 of
  the archive or its checksum means "not published yet".
- Archive CSV columns used: open time, open, high, low, close and volume (the first six of 12,
  no header row).
- Open times are microseconds in archives from 2025-01-01 on and milliseconds before (REST is
  always milliseconds); the unit is decided per value (`>= 10^15` is microseconds) and
  converted to seconds. `close_time` is only used to drop the newest REST row while it is still
  open.
- REST `startTime` and `endTime` are both inclusive: a chunk `[start, end)` sends
  `endTime = end - 1 ms`.
- A few archives hold candles that open off the timeframe's UTC grid (see
  [Facts](#binance)). REST has these periods on the grid, so the span from the first to the
  last such candle of an archive is taken from `GET /api/v3/klines` instead.
- `available_from`: `GET /api/v3/klines?interval=1m&startTime=0&limit=1`.

**Stocks** (`ALPACA_DATA_URL`, `ALPACA_API_URL`)

- `GET /v2/stocks/bars` with `feed=sip`, `adjustment=all`, `timeframe=1Min` (or `1Day` for
  1d), `limit=10000`, following `next_page_token`. Alpaca's `end` is inclusive: a chunk
  `[start, end)` sends `end - 1 s`.
- No request has an `end` within the last 15 minutes (the free plan refuses it): the chunks are
  planned as of 15 minutes before the request, so the live tail ends there and holds the
  candles complete until then, and a day becomes part of a chunk cached for a day only once it
  ended 15 minutes ago (00:15 UTC). Every bars request's `end` is also capped at 15 minutes ago.
- A `1Day` bar counts the volume of extended hours, which end at 20:00 New York (01:00 UTC at
  the latest): a chunk of 1Day bars fetched before 01:15 UTC after its last day is cached only
  until then and fetched again. The regular sessions' 1m bars are final long before.
- 1m bars outside the sessions of the calendar are dropped before anything is built (SIP has
  pre-market and after-hours bars).
- Unknown symbols are answered with 200 and no bars, so unknown instruments are recognised by
  the catalog, before any bar request.
- `available_from`: the first `1Day` bar from 2016-01-01 (`limit=1`, `end` 15 minutes ago),
  labelled with its session open.

## Gaps

- A gap is an expected candle that the source did not deliver. Expected candles: crypto,
  every aligned bin of the period; stocks, every session bin of the period.
- Gaps are never filled, interpolated or forward-filled. They are reported in `meta.gaps`,
  merged `[start, end)` ranges, earliest first, at most 100, and `meta.gaps_total`, the total
  number of missing candles. Consecutive missing candles form one range, for stocks also
  across a night or weekend; a range ends where its last missing candle ends (at most the
  session close).
- Typical causes: exchange maintenance and trading halts; minutes without any trade (illiquid
  stocks at 1m, rare at 1h and above).
- Candles with zero volume are kept as delivered.

## Validation

Requests are checked in this order; the first failure is the answer.

| Check | Error |
| --- | --- |
| client rate limit | 429 `rate-limited` |
| parameters present and well-formed: instrument id, timeframe from the list, `start` (or `limit`) and `end` as epoch seconds or ISO 8601, `limit` 1 to 5000 and not together with `start`, `start < end` | 422 `validation` |
| instrument in the catalog | 404 `instrument-not-found` |
| `start >= available_from` and `end <=` request time; with `limit`: `end > available_from` and `end <=` request time | 422 `period-out-of-range` |
| expected candle count `<= CANDLES_MAX` (with `limit`: `limit <= CANDLES_MAX`, the open candle at `end` = now does not count) | 422 `too-many-candles` |

Source data is checked before it is cached:

- sorted by time; rows with the same timestamp collapse to one (the last wins)
- open, high, low and close are positive
- `low <= min(open, close)` and `high >= max(open, close)`
- volume is not negative
- timestamps strictly increase
- crypto candles open on the timeframe's UTC grid
- Binance archives match their checksum

Data that fails is not cached, the failure is logged, and the request gets 502
`source-data-invalid`. Its detail names the source and what to do; what failed (the rule, a
parser message) is only in the log.

## Fingerprint

A fingerprint is a short code computed from a candle set: the same candles for the same request
always give the same code, and changing any single number gives a different one. It does not
depend on the cache, row order or JSON formatting.

Later, an experiment stores the fingerprint of the data it ran on. When the same request later
returns a different fingerprint, the source changed its data (for example Alpaca re-adjusted
the history after a split, or Binance corrected a candle) and the old result is no longer
reproducible on current data.

- Format: `sha256:<64 hex digits>`.
- Input: source, feed, instrument id, timeframe, `start`, `end`, then `t`, open, high, low,
  close and volume of every candle in time order. So every stock fingerprint changed with the
  switch from the IEX to the SIP feed (#194). The exact byte layout is documented on
  `fingerprint()` in `backend/src/candlestack/data/candles.py`.

## Limits

**Candles per response.** At most `CANDLES_MAX` (default 50000). The count is the number of
expected candles in `[start, end)` (session bins for stocks), computed before any source call.
The error suggests the smallest timeframe that fits, or the latest `end` that fits with the
requested timeframe.

**Rate limits.** Fixed one-minute windows (UTC minute) counted in Redis.

| Limit | Counted per | Default | When spent |
| --- | --- | --- | --- |
| Clients: `CLIENT_RATE_LIMIT` | client IP (`CF-Connecting-IP`, else the peer address; an IPv6 address counts as its /64 network); every candle and instrument-detail request, cached or not | 60 per minute, `0` disables | 429 `rate-limited` with `Retry-After` |
| Alpaca: `ALPACA_RATE_LIMIT` | environment; each Alpaca request | prod 150, stage 100, else 60 | 503 `source-unavailable` with `Retry-After` |
| Alpaca, stats refresh | environment; each snapshots request, on top of the above | a fifth of `ALPACA_RATE_LIMIT`, at most 20 | the refresh waits for the next minute |
| Alpaca, daily candles refresh | environment; each daily bars request, on top of the above | a twentieth of `ALPACA_RATE_LIMIT`, at most 5 | the refresh waits for the next minute |
| Binance REST: `BINANCE_WEIGHT_LIMIT` | environment; request weight of each REST call | 1000 | 503 `source-unavailable` with `Retry-After` |
| Binance, daily candles refresh | environment; weight of each klines request, on top of the above | a third of `BINANCE_WEIGHT_LIMIT`, at most 300 | the refresh waits for the next minute |
| logo.dev: `LOGO_DEV_RATE_LIMIT` | environment; each logo.dev request | prod 50, else 20 | the fallback icon, no waiting |
| `data.binance.vision` | not limited | | |

- When our budget at a source is spent, a request waits for the next window if it begins
  within 5 s; otherwise it gets 503 `source-unavailable` with `Retry-After`.
- Alpaca allows 200 requests per minute per key. Prod and stage have one each.
- Intraday stock candles need two or three Alpaca requests per month of 1m bars (SIP has
  extended hours), so a cold request for years of them can need more than a minute's budget.
  It then gets 503 with `Retry-After`; the months fetched so far stay cached and the retry
  continues from there.
- Binance limits request weight to 6000 per minute per IP, and all environments on the VM
  share one IP. When `X-MBX-USED-WEIGHT-1M` reaches 5000, REST calls pause until the minute
  ends. Weights: `exchangeInfo` 20, `ticker/24hr` of every pair 80, `klines` 2 (any `limit`),
  `ping` 1.
- A 429 from a source is retried with backoff when its `Retry-After` is at most 10 s; when
  retries run out the request gets 503 `source-unavailable`. After a 429 or 418 (IP ban) from
  Binance, REST calls pause for its `Retry-After` (60 s without one).
- When Redis fails, the limits are not counted (requests pass) for 5 s at a time.

## Cache

Redis, per environment, no persistence, `allkeys-lru`: anything can disappear and is fetched
again. Keys:

| Key | Value | Lifetime |
| --- | --- | --- |
| `data:v2:catalog:<market>` | instrument list of the market with its fetch time (compact JSON) | 7 days; refreshed when older than 24 h, the old list is served while one background task refreshes it |
| `data:v1:calendar` | NYSE sessions from 2016 to the end of next year | 7 days |
| `data:v2:candles:crypto:<symbol>:<base timeframe>:<chunk>`, `data:v3:candles:stock:<symbol>:...` | one chunk of validated candles, Arrow IPC + zstd | see below |
| `data:v1:first:crypto:<symbol>`, `data:v2:first:stock:<symbol>` | `available_from` | 90 days (the first candle never moves, so the list rarely asks the sources again; it still expires, so a reused symbol is asked again some day); 1 hour while the instrument has no candle |
| `data:v1:health:<source>` | reachability of the source | 60 s |
| `data:v1:stats:crypto`, `data:v2:stats:stock` | the 24 h stats of the market's instruments (symbol, price, change, volume): their fetch time (8 bytes), then Arrow IPC + zstd, as a candle chunk | until 2 hours after the fetch began; replaced every 5 (crypto) or 10 (stocks) minutes by the background refresh |
| `data:v1:spark:crypto`, `data:v2:spark:stock` | the last 10 daily candles of the market's instruments (symbol, open time, open, high, low, close), as the stats | until 7 days after the fetch began; replaced once a day after the market's daily close |
| `data:v1:icon:ticker/<symbol>`, `data:v1:icon:crypto/<base asset>` | the logo (`<media type>` + newline + image), `none` or `failed` | 30 days; `none` 7 days; `failed` 60 s |
| `data:lock:vN:<key without data:vN:>` | single-flight lock for a key being fetched, with the key's version (`data:lock:v2:catalog:crypto`) | 30 s; 2 minutes for a stats or daily candles refresh, renewed every 40 s while it runs |
| `data:fail:vN:<key without data:vN:>` | the error of a failed fetch of that key (source unavailable or invalid data) | 10 s, or the error's `Retry-After` when shorter |
| `data:rl:client:<ip>:<minute>`, `data:rl:alpaca:<minute>`, `data:rl:alpaca-stats:<minute>` (the stats' share), `data:rl:binance:<minute>`, `data:rl:alpaca-spark:<minute>`, `data:rl:binance-spark:<minute>` (the daily candles' shares), `data:rl:first-warm:<market>:<minute>` (the list's first-candle lookups), `data:rl:logodev:<minute>` | fixed-window counters; `<ip>` is an IPv4 address or an IPv6 /64 network, `<minute>` is Unix time // 60 | 61 s |

| Chunk | `<chunk>` | Crypto | Stock |
| --- | --- | --- | --- |
| closed month or year | `2024-06`, `2024` | 30 days | 1 day |
| closed day of the current month | `2026-09-25` | 7 days | |
| current month or year before today | `2026-09-01..2026-09-26` (end exclusive) | | 1 day; 1d fetched before 01:15 UTC: until then |
| today up to the request | `2026-09-26-live` | 60 s | 60 s; it ends 15 minutes before the request, so a day is today until 00:15 UTC the next day |

- Stock chunks live one day because `adjustment=all` rewrites past bars after every dividend or
  split.
- A chunk starts with its complete-until time (8 bytes) before the Arrow data. Candles that
  closed after the live chunk was fetched are neither returned nor reported as gaps until the
  chunk is fetched again.
- Single-flight: the first request for a missing key takes the lock (`SET NX`), fetches and
  stores the value; concurrent requests for the same key, in any process, wait for the value
  instead of calling the source again. When the fetch fails, its error is kept for 10 s: the
  waiting requests and new ones get the same 502 or 503 at once, without calling the source.
- The catalogs are also kept in process memory for search (about 15k instruments, a search takes
  a few ms). A process whose copy is older than 24 h first adopts a newer list from Redis.
- When Redis fails, values are fetched from the source and not cached for 5 s at a time; a
  Redis failure never fails a request by itself.
- The version (`v1`, `v2`, ...) is per key family, or per family and market, and changes when
  the meaning or format of its values changes, because Redis keeps its contents across
  deploys: values under the old version are never read again and expire. `candles` is `v2`
  since archive candles off the grid are taken from REST (see [What is fetched](#what-is-fetched));
  the chunks cached before that hold them as delivered. `catalog` is `v2` since crypto pairs
  have the exchange `BINANCE`; the lists cached before that have none. The stocks' `candles`
  (`v3`), `first`, `stats` and `spark` (`v2`) changed with the SIP feed (#194): IEX values are
  never served; the crypto keys kept their versions. Locks and kept errors carry the version
  too, so during a deploy a process on the old version and one on the new never wait for each
  other's value.
- `GET /api/health/sources` reports `ok` or `error` per source (Binance `GET /api/v3/ping`,
  Alpaca `GET /v2/clock`) from the 60 s health keys.

## Candles endpoint

`GET /api/v1/data/instruments/{instrument_id}/candles?timeframe=<tf>&start=<time>&end=<time>`

`GET /api/v1/data/instruments/{instrument_id}/candles?timeframe=<tf>&end=<time>&limit=<n>`

The instrument id is part of the path; `timeframe`, `start`, `end` and `limit` are query parameters.
The former `/api/v1/data/candles?instrument=<id>` endpoint has been removed.

| Parameter | Rule |
| --- | --- |
| `instrument_id` (path) | required, an instrument id |
| `timeframe` | required, one of `1m 5m 15m 30m 1h 4h 1d` |
| `start` | inclusive; epoch seconds or ISO 8601 (see [Time](#time)). Without it, `limit` picks the start |
| `end` | optional, exclusive, same formats; default the request time |
| `limit` | optional, 1 to 5000, default 500 (`CANDLES_MAX` when that is lower): without `start`, the number of candles to return. With `start` it is a 422 `validation`; above 5000 it is a 422 `validation`, and above `CANDLES_MAX` (default 50000, so only when it is set below 5000) a 422 `too-many-candles` |

Example: `/api/v1/data/instruments/stock:AAPL/candles?timeframe=1h&start=2024-06-03&end=2024-06-04`
returns the 7 candles of that session.

### Last N candles

Without `start`, the response holds the last `limit` candles that open before `end` (`end`
defaults to the request time, so only closed candles count). The server picks the start and
returns it as `meta.start`, which is the open time of the earliest candle it counted. Candles
are counted by the market schedule, as the candle count is: stocks only in regular sessions, so
nights, weekends and holidays are skipped, crypto around the clock. Missing candles inside the
period stay in `meta.gaps` and count towards `limit`. When the data starts later, fewer than
`limit` candles come back and `meta.start` is the first candle there is. With `end` at or before
the first candle the answer is 422 `period-out-of-range` ("Set end after ..."); an instrument
without any candle yet gives an empty result with `meta.start` equal to `meta.end`. `start` together with
`limit` is a 422 `validation` (error `type` `limit_with_start`, `loc` `["query", "limit"]`).

```text
/api/v1/data/instruments/crypto:BTCUSDT/candles?timeframe=15m
/api/v1/data/instruments/stock:AAPL/candles?timeframe=1h&end=2024-06-03T13:30Z&limit=500
```

The first returns the last 500 closed 15m candles of BTCUSDT. The second returns the 500
session candles before the open on Monday 2024-06-03; the ones before it are Friday's, as
the weekend has no sessions. A `limit` above `CANDLES_MAX` is refused with 422
`too-many-candles` ("Use a limit of N or less.").

Processing: rate limit, validation, catalog lookup, period and count checks, then each chunk
of the period from the cache or the source (single-flight; up to 16 at once for crypto, 4 for
stocks), resampled (stocks) a year of chunks at a time so that years of 1m bars are never held
at once, concatenated, closed candles only, sliced to `[start, end)`, gaps found, fingerprint
computed.

Response (columnar arrays; index `i` of every array is one candle):

```json
{
  "meta": {
    "instrument": "crypto:BTCUSDT",
    "timeframe": "1h",
    "start": 1717200000,
    "end": 1717214400,
    "source": "binance",
    "feed": "spot",
    "fingerprint": "sha256:3b1f0c9e...",
    "count": 3,
    "gaps": [[1717207200, 1717210800]],
    "gaps_total": 1
  },
  "t": [1717200000, 1717203600, 1717210800],
  "o": [67491.0, 67612.5, 67580.1],
  "h": [67700.0, 67650.0, 67640.0],
  "l": [67420.2, 67510.0, 67488.8],
  "c": [67612.4, 67540.3, 67601.9],
  "v": [512.31, 398.07, 421.55]
}
```

| Field | Meaning |
| --- | --- |
| `meta.instrument`, `meta.timeframe`, `meta.start`, `meta.end` | the request, normalised |
| `meta.source`, `meta.feed` | `binance`/`spot` or `alpaca`/`sip` |
| `meta.fingerprint` | see [Fingerprint](#fingerprint) |
| `meta.count` | number of candles returned |
| `meta.gaps` | up to 100 `[start, end)` ranges of missing candles, earliest first |
| `meta.gaps_total` | number of missing candles in total |
| `t` | open times, epoch seconds, strictly increasing |
| `o`, `h`, `l`, `c`, `v` | open, high, low, close, volume as numbers |

## Sources health

`GET /api/health/sources` (not versioned, like `/api/health`) reports whether each source
answers its cheapest request (Binance `GET /api/v3/ping`, Alpaca `GET /v2/clock`), checked at
most once a minute: 200 when both answer, 503 with the same body when one does not.

```json
{
  "status": "ok",
  "sources": {
    "binance": {"status": "ok", "latency_ms": 84, "detail": null},
    "alpaca": {"status": "ok", "latency_ms": 131, "detail": null}
  }
}
```

## Errors

RFC 9457 `application/problem+json`, `type` `https://candlestack.tech/problems/<slug>`. The
`detail` says which limit fired and what to change.

| Status | Slug | When | Extensions |
| --- | --- | --- | --- |
| 404 | `instrument-not-found` | the id is not in the catalog | `instrument` |
| 422 | `validation` | missing or malformed parameters | `errors`: one `{loc, msg, type}` per bad parameter |
| 422 | `period-out-of-range` | `start` before `available_from`, or `end` in the future | `instrument`, `timeframe`, `available_from`, `available_to` |
| 422 | `too-many-candles` | more than `CANDLES_MAX` expected candles | `requested`, `maximum`, `suggestion` (also in `detail`) |
| 422 | `warm-up-out-of-range` | a backtest's period starts too early for the candles its model needs before it (`POST /api/v1/backtests`) | `instrument`, `timeframe`, `candles`, `earliest_start` (null when the first candle is not known) |
| 429 | `rate-limited` | client over `CLIENT_RATE_LIMIT` | `limit`; header `Retry-After` |
| 502 | `source-data-invalid` | source data failed validation | `source` |
| 503 | `source-unavailable` | source down or timing out, or our budget for it is spent | `source`; header `Retry-After` when waiting helps (our budget is spent, or the source limits us) |

Invalid parameters, all of them at once:

```json
{
  "type": "https://candlestack.tech/problems/validation",
  "title": "Invalid request",
  "status": 422,
  "detail": "query parameter 'timeframe': Input should be '1m', '5m', '15m', '30m', '1h', '4h' or '1d'; query parameter 'start': 'yesterday' is neither epoch seconds nor an ISO 8601 date or date-time",
  "errors": [
    {"loc": ["query", "timeframe"], "msg": "Input should be '1m', '5m', '15m', '30m', '1h', '4h' or '1d'", "type": "enum"},
    {"loc": ["query", "start"], "msg": "'yesterday' is neither epoch seconds nor an ISO 8601 date or date-time", "type": "time"}
  ]
}
```

`start` before the first candle (`available_to` is the request time):

```json
{
  "type": "https://candlestack.tech/problems/period-out-of-range",
  "title": "Period out of range",
  "status": 422,
  "detail": "crypto:BTCUSDT has 1h candles from 2017-08-17T04:00:00Z (1502942400). Set start to 1502942400 or later.",
  "instrument": "crypto:BTCUSDT",
  "timeframe": "1h",
  "available_from": 1502942400,
  "available_to": 1790424000
}
```

Too many candles (1m for 2024-01-01 to 2024-04-01):

```json
{
  "type": "https://candlestack.tech/problems/too-many-candles",
  "title": "Too many candles",
  "status": 422,
  "detail": "Requested 131040 candles, the maximum is 50000. Use 5m or a larger timeframe, or end the period at 2024-02-04T17:20:00Z (1707067200) or earlier.",
  "requested": 131040,
  "maximum": 50000,
  "suggestion": "Use 5m or a larger timeframe, or end the period at 2024-02-04T17:20:00Z (1707067200) or earlier."
}
```

Client rate limit:

```http
HTTP/1.1 429 Too Many Requests
Content-Type: application/problem+json
Retry-After: 17

{
  "type": "https://candlestack.tech/problems/rate-limited",
  "title": "Too many requests",
  "status": 429,
  "detail": "More than 60 candle and instrument requests in one minute from this address. Retry in 17 seconds.",
  "limit": 60
}
```

Source unavailable:

```json
{
  "type": "https://candlestack.tech/problems/source-unavailable",
  "title": "Data source unavailable",
  "status": 503,
  "detail": "Alpaca did not answer in time. Try again in a minute.",
  "source": "alpaca"
}
```

## Facts verified against the sources

Measured on 2026-09-25 (a Friday evening, US market closed): Binance from a developer machine,
Alpaca from a GitHub Actions job with the stage paper keys, on the IEX feed. What is marked
"docs" comes from Alpaca's documentation of 2026-10-14 and was not measured with our keys. The
recorded responses the tests use are in `backend/tests/fixtures/` (see its README).

### Binance

- **Catalog.** `GET /api/v3/exchangeInfo` lists 3713 symbols: 1368 `TRADING`, 2345 `BREAK`.
  Every `TRADING` symbol allows spot trading, so `status` alone selects the catalog.
  `symbolStatus=TRADING&showPermissionSets=false` returns just those in 2.48 MB (53.5 KB
  gzipped) instead of 17.6 MB, at the same weight 20. Quote assets: USDT 496, TRY 310,
  USDC 258, ... Six trading symbols are not ASCII (`币安人生USDT`, `牛来USDT`, ...); REST and
  archives accept them URL-encoded, and responses must be decoded as UTF-8.
- **Archives.** One CSV per zip, no header row, 12 columns, prices and volumes as decimal
  strings. Open times switch from milliseconds to microseconds exactly at 2025-01-01 (monthly
  and daily files). `close_time` is open time + interval - 1 unit. `.CHECKSUM` is
  `<sha256 hex>  <file name>` without a trailing newline; all downloaded archives matched.
  Monthly and daily files hold identical rows, and 1m summed into 1h (2024-03-10) and 1h into
  1d (2024-01) reproduce the archives exactly (OHLCV, trades, taker volumes).
- **Minutes without trades.** A 1m candle of a minute without trades has volume 0 and open,
  high, low and close equal to the previous close, while the native 5m, 15m and 1h candles are
  built from trades only. So 1m aggregated into 1h is sure to equal the native 1h only where
  every minute traded: an hour that starts with such minutes gets the previous close as its
  open, which can also widen its high or low. Aggregating while skipping the zero-volume
  minutes reproduces the native candles exactly (BTCUSDT 2017-08-17 04:00-24:00, where 369 of
  1200 minutes had no trade: 20 of 20 1h and 80 of 80 15m candles; 15 and 62 without
  skipping). Crypto candles are served in Binance's native timeframes, so this does not affect
  responses.
- **Archives off the grid.** Scans of the monthly archives (BTCUSDT 1h and 15m and ETHUSDT 1h
  from 2017-08 to 2026-08, BTCUSDT 1m of 2017-12 and 2018-02) found candles whose open time is
  no multiple of the timeframe in two periods: BTCUSDT 1m from 2017-12-04 06:00 to 12-18 10:00
  at `hh:mm:20` (20401 rows), and the two days after the maintenance of 2018-02-08 (from
  2018-02-09T09:28:14Z, 1m to 1h of BTCUSDT, ETHUSDT and BNBUSDT; 1h at `hh:28:14`). 4h and 1d
  are on the grid. REST returns both periods on the grid, with the maintenance as missing
  candles (1h from 2018-02-09T10:00).
- **Residual after the maintenance.** The BTCUSDT 1h archive row of 2018-02-11T04:00, the first
  on the grid after that span and therefore served from the archive, has open 7976.74 and
  volume 1055.87. The 1m candles of that hour (REST, on the grid) give open 7976.73 and volume
  1063.59, as does REST's 1h candle; the native 4h and 1d candles agree with the 1m ones.
- **Publication lag.** A daily archive appears 01:28-03:26 UTC the next day. A monthly archive
  appears 1-7 days after the month, on the following Monday. Until then the days of a closed
  month come from daily archives, and today (and yesterday before its archive) from REST.
- **REST.** `klines` costs weight 2 whatever the `limit`; `limit` above 1000 is cut to 1000;
  `startTime` and `endTime` are both inclusive; the newest row is the candle still in
  progress. The per-IP limit is 6000 weight per minute (`rateLimits`), reported per response in
  `X-MBX-USED-WEIGHT-1M`. Errors are JSON (`{"code":-1121,"msg":"Invalid symbol."}`), and a
  `BREAK` symbol still returns klines.
- **History.** BTCUSDT from 2017-08-17 (first 1m candle 04:00 UTC, first 1d candle 00:00 UTC);
  a pair listed 2026-09-24 has 1d from 00:00 but 1m only from its first trade at 11:00.

### Alpaca

- **Catalog.** `GET /v2/assets?status=active&asset_class=us_equity`: 14,387 assets (ETFs
  included, no ETF flag), 6.49 MB. Tradable and not OTC: 13,199 (NASDAQ 5600, NYSE 2924,
  ARCA 2739, BATS 1646, AMEX 290). Symbols are `A-Z` and `.` (525 with a dot, e.g. `BRK.B`),
  which work unencoded in every endpoint. Stored minimally, both catalogs are about 1.25 MB.
- **Calendar.** Only trading days, `open`/`close` as New York `HH:MM`; early closes 13:00
  (2024-07-03, 2024-11-29, 2024-12-24), special closures absent. 2016 to 2026 is one request
  (2765 days, 357 KB); the calendar is published about three years ahead.
- **Bars.** `t` is the bar open in RFC 3339 UTC; `v` and `n` are integers. `limit` is at most
  10000 and counts across symbols; pages follow `next_page_token`. `end` is inclusive. A month of
  AAPL IEX 1m bars is 7235 bars, one request. 1Hour bars are aligned to the clock, not the
  session open (the 09:00 bar holds 09:30-09:59), so intraday candles are built from 1m bars.
- **IEX 1Day = regular session.** `1Day` is stamped 00:00 New York. Its open, high, low and
  close equal the aggregate of the regular-session 1m bars on 20 of 20 checked days, including
  four days with extended-hours prints and the early close 2024-11-29 (the all-hours aggregate
  matched only 16). Its volume is 0.01-0.85% higher. SIP `1Day` differs: official auction
  prices and extended-hours volume.
- **Coverage.** IEX starts 2020-07-27 (AAPL, MSFT and UUU; SPY has one stray 1-share 1Day
  bar in 2018). IEX volume is about 0.8% of the market, and 1m bars are sparse: on 2024-06-03
  AAPL has 320 of 390 regular minutes, SPY 330, PLBY 4, UUU 1. IEX carries sparse prints
  before 09:30 and after 16:00, so bars are filtered to the calendar's sessions. SIP starts
  2016-01-04 and answers with the paper keys.
- **SIP on the free plan (docs).** The Basic plan, the default of paper accounts, includes SIP
  history since 2016 for any query whose `end` is at least 15 minutes old; a newer one is
  refused with `{"code":42210000,"message":"subscription does not permit querying recent SIP
  data"}` (a 4xx). The latest and snapshot endpoints with `feed=sip` need a paid plan;
  `GET /v2/stocks/snapshots` takes `feed=delayed_sip`, SIP delayed by 15 minutes. The limit
  stays 200 requests per minute. SIP `1Day` bars have the official auction open and close and
  count extended-hours volume; SIP 1m bars cover pre-market and after-hours.
- **Adjustment.** `adjustment=all` divides prices before a split (NVDA 10:1 on 2024-06-10) and
  multiplies volumes, and applies dividend factors to all earlier bars: adjusted history
  changes after every later corporate action.
- **Errors.** An unknown symbol is 200 with no bars (`{"bars":{}}`); `/v2/assets/<symbol>` is
  404. 400 errors are `{"message":...}` (bad timeframe, `limit` above 10000, end before
  start). Missing or wrong keys give **401 as `text/html`** (an nginx page), without rate-limit
  headers.
- **Rate limit.** Every authenticated response carries `X-Ratelimit-Limit: 200`,
  `X-Ratelimit-Remaining` and `X-Ratelimit-Reset`; `Remaining` is not monotonic across
  calls, so our own counter is the budget. Latency is 145-525 ms per bars request, 1.2 s for
  the assets.

### Decisions from the facts

| Question | Decision |
| --- | --- |
| Stock feed | `sip` since #194: the whole market from 2016, which the free plan of paper accounts includes up to 15 minutes ago. It was `iex` before, on the wrong belief that paper accounts get IEX only (history from 2020-07-27, about 1% of the volume). |
| Stock delay | No request reaches into the last 15 minutes; the live tail ends there, and a chunk with data that was not final yet is cached only until it is. The stats come from `delayed_sip` snapshots, never from another feed. |
| Stock 1d | Alpaca `1Day`, relabelled to the session open (see [4h and 1d](#4h-and-1d)): on SIP official open and close and extended-hours volume, so not the aggregate of the 1m candles. Intraday timeframes from regular-session 1m bars. |
| Early closes | One 4h candle 09:30-13:00 and no second one; the last 1h candle is 12:30-13:00. |
| Binance timestamps | Unit per value (`>= 10^15` is microseconds), label = open time, `close_time` ignored except for the still-open REST row. |
| Binance current period | Closed month: monthly archive, else daily archives. Current month: daily archive per complete day, REST on 404; REST for today. |
| End of range | Both sources treat `end` as inclusive; the adapters send end minus one unit. |
| Stock cache | Closed stock chunks live 1 day (history is rewritten after corporate actions; 30 days for crypto). Stocks are fetched in whole months (1m) or years (1Day) because one request returns them and Alpaca requests are budgeted; per-day chunks would multiply the requests. |
| Unknown instruments | Recognised by the catalog: Alpaca answers 200 with no bars. Error bodies are never assumed to be JSON. |
| Gaps | Minutes without trades are gaps like any other, reported as capped ranges and a total. |

Not verified yet: whether Alpaca returns a partial bar for the session in progress (the market
was closed); candles are only returned once their end has passed, so it would not show. Not
measured with our keys yet (#194): the SIP bars of the last day and the `delayed_sip`
snapshots; until then the facts on SIP are Alpaca's documentation.
