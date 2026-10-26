# Test fixtures

Real source responses, recorded once and committed so the tests need no network and no keys.
The Alpaca bars were recorded on the IEX feed, which CandleStack used before #194; SIP answers in
the same format, so they stand for SIP bars where the values do not matter. The SIP-specific ones
are written by hand (below) until they can be recorded with the keys.

| File | What | Recorded |
| --- | --- | --- |
| `alpaca/calendar-2024.json` | Alpaca `GET /v2/calendar?start=2024-01-01&end=2024-12-31`: 252 trading days, early closes 2024-07-03, 11-29 and 12-24 | 2026-09-25 with the stage paper keys, response body unchanged |
| `binance/archive/monthly/BTCUSDT-1h-2024-01.zip` and `.CHECKSUM` | monthly kline archive of data.binance.vision, 744 rows, open times in milliseconds | 2026-09-25 from a developer machine |
| `binance/archive/monthly/BTCUSDT-1h-2025-01.zip` and `.CHECKSUM` | the same for January 2025, open times in microseconds | 2026-09-25 from a developer machine |
| `binance/archive/daily/BTCUSDT-1h-2024-03-10.zip` and `.CHECKSUM` | daily kline archive, 24 rows | 2026-09-25 from a developer machine |
| `binance/rest/exchangeInfo-trading.json` | `GET /api/v3/exchangeInfo?symbolStatus=TRADING&showPermissionSets=false`, symbols cut to BTCUSDT, ETHUSDT, SOLUSDT, ETHBTC and 币安人生USDT | 2026-09-25 from a developer machine, trimmed as described |
| `binance/rest/klines-BTCUSDT-1h-limit5.json` | `GET /api/v3/klines?symbol=BTCUSDT&interval=1h&limit=5` at 2026-09-25T22:42:42Z (1790376162); the last row is the candle still open | 2026-09-25 from a developer machine |
| `alpaca/assets.json` | `GET /v2/assets?status=active&asset_class=us_equity`, cut to AAPL, SPY (ARCA ETF), BRK.B, HEINY (OTC) and MLGO (not tradable) | 2026-09-25 with the stage paper keys, trimmed as described |
| `alpaca/bars-AAPL-1Min-2024-06-03-p0.json`, `-p1.json` | `GET /v2/stocks/bars` AAPL 1Min of 2024-06-03 with `limit=300`: 300 bars and a `next_page_token`, then the last 20 | 2026-09-25 with the stage paper keys |
| `alpaca/bars-AAPL-1Min-2024-11-29.json` | 1Min of the early close 2024-11-29: 209 bars, 09:30-12:59 EST | 2026-09-25 with the stage paper keys |
| `alpaca/bars-AAPL-1Min-2024-03-08-open.json` | 1Min 14:00-15:31 UTC of 2024-03-08: the EST open at 14:30 and a 14:29 print before it | 2026-09-25 with the stage paper keys |
| `alpaca/bars-AAPL-1Day-2024-06.json` | 1Day of June 2024, 19 bars stamped 04:00 UTC (00:00 EDT) | 2026-09-25 with the stage paper keys |
| `alpaca/bars-AAPL-1Day-2024-11-25_12-02.json` | 1Day 2024-11-25 to 12-02 around the early close, stamped 05:00 UTC (00:00 EST) | 2026-09-25 with the stage paper keys |
| `alpaca/bars-AAPL-1Day-earliest.json` | the first AAPL SIP 1Day bar (`limit=1`): 2016-01-04, prices adjusted for the 2020 split, extended-hours volume | written by hand on 2026-10-14 (#194), values approximate: no Alpaca keys were at hand |
| `alpaca/bars-AAPL-1Min-2016-01-04.json` | SIP 1Min bars of AAPL on 2016-01-04: two pre-market bars, five of the regular session (09:30-09:32, 10:30, 15:59 EST) and two after-hours bars | written by hand on 2026-10-14 (#194), not recorded |
| `alpaca/calendar-2016-01.json` | the 19 trading days of January 2016 in the format of `GET /v2/calendar` (New Year's Day and Martin Luther King Jr. Day closed), so that the first SIP bar has its session | written by hand on 2026-10-14 (#194) from the NYSE holidays |
| `alpaca/clock.json` | `GET /v2/clock` while the market was closed | 2026-09-25 with the stage paper keys |
| `alpaca/error-no-auth.html` | the 401 of a request without keys: an nginx `text/html` page (line endings normalised to LF) | 2026-09-25 with the stage paper keys |
| `alpaca/error-unknown-symbol-multi.json` | `GET /v2/stocks/bars?symbols=NOTAREALSYM`: 200 with no bars | 2026-09-25 with the stage paper keys |
| `binance/archive/monthly/BTCUSDT-1d-2024-01.zip` and `.CHECKSUM` | monthly 1d kline archive, 31 rows, open times in milliseconds | 2026-09-25 from a developer machine |
| `binance/rest/klines-BTCUSDT-1d-first.json` | `GET /api/v3/klines?symbol=BTCUSDT&interval=1d&startTime=0&limit=1`: the first BTCUSDT candle, 2017-08-17 | 2026-09-25 from a developer machine |
| `binance/rest/klines-BTCUSDT-1m-first.json` | `GET /api/v3/klines?symbol=BTCUSDT&interval=1m&startTime=0&limit=1`: the first BTCUSDT 1m candle, 2017-08-17T04:00Z (the first-candle lookup) | 2026-09-26 from a developer machine |
| `binance/archive/monthly/BTCUSDT-1h-2018-02.zip` and `.CHECKSUM` | monthly archive of the maintenance of 2018-02-08: 640 rows, of which 43 open at hh:28:14 (2018-02-09T09:28:14 to 02-11T03:28:14), off the 1h grid | 2026-09-26 from a developer machine |
| `binance/rest/klines-BTCUSDT-1h-2018-02-09-maintenance.json` | `GET /api/v3/klines?symbol=BTCUSDT&interval=1h&startTime=1518166800000&endTime=1518321599999&limit=1000`: the same span on the grid, 42 rows from 2018-02-09T10:00 | 2026-09-26 from a developer machine |
| `binance/rest/ticker-24hr-trading.json` | `GET /api/v3/ticker/24hr?symbolStatus=TRADING`, cut to the pairs of `exchangeInfo-trading.json` (BTCUSDT, ETHUSDT, SOLUSDT, ETHBTC and 币安人生USDT) | 2026-10-14 from a developer machine, trimmed as described |
| `alpaca/snapshots-sample.json` | `GET /v2/stocks/snapshots?symbols=...&feed=delayed_sip` in the format of Alpaca's documentation: AAPL, SPY (no `vw`), BRK.B (a daily bar six weeks old, as of a halted stock), UUU (no trade, no previous bar) and AAC.U (`null`), as of the evening of 2026-10-13, with volumes of the whole market | written by hand, not recorded: no Alpaca keys were at hand; to be replaced by a recorded answer |

`tests/e2e/mock_sources.py` fakes Binance and Alpaca for the e2e tests from these files (it finds
them by name): the archives under their data.binance.vision paths, klines cut from the archives
(`startTime=0`, the first-candle lookup, gets `klines-<SYMBOL>-<interval>-first.json` of the
requested interval), and Alpaca bars merged from every `bars-<SYMBOL>-<timeframe>-*.json`.

`keras/*.keras` are small real Keras 3.15 models for the upload check (`candlestack.ml`), built by
`keras/make_models.py` in a throwaway container (its docstring says how and what each file is).
`keras/raw_candles.keras` (25 x 5 in, one score out) goes with `keras/raw_candles.manifest.json`,
the manifest without preprocessing of [docs/preprocessing.md](../../../docs/preprocessing.md),
for the manifest checks (`candlestack.preprocessing`);
`keras/make_allowlist.py` writes `candlestack/ml/allowlist.py` from Keras the same way.

`onnx/raw_candles.onnx` is the same kind of model in ONNX (25 x 5 in, one score out, 128
weights), which takes the same manifest, for the ONNX upload check and the worker's onnxruntime
runner. `onnx/make_models.py` builds it with onnx from `backend/`
(`uv run python tests/fixtures/onnx/make_models.py`); the hostile ONNX files are built in the
tests themselves.
