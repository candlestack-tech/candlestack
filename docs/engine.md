# Backtest engine contract

The backtest answers one question: had we traded on a model's predictions, what would have
happened? It turns candles and predictions into trades, an equity curve and statistics. This
document is the contract for the code behind it: the `signals`, `engine` and `metrics` modules
and the `candlestack-bt` command in `cli`, as the code does it now. Architecture and module
rules: [architecture.md](architecture.md). Candles: [data.md](data.md).

## Where the engine sits

```mermaid
flowchart LR
    files["candles + predictions"] --> signals["signals<br/>relabel, align, zscore, rank, ema,<br/>threshold, hysteresis, kmeans,<br/>long_only, confirm, min_hold"]
    signals -->|"ts, open, high, low, close, signal"| engine["engine<br/>backtest"]
    engine -->|"trades, series"| metrics["metrics<br/>stats, drawdown"]
```

| Module | Public names | Job |
| --- | --- | --- |
| `signals` | `relabel`, `align`, `AlignReport`, `zscore`, `rank`, `ema`, `threshold`, `hysteresis`, `kmeans`, `long_only`, `confirm`, `min_hold`; `PostProcessing`, `make_signal` (`prepare`, `decide`), `describe` | put predictions next to their candles and turn them into a signal of -1, 0 or +1; apply the post-processing in its order and record it |
| `engine` | `backtest`, `Settings`, `Result`, `InvalidInput`, `ENGINE_VERSION` | the loop: candles and a signal in, trades and an equity series out |
| `metrics` | `stats`, `drawdown` | the statistics of one run and its drawdown curve |
| `cli` | `main` (the `candlestack-bt` command) | read files, call the three modules above in order, write the results |

- The three backtest modules are plain functions on Polars frames. They import no other module
  of the project, not each other and no server library; nothing imports `cli`.
  `uv run lint-imports` checks this (contracts in `backend/pyproject.toml`).
- They meet only through the column contracts below. In the code, the command and
  `experiments` (`assembly.py`) call them one after the other.

What is outside the engine on purpose:

| Concern | Where | Why |
| --- | --- | --- |
| how a prediction becomes long, short or flat (calibration, smoothing, thresholds, hysteresis, k-means, long only, confirmation, holding period) | `signals` | a new idea is a new signal function; the loop and its results stay the same |
| time labels and missing candles or predictions | `signals` (`relabel`, `align`) | the engine takes the rows as given; it only checks that `ts` increases |
| statistics, drawdown curve, annualization | `metrics` | a new statistic needs no new engine version |
| files, hashes of the inputs, the summary | `cli` | the engine reads and writes no files |
| what only one data set needs (the supervisor's close labels, his trades without the last open one) | his test, `tests/unit/engine/test_supervisor.py` | the engine stays general |

The loop is the part every result depends on, so it stays small, it is versioned
(`ENGINE_VERSION`) and it is the tech lead's area: a change to `candlestack/engine/`, its tests
or this document needs the tech lead's approval (`.github/CODEOWNERS`). Extend the backtest
through `signals` or `metrics`, not by changing the loop.

Not modelled in this version:

- position sizing: one position at a time, always the whole capital; a fractional signal is
  refused ("position sizing is not supported yet");
- leverage, margin and liquidation: the limits are ruin (rule 8: once the whole capital is
  lost it is 0 and the run stays flat) and the drawdown stop (rule 9, off by default);
- volume and liquidity: every order fills in full at its price; `volume` is ignored;
- borrowing costs, financing, dividends and several instruments in one run.

## Input

`backtest(frame, settings)` takes one Polars frame, one row per candle.

| Column | Type | Rule |
| --- | --- | --- |
| `ts` | any integer, cast to Int64 | UTC epoch seconds of the candle's open (as in [data.md](data.md#time)); none missing; each row after the previous one |
| `open`, `high`, `low`, `close` | any number, cast to Float64 | finite and above 0 |
| `signal` | any number, cast to Float64 | -1 (short), 0 (flat) or +1 (long); none missing |

- Other columns (`volume`, `prediction`, ...) are ignored.
- The engine does not sort and does not compare high and low with open and close; candles from
  the `data` module are checked for that already ([data.md](data.md#validation)).
- An empty frame gives empty trades and series with the schemas below.
- A frame that breaks a rule raises `InvalidInput` (a `ValueError`) before anything runs. The
  message names every broken rule, how many rows break it and the first one, for example:

  ```text
  Invalid backtest input: 1 row has a ts not after the previous row, first at row 2; 1 row has a fractional signal (position sizing is not supported yet), first at row 1.
  ```

## Settings

`Settings` is a frozen Pydantic model, so a stored run keeps the settings it ran with. An
unknown field or a value out of range raises a validation error (a `ValueError`).

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `fill` | `"next_open"` or `"signal_close"` | `"next_open"` | when a decision fills: at the open of the next candle, what a live run can do, or at the close of the candle that made it, the convention of the supervisor's results (optimistic: the decision and its fill see the same close) |
| `stop_loss` | number above 0 and below 1, or `None` | `None` | the loss that closes a position, as a fraction of its entry price: 0.05 is 5%; `None` for no stop |
| `drawdown_stop` | number above 0 and below 1, or `None` | `None` | the fall of the equity from its highest level so far that halts the run (rule 9), as a fraction of that level: 0.2 is 20%; `None` for no drawdown stop |
| `fee_bps` | finite number, 0 or more | `0.0` | the fee of each leg, in basis points of its notional |
| `slippage_bps` | finite number, 0 or more | `0.0` | how far each fill lands from its price, against the trade, in basis points |

A basis point (bps) is 0.01%: 10 bps is a factor of 0.001.

## Output

`backtest` returns a `Result`, a frozen dataclass with `trades`, `series` and `engine_version`.
All numbers are float64 as computed, never rounded.

`trades`: one row per closed trade, in time order (there is at most one position at a time).

| Column | Type | Meaning |
| --- | --- | --- |
| `signal_ts` | Int64 | `ts` of the candle whose signal change made the decision |
| `open_ts` | Int64 | `ts` of the candle the entry filled on |
| `close_ts` | Int64 | `ts` of the candle the exit filled on |
| `side` | Int8 | +1 long, -1 short |
| `open_price` | Float64 | entry fill price, slippage included |
| `close_price` | Float64 | exit fill price, slippage included |
| `stop_price` | Float64 | the stop level; null without a stop |
| `exit_reason` | String | `signal`, `stop`, `gap`, `end`, `ruin` or `drawdown` (see the rules) |
| `return` | Float64 | capital after the trade / capital before it - 1, costs included; below -1 only when the trade lost more than the capital, which then becomes 0 (rule 8) |
| `fees` | Float64 | the fees of both legs, as a fraction of the capital before the trade |
| `slippage_cost` | Float64 | what slippage cost the trade, as a fraction of the capital before it: the return at the prices before slippage, with the same fees, minus `return`; `fees + slippage_cost` is the full cost |

`series`: one row per input candle.

| Column | Type | Meaning |
| --- | --- | --- |
| `ts` | Int64 | the candle's `ts` |
| `signal` | Float64 | the input signal |
| `position` | Float64 | -1, 0 or +1: the side held after the candle's fills |
| `equity` | Float64 | the capital after the candle's fills, an open position marked at the candle's close; the run starts with 1.0 |

`engine_version` is `ENGINE_VERSION`, now `"0.3.0"` (see [ENGINE_VERSION](#engine_version)).

## Rules

These are the rules of `candlestack/engine/loop.py`, numbered as in its docstring. On each
candle the loop does five things, in this order:

1. `next_open` only: the order decided on the previous candle fills at this candle's open.
2. The stop of the position held now is checked.
3. If the signal differs from the previous candle's, the decision is acted on: `signal_close`
   fills it at this candle's close, `next_open` leaves an order for the next open.
4. A position worth 0 or less at this candle's close is closed there (ruin, rule 8); the
   candle's `position` and `equity` are recorded.
5. With a drawdown stop, the candle's `equity` is compared with the highest so far: at or
   below `peak * (1 - drawdown_stop)` the run halts, a position still open closed at this
   candle's close (rule 9).

After the last candle, a position still open is closed at the last close. After a ruin or a
halt the loop does nothing more: every later candle is flat, its equity 0 after a ruin and the
capital after a halt.

The rules, the same for both fills:

1. **Only a change of the signal is a decision.** The first row is a warm-up: it sets the
   starting signal and can never be a change, so a signal of +1 from the first row opens
   nothing until it changes.
2. **Two fills.** `signal_close` fills a decision at the close of its candle; `next_open` at
   the open of the next candle. On that next candle the order fills first, at the open, and
   then the stop of the position now held is checked. A position the order closes at the open
   cannot gap out on that candle: it leaves with reason `signal`.
3. **A flip is two legs.** From +1 to -1 (or back) the position is closed and the new one
   opened at the same fill price, as two trades, each leg with its fee and slippage.
4. **Stop loss.**
   - The level comes from the actual entry price, slippage included:
     `entry * (1 - side * stop_loss)`, below the entry for a long, above it for a short.
   - On a candle the position already held at its open: an open at or beyond the level exits
     at that open (`gap`); otherwise a low (long) or high (short) at or beyond the level exits
     at the level (`stop`). Touching the level counts.
   - On its entry candle a `next_open` position can only touch the level (its open is the
     entry); a `signal_close` position is first checked on the next candle.
   - The stop comes before the signal of the same candle: on a candle that reaches the stop
     and changes the signal, the stop closes the position first, and the change then acts on
     the flat position, so a change to the other side opens it.
   - After a stop, only the next change of the signal opens a position again; a signal that
     stays at +1 after its long was stopped out does not buy back.
5. **Costs.** Slippage moves each fill price against the trade; the fee is a commission on each
   leg (see [Fees and slippage](#fees-and-slippage)). A trade's return is capital after /
   capital before - 1: `side * (exit / entry - 1) - fee * (1 + exit / entry)`. Its
   `slippage_cost` is the return at the prices before slippage minus that, so
   `fees + slippage_cost` is the return without costs, `side * (price_exit / price_entry - 1)`,
   minus the return.
6. **The last row.** A change on the last row opens nothing: with `signal_close` it may still
   close a position (reason `signal`), with `next_open` its order is never filled. A position
   still open after the last row is closed at the last close (reason `end`), with its exit fee
   and slippage, unless rule 9 closes it on that row. A `next_open` order from the row before the last still fills at the last open;
   its stop is checked on the last row like on any entry candle (only a touch counts there), so
   it can still exit by `stop`, and otherwise it ends at the last close.
7. **Equity.** Capital starts at 1.0, is always fully invested and compounds: after each trade
   it is multiplied by `1 + return`. A candle's `equity` is the capital after all its fills,
   with the open position marked at the candle's close:
   `capital * (1 + side * (close / entry - 1) - fee)`, where the entry fee is already paid and
   the exit's fee and slippage are not yet. `position` is the side held after the candle's
   fills, so the last row is always flat.
8. **Ruin.** The capital cannot go below 0:
   - when the equity of an open position, marked at a candle's close as in rule 7, is at or
     below 0, the position closes at that close, with its exit fee and slippage, and reason
     `ruin`;
   - when a closing trade's `return` is at or below -1, for example a gap through the stop or a
     short closed by the signal after its price doubled, nothing is left of the capital.

   Either way the capital becomes 0 (the trade's `return` stays as computed, at or below -1),
   no position opens again, not even the second leg of a flip or a `next_open` order already
   decided, and the `equity` of every later row is 0. Without a stop, a short is ruined once
   its price about doubles.
9. **Drawdown stop.** A risk limit on the run, off by default (`drawdown_stop=None`).
   - The peak is the highest `equity` recorded so far, the starting capital 1.0 included, so
     it is at least 1.0. It is taken from the recorded series, marked at the closes (rule 7):
     the highs and lows inside a candle do not count, so a position can lose more than
     `drawdown_stop` within a candle before it is closed at that candle's close.
   - After a candle's fills, its mark and the ruin check, an `equity` at or below
     `peak * (1 - drawdown_stop)` halts the run; being exactly at the level counts. A position
     still open closes at that candle's close, with its exit fee and slippage, and reason
     `drawdown`; the candle's `position` becomes 0 and its `equity` the capital after that
     trade. A run that is flat at that moment, for example after a stop exit that made the
     drawdown, halts without a trade.
   - After the halt nothing opens again: a `next_open` order already decided is dropped, later
     changes of the signal do nothing, every later row is flat and its `equity` stays the
     capital (not 0, unlike a ruin).
   - With `signal_close`, a position does not open at the close of a candle whose capital,
     after a flip's closing leg or a stop, is already at or below the level; the candle halts
     flat. It would only be closed again at the same close, paying both legs for nothing.
   - Ruin comes first: a candle that ruins the run (rule 8) is a `ruin`, not a `drawdown`.

| `exit_reason` | When | Exit price before slippage |
| --- | --- | --- |
| `signal` | the signal changed to flat or to the other side | the fill price of the decision (`close` or next `open`) |
| `stop` | the low (long) or high (short) reached the stop level | the stop level |
| `gap` | the candle opened at or beyond the stop level | the candle's open |
| `end` | the position was still open after the last row | the last close |
| `ruin` | the position was worth 0 or less at a candle's close (rule 8) | that close |
| `drawdown` | the equity at a candle's close was `drawdown_stop` or more below its peak (rule 9) | that close |

The hand-made examples of each rule are in `backend/tests/unit/engine/test_rules.py`.

## Worked example

Five 30-minute candles from 2024-06-03 13:30 UTC (09:30 in New York), a stop loss of 5% and a
fee of 10 bps (`Settings(stop_loss=0.05, fee_bps=10)`, no slippage). The model goes long on
row 1 and stays long.

| Row | `ts` | open | high | low | close | signal |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | 1717421400 | 100 | 101 | 99 | 100 | 0 |
| 1 | 1717423200 | 100 | 102 | 99.5 | 101 | +1 |
| 2 | 1717425000 | 102 | 104 | 101 | 103 | +1 |
| 3 | 1717426800 | 99 | 99.5 | 96 | 97 | +1 |
| 4 | 1717428600 | 97 | 98 | 96.5 | 98 | +1 |

With `fill="signal_close"`:

| Row | What happens | `position` | `equity` |
| --- | --- | --- | --- |
| 0 | warm-up: the starting signal is 0 | 0 | 1 |
| 1 | the signal changes from 0 to +1: long at the close, 101; stop level `101 * 0.95 = 95.95`; the entry fee is paid | 1 | `1 + (101 / 101 - 1) - 0.001 = 0.999` |
| 2 | the open (102) and the low (101) stay above the level | 1 | `1 + (103 / 101 - 1) - 0.001 = 1.018802` |
| 3 | the low, 96, stays above 95.95 | 1 | `1 + (97 / 101 - 1) - 0.001 = 0.959396` |
| 4 | no change; after the last row the long is closed at the last close, 98 (`end`) | 0 | `1 + return = 0.968327` |

The trade's return is
`98 / 101 - 1 - 0.001 * (1 + 98 / 101) = -0.029703 - 0.001970 = -0.031673`.

With `fill="next_open"` the same signal fills one candle later, at a higher price, so its stop
is higher too:

| Row | What happens | `position` | `equity` |
| --- | --- | --- | --- |
| 0 | warm-up | 0 | 1 |
| 1 | the signal changes: an order for the next open | 0 | 1 |
| 2 | the order fills at the open: long at 102; stop level `102 * 0.95 = 96.9`; on its entry candle only a touch counts, and the low, 101, is above it | 1 | `1 + (103 / 102 - 1) - 0.001 = 1.008804` |
| 3 | the open, 99, is above the level (no gap), but the low, 96, reaches it: out at 96.9 (`stop`) | 0 | `1 + return = 0.94805` |
| 4 | the signal is still +1, which is no change: no new long | 0 | 0.94805 |

The trade's return is `96.9 / 102 - 1 - 0.001 * (1 + 0.95) = -0.05 - 0.00195 = -0.05195`.

The two `trades` rows:

| Column | `signal_close` | `next_open` |
| --- | --- | --- |
| `signal_ts` | 1717423200 (row 1) | 1717423200 (row 1) |
| `open_ts` | 1717423200 (row 1) | 1717425000 (row 2) |
| `close_ts` | 1717428600 (row 4) | 1717426800 (row 3) |
| `side` | 1 | 1 |
| `open_price` | 101 | 102 |
| `close_price` | 98 | 96.9 |
| `stop_price` | 95.95 | 96.9 |
| `exit_reason` | `end` | `stop` |
| `return` | -0.031673 | -0.05195 |
| `fees` | 0.001970 | 0.00195 |
| `slippage_cost` | 0 | 0 |

`backend/tests/unit/engine/test_doc_example.py` runs this example, the slippage variant, its
statistics, the `InvalidInput` message under [Input](#input) and the command below with its
`run.json`, and compares them with what is printed here. Change both together.

## Fees and slippage

The two costs work differently:

- **Fee: a commission, nothing else.** Each leg pays `fee_bps` of its notional: the entry leg
  pays `fee * capital`, the exit leg `fee * capital * exit / entry` (the position's value at
  the exit). The trade's `fees` column is their sum as a fraction of the capital before the
  trade, `fee * (1 + exit / entry)`. The fee never changes a price.
- **Slippage: a worse price, nothing else.** With `s = slippage_bps * 0.0001` and `side` +1 or
  -1, an entry fills at `price * (1 + side * s)`, an exit (`signal`, `gap` or `end`) at
  `price * (1 - side * s)`, and a stop exit at `level * (1 - side * s)`. The stop level comes
  from the slipped entry price. `open_price` and `close_price` include slippage; `stop_price` is
  the level before the exit's slippage. Slippage never shows in `fees`; it shows in the prices,
  the return and `slippage_cost`: the return at the prices before slippage (the entry's fill
  price and the exit's fill price or stop level), with the same fees, minus the return, that is
  `side * (price_exit / price_entry - exit / entry)`.

The worked example with `fill="next_open"` and 10 bps of slippage as well
(`Settings(fill="next_open", stop_loss=0.05, fee_bps=10, slippage_bps=10)`):

- entry: `102 * 1.001 = 102.102`;
- stop level: `102.102 * 0.95 = 96.9969`, from the slipped entry, so above 96.9;
- the low of row 3 reaches it: exit at `96.9969 * 0.999 = 96.8999031`;
- return: `96.8999031 / 102.102 - 1 - 0.001 * (1 + 0.94905) = -0.05095 - 0.00194905`,
  which is -0.05289905;
- `fees` is 0.00194905: the commission only, a little less than without slippage because the
  exit leg is worth less;
- `slippage_cost` is 0.0019: before slippage the trade was in at 102 and out at the level,
  96.9969, so `96.9969 / 102 - 96.8999031 / 102.102 = 0.95095 - 0.94905`. With `fees` that is
  0.00384905, the return without costs, `96.9969 / 102 - 1 = -0.04905`, minus the return;
- `equity` is 1, 1, 1.007795, 0.947101, 0.947101.

## Signals

| Function | What it does |
| --- | --- |
| `relabel(frame, timeframe_seconds, column="ts")` | turns close-time labels into open-time labels: `column - timeframe_seconds`, as Int64. The supervisor labels a 30-minute candle and its prediction by the candle's close; the engine and `align` expect its open. Raises `ValueError` when the column is missing, not integer or has a missing value, or the timeframe is not a positive int |
| `align(candles, predictions, *, model=None, strict=True)` | the candles from the model's first to its last prediction, each with its `prediction`, sorted by `ts`, and an `AlignReport` |
| `threshold(predictions, limit)` | the signal of each prediction `p`: 1.0 if `p > 0` and `p >= limit`, -1.0 if `p < 0` and `p <= -limit`, else 0.0 |
| `long_only(signal)` | the signal without shorts: a negative value becomes 0.0, the rest stays |
| `min_hold(signal, candles)` | the signal with each new position (a change to a non-zero value, after the first row) kept for at least `candles` rows; 1 leaves it as it is |
| `zscore(predictions, window)` | each prediction in standard deviations from the mean of the `window` predictions ending at it (itself included, sample std); 0.0 before the first full window and on a window without spread |
| `rank(predictions, window)` | where each prediction ranks among the `window` predictions ending at it, in [-1, 1]: the largest 1.0, the smallest -1.0, 0.0 with as many below as above; 0.0 before the first full window |
| `kmeans(predictions, k, window, refit_every)` | the signal of each prediction from its group among `k` groups (2 to 5) of the `window` predictions before the latest refit row: the lowest group gives -1.0, the highest 1.0, the others 0.0 |
| `ema(predictions, span)` | the exponential moving average of the predictions with `alpha = 2 / (span + 1)`: the first value is the first prediction, then `e[t] = alpha * p[t] + (1 - alpha) * e[t-1]`; a span of 1 gives the predictions back. Keeps the name |
| `hysteresis(predictions, enter, exit)` | a `signal` with two limits, a state machine that starts flat: flat goes long when `p > 0` and `p >= enter`, short when `p < 0` and `p <= -enter`; long stays long while `p > 0` and `p >= exit`, otherwise it goes short when `p < 0` and `p <= -enter`, else flat; short mirrors long. With `exit` equal to `enter` it is `threshold` |
| `confirm(signal, candles)` | the signal with a value counted only once it has held for `candles` rows in a row; until then the last counted value stays. The first row counts at once; 1 candle gives the signal back. Keeps the name |

`align` in detail:

- It joins on `ts`, which must label the candle's open on both sides (see `relabel`). `ts`
  must be integer and unique in both frames (per model for the predictions); the candles must
  not have a `prediction` column yet.
- `predictions` has `ts` and `prediction`, and `model` when it holds several models; then
  `model=` picks one and is required. Each prediction must be a number: a missing (null) or
  NaN one is refused with their count and the first `ts`, and a row with a missing prediction
  still counts for the unique `ts`.
- The rows run from the first to the last prediction that has a candle. Missing candles are
  never filled in.
- `AlignReport` counts what did not match one to one: `predictions_without_candle` and
  `candles_without_prediction` (gaps inside the rows).
- `strict=True` (the default, and what the command uses) raises a `ValueError` on either kind,
  naming the first `ts`. With `strict=False` a prediction without a candle is dropped, and a
  candle without a prediction repeats the previous one: the model said nothing new, so the
  signal does not change and no order follows, but the candle stays for the stop and the
  equity.

`threshold` and `long_only` take a Polars Series and return a Float64 Series, a plain 0.0 for
flat (never -0.0); the one `threshold` returns is named `signal`, the engine's column.
`threshold` compares in float64: a float32 prediction is widened first, so it meets the limit
only when its value does. Both raise `ValueError` on a missing or NaN value or a column that is
not numeric; `threshold` also on a negative or NaN `limit`. For example, with a limit of 0.05:

| Prediction | 0.01 | 0.3 | -0.02 | -0.08 | 0.05 |
| --- | --- | --- | --- | --- | --- |
| `threshold` | 0.0 | 1.0 | 0.0 | -1.0 | 1.0 |
| `long_only` of that | 0.0 | 1.0 | 0.0 | 0.0 | 1.0 |

Calibration (`zscore`, `rank`) comes before `threshold` when models predict on different
scales: the supervisor's predictions stay within about ±0.4, another model's may reach ±5, so
one limit does not fit both. Each function rescales a prediction from the `window` predictions
ending at it (rows t-window+1..t, the prediction itself included: it is known at t) and from
nothing later, so it adds no look-ahead; `test_predictions_after_a_row_change_nothing_up_to_it`
in `backend/tests/unit/signals/test_calibration.py` shows it on random predictions. The first
window-1 rows have no full window yet and give 0.0 (flat), as does a `zscore` window without
spread (std 0, or within float rounding of its values). `rank` counts the other window-1
predictions: `below` smaller than p[t] and `equal` equal to it, r = (below + equal / 2) /
(window - 1), and gives 2r - 1, so tied predictions share the middle of their places and 0.0
has as many below as above. Both take a Series and return a Float64
Series with the input's name and a plain 0.0, and raise `ValueError` on a missing or NaN value,
a column that is not numeric or a `window` that is not an int of 2 or more. With a window of 3,
and `threshold` at 1.0, which on a z-score means one standard deviation:

| Prediction | -0.25 | 0.25 | 0.0 | 0.5 | 0.5 |
| --- | --- | --- | --- | --- | --- |
| `zscore` | 0.0 | 0.0 | 0.0 | 1.0 | 0.58 |
| `rank` | 0.0 | 0.0 | 0.0 | 1.0 | 0.5 |
| `threshold` of either | 0.0 | 0.0 | 0.0 | 1.0 | 0.0 |

`ema`, `hysteresis` and `confirm` post-process the same way: a Polars Series in, a Float64
Series out, a plain 0.0, never -0.0. They raise `ValueError` on a missing or NaN value or a
column that is not numeric, on a `span` or `candles` that is not an int of 1 or more (a bool is
not one), and on limits that are not finite with `0 <= exit <= enter`. `ema` also refuses an
infinite prediction, which would make every average after it inf or NaN (`threshold` and
`hysteresis` take one as any number). Each is one pass forward: a value at row t comes from
the rows up to t only, and `test_values_after_a_row_change_nothing_up_to_it` in
`backend/tests/unit/signals/test_filters.py` proves it the way the engine's test does, by
replacing every value after a row with other random values. The steps go in this order: the
predictions, calibration (`zscore` or `rank`) when the scales differ, `ema` to smooth them,
one rule that makes the signal (`threshold`, `hysteresis` or `kmeans`), `long_only`,
`confirm`, then `min_hold`. Calibration and smoothing come before the rule because the rule
reads the level of the prediction; `confirm` comes after `long_only` because it counts rows of
the signal the engine will trade on, and `min_hold` last, so the hold starts at the entry the
engine sees. For example, with a span of 3 (`alpha` 0.5), `threshold` at 0.05 and 2 candles
to confirm, the long opens one row late and closes one row late:

| Prediction | 0.0 | 0.2 | 0.2 | -0.2 | -0.2 | -0.2 |
| --- | --- | --- | --- | --- | --- | --- |
| `ema`, span 3 | 0.0 | 0.1 | 0.15 | -0.025 | -0.1125 | -0.15625 |
| `threshold` at 0.05 | 0.0 | 1.0 | 1.0 | 0.0 | -1.0 | -1.0 |
| `long_only` of that | 0.0 | 1.0 | 1.0 | 0.0 | 0.0 | 0.0 |
| `confirm`, 2 candles | 0.0 | 0.0 | 1.0 | 1.0 | 0.0 | 0.0 |

`min_hold` is a post-processing step on the signal, before the engine. Once its output changes
to a non-zero value at row r (an entry from flat, or a flip), it stays there through row
r + candles - 1: during the hold the input's changes are ignored, a flat one like any other.
From row r + candles on it follows the input again, where a change may start a new hold. Going
flat starts no hold, and neither does the first row: it only sets the starting value (the
output of row 0 is the input), as the engine's warm-up row (rule 1) opens nothing there either.
It takes and returns a Float64 Series like `long_only` (same name, plain 0.0, same `ValueError`
on a bad value), raises `ValueError` when `candles` is not an int of 1 or more, and is causal:
the output up to a row depends only on the input up to it
(`backend/tests/unit/signals/test_holding.py` checks it on random signals, and that the output
differs from the input only inside a hold). The engine does not know about the hold: its stop
loss still closes a held position early (risk before holding). The hold ignores the input after
that stop as well, so the run stays flat at least until the hold ends, and rule 4 then opens a
position only on the next change of the signal. For example, with 3 candles:

| Input | 0.0 | 1.0 | 0.0 | -1.0 | -1.0 | 1.0 |
| --- | --- | --- | --- | --- | --- | --- |
| `min_hold` of that | 0.0 | 1.0 | 1.0 | 1.0 | -1.0 | -1.0 |

`kmeans` needs no limit: it sorts the predictions into `k` groups by value (one-dimensional
k-means, deterministic) and trades the extremes, long in the highest group and short in the
lowest; with `k=2` there is no flat group. The groups are fitted only on the past: the fit used
at row t was made at the latest refit row f <= t on the `window` predictions before f (rows
f-window to f-1, never p[f] itself), and refits happen at rows `window`, `window +
refit_every`, and so on. The first `window` rows have no fit yet and give 0.0, the warm-up.
A window whose values are all equal has no groups: every row using that fit gives 0.0. The
arguments are ints: `k` in 2..5, `window` at least `2 * k`, `refit_every` at least 1; the
predictions are checked like `threshold`, and an infinite one is refused too. For example,
with `k=3`, `window=6` and
`refit_every=100`, the first six predictions 1, 5, 9, 9, 1, 5 form the groups around 1, 5 and
9, and the rows after them use that fit:

| Prediction | 1 | 5 | 9 | 9 | 1 | 5 | 0.5 | 4.0 | 9.5 | 3.0 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `kmeans` | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | -1.0 | 0.0 | 1.0 | -1.0 |

(3.0 is exactly between the centers 1 and 5, and a tie goes to the lower group.)

One run from code, the steps the command takes:

```python
from candlestack.engine import Settings, backtest
from candlestack.metrics import stats
from candlestack.signals import align, threshold

rows, report = align(candles, predictions, model="M")
frame = rows.with_columns(threshold(rows["prediction"], 0.05))
result = backtest(frame, Settings(stop_loss=0.05, fee_bps=5))
values = stats(result.trades, result.series, rows)
```

## Metrics

`drawdown(series)` is a Float64 series named `drawdown`, one value per row:
`equity[t] / peak[t] - 1` with `peak[t] = max(1.0, equity[0], ..., equity[t])`. It is 0 at a
new high and -0.2 at 20% below the peak. The starting capital 1.0 is the first peak, so a run
that loses from its start is in drawdown from its first row.

`stats(trades, series, candles, periods_per_year=252)` returns a dict with these names, always
in this order. Returns are fractions (0.05 is 5%).

Daily returns `r`, used by Sharpe and Sortino: the day of a row is the UTC date of its `ts`
(`ts // 86400`), `E[d]` is the equity of the last row of day `d`, and
`r[d] = E[d] / E[d-1] - 1`, the first day's from the starting capital 1.0. Days without rows are
skipped, and the risk-free rate is 0. For US stocks the UTC date is the New York date of the
session, because a regular session lies within one UTC day.

| Name | Formula | None when |
| --- | --- | --- |
| `total_return` | `equity[last] - 1` | `series` has no rows |
| `max_drawdown` | `-min(drawdown)`: a positive fraction, 0 when equity never falls below a peak | `series` has no rows |
| `sharpe` | `mean(r) / std(r) * sqrt(periods_per_year)`, `std` with ddof 1 | fewer than 2 days, or `std(r)` is 0 |
| `sortino` | `mean(r) / sqrt(mean(min(r, 0) ** 2)) * sqrt(periods_per_year)`: the downside deviation over all days | fewer than 2 days, or no losing day |
| `num_trades` | rows of `trades`, the `end` trade included (an int) | never |
| `win_rate` | `count(return > 0) / num_trades`; a return of exactly 0 is no win | no trades |
| `avg_trade_return` | `mean(return)` | no trades |
| `best_trade_return` | `max(return)` | no trades |
| `worst_trade_return` | `min(return)` | no trades |
| `exposure` | `count(position != 0) / rows of series`: the share of candles that close with a position | `series` has no rows |
| `fees_paid` | `sum(fees)`, 0 without trades | never |
| `buy_and_hold_return` | `close[last] / close[first] - 1` over the candles whose `ts` is in `series` | no candle has a `ts` of `series` |

- `periods_per_year` scales Sharpe and Sortino to a year: 252 trading days for stocks, 365 for
  crypto. The command always uses 252.
- Once the equity has reached 0, a day that starts from 0 has no return, so there are no daily
  returns at all and Sharpe and Sortino are None.
- A value that would come out infinite or NaN is None too. None, never NaN, keeps the result
  valid JSON (`null` in `stats.json`).
- `fees_paid` adds fractions of different capitals (each trade's capital before it), so it is
  close to, but not exactly, the share of the starting capital paid in fees.
- The last row is always flat (rule 7), so `exposure` never counts it.

The worked example with `signal_close` gives:

| Name | Value | Why |
| --- | --- | --- |
| `total_return` | -0.031673 | `0.968327 - 1` |
| `max_drawdown` | 0.058310 | the peak 1.018802 (row 2), then 0.959396 (row 3): `1 - 0.959396 / 1.018802` |
| `sharpe`, `sortino` | None | one day only |
| `num_trades` | 1 | the `end` trade |
| `win_rate` | 0.0 | its return is below 0 |
| `avg_trade_return`, `best_trade_return`, `worst_trade_return` | -0.031673 | one trade |
| `exposure` | 0.6 | rows 1, 2 and 3 of 5 close long |
| `fees_paid` | 0.001970 | the trade's `fees` |
| `buy_and_hold_return` | -0.02 | `98 / 100 - 1` |

## The command

`candlestack-bt run` backtests one model's predictions on the candles of one stock from
Parquet files. From `backend/`: `uv run candlestack-bt run ...` (`candlestack-bt` without a
command prints the help).

The steps are the public functions above, so a run from code gives the same numbers: read both
files, rename their time column to `ts`, `relabel` both with `--labels close`, `align`
(strict unless `--allow-gaps`), the post-processing in the order of [Signals](#signals)
(calibration, `ema`, one rule, `long_only`, `confirm`, `min_hold`; by default only
`threshold`), `backtest`, `stats`. The order of the steps is in one place in the code,
`prepare` and `decide` (together `make_signal`) of `PostProcessing` in
`signals/post_processing.py`, which `run-all` uses too, and `describe` records them in
`run.json`; the command's options for them are in `cli/post_processing.py`.

| Option | Default | Meaning |
| --- | --- | --- |
| `--candles PATH` | required | Parquet file of the candles: the time column, `open`, `high`, `low`, `close` |
| `--predictions PATH` | required | Parquet file of the predictions: the time column, `prediction` (and `model`) |
| `--out DIR` | required | folder for the four result files, created if needed |
| `--ts-column NAME` | `ts` | the time column of both files, integer UTC epoch seconds; renamed to `ts` (the supervisor's files use `unix`) |
| `--labels open\|close` | `open` | whether the time is a candle's open or its close; `close` moves both files back by `--timeframe` |
| `--timeframe LENGTH` | none | the length of a candle: a number and `m`, `h` or `d` (`30m`, `1h`, `1d`); needed with `--labels close`, refused with `--labels open` |
| `--model NAME` | none | backtest only this model's predictions (column `model`); needed when the file holds several models |
| `--allow-gaps` | off | `align(strict=False)`: a prediction without a candle is dropped and a candle without a prediction repeats the previous one; `run.json` counts both. Off, either stops the run |
| `--calibrate zscore\|rank` | off | `zscore` or `rank` over the `--calibration-window` predictions ending at each one; the two go together |
| `--calibration-window N` | none | the window of `--calibrate`, 2 or more |
| `--ema SPAN` | off | `ema` with this span, 1 or more |
| `--rule threshold\|hysteresis\|kmeans` | `threshold` | the rule that makes the signal; an option of another rule is refused |
| `--threshold LIMIT` | `0` | `threshold`: long at a prediction of LIMIT or more, short at -LIMIT or less, else flat; with `--rule hysteresis` the `enter` limit; refused with `--rule kmeans` |
| `--exit LIMIT` | none | `hysteresis`: the `exit` limit, at most `--threshold`; needed with `--rule hysteresis` |
| `--k 2..5`, `--kmeans-window N`, `--refit-every N` | none | `kmeans(k, window, refit_every)`, the window at least `2 * k`; all three needed with `--rule kmeans` |
| `--long-only` | off | flat instead of short |
| `--confirm CANDLES` | off | `confirm` with this many candles |
| `--min-hold CANDLES` | off | `min_hold` with this many candles |
| `--fill next_open\|signal_close` | `next_open` | `Settings.fill` |
| `--stop-loss FRACTION` | no stop | `Settings.stop_loss`, between 0 and 1 (0.05 is 5%) |
| `--drawdown-stop FRACTION` | no drawdown stop | `Settings.drawdown_stop`, between 0 and 1 (0.2 is 20%) |
| `--fee-bps BPS` | `0` | `Settings.fee_bps` |
| `--slippage-bps BPS` | `0` | `Settings.slippage_bps` |

The post-processing options are off unless given, so a run without them makes the same files
as before they existed, `run.json` aside. A step's argument out of its range (`--ema 0`,
`--k 6`) or a mix that makes no sense (`--calibrate` without `--calibration-window`, `--exit`
without `--rule hysteresis`, `--rule kmeans` without `--k`, an `--exit` above `--threshold`, a
`--kmeans-window` under `2 * --k`) is a usage error. For example, z-scores over 500
predictions, smoothed over 5, hysteresis at one standard deviation that closes at half of one,
long only, confirmed over 2 candles and held for 10, with a 20% drawdown stop:

```sh
uv run candlestack-bt run --candles candles.parquet --predictions predictions.parquet \
  --calibrate zscore --calibration-window 500 --ema 5 \
  --rule hysteresis --threshold 1 --exit 0.5 --long-only --confirm 2 --min-hold 10 \
  --drawdown-stop 0.2 --out runs/calibrated
```

What it writes to `--out`:

| File | Content |
| --- | --- |
| `trades.parquet` | the engine's `trades` |
| `series.parquet` | the engine's `series` with a `drawdown` column |
| `stats.json` | the statistics, None as `null`, in the order of [Metrics](#metrics) |
| `run.json` | `engine_version`; `inputs`: path and SHA-256 of both files; `settings`: the time label options, `model`, `allow_gaps`, `threshold` (the rule's limit, null with `kmeans`), `long_only` and the `Settings` fields; `post_processing`: each step in the order applied, with the arguments of its function, null when off (the rule under `rule` with its `name`); `align`: the `AlignReport` |

Exit codes:

| Code | When |
| --- | --- |
| 0 | the run wrote its four files |
| 2 | a wrong option or a wrong mix of options (`--labels close` without `--timeframe`, `--timeframe` with `--labels open`, a post-processing mix listed above): the usage and the reason |
| 2 | a missing or unreadable file, a missing or mistyped column, a missing or NaN prediction, a prediction without a candle or a candle without a prediction (without `--allow-gaps`), or any other data a step (or Polars) refuses: one line, `candlestack-bt run: error: <reason>`, never a traceback |

On exit code 2 it writes no file. The same files and options write the same bytes again.

The worked example from files (the candles without `signal`, predictions of 0.01, 0.3, 0.2, 0.1
and 0.4, which the threshold turns into 0, +1, +1, +1, +1):

```sh
uv run candlestack-bt run --candles candles.parquet --predictions predictions.parquet \
  --threshold 0.05 --fill signal_close --stop-loss 0.05 --fee-bps 10 --out runs/example
```

```text
Wrote trades.parquet, series.parquet, stats.json and run.json to runs/example
  trades        1
  win rate      0.00%
  total return  -3.17%
  max drawdown  5.83%
  sharpe        n/a
  buy and hold  -2.00%
```

Its `run.json` (hashes shortened):

```json
{
  "engine_version": "0.3.0",
  "inputs": {
    "candles": {"path": "candles.parquet", "sha256": "509ac6b5..."},
    "predictions": {"path": "predictions.parquet", "sha256": "efe0645e..."}
  },
  "settings": {
    "ts_column": "ts",
    "labels": "open",
    "timeframe_seconds": null,
    "model": null,
    "allow_gaps": false,
    "threshold": 0.05,
    "long_only": false,
    "fill": "signal_close",
    "stop_loss": 0.05,
    "drawdown_stop": null,
    "fee_bps": 10.0,
    "slippage_bps": 0.0
  },
  "post_processing": {
    "calibrate": null,
    "ema": null,
    "rule": {"name": "threshold", "limit": 0.05},
    "long_only": false,
    "confirm": null,
    "min_hold": null
  },
  "align": {"predictions_without_candle": 0, "candles_without_prediction": 0}
}
```

The command above, with every step on, records them as
`"calibrate": {"name": "zscore", "window": 500}`, `"ema": {"span": 5}`,
`"rule": {"name": "hysteresis", "enter": 1.0, "exit": 0.5}`, `"long_only": true`,
`"confirm": {"candles": 2}` and `"min_hold": {"candles": 10}`; the k-means rule is
`"rule": {"name": "kmeans", "k": 3, "window": 500, "refit_every": 100}`.

Files labelled by the candle's close in a `unix` column, with several models:
`--ts-column unix --labels close --timeframe 30m --model <name>`.

### Every stock, model and setting: `run-all`

`candlestack-bt run-all` runs every stock × model × threshold × stop loss of a data folder in
one command, like the supervisor's `all_stats`. Each combination runs the steps of `run` with
the same functions, so it gives the trades and statistics `run` gives with the same options. A
stock's files are read and relabelled once and aligned once per model (`align` strict unless
`--allow-gaps`); the post-processing before the rule (calibration, `ema`) runs once per model
too, as only the rule reads the threshold.

```text
<--data>/
  AAPL/
    candles.parquet       the time column, open, high, low, close
    predictions.parquet   the time column, model, prediction
  <another stock>/
    ...
  _raw/                   a folder whose name starts with _ or . is no stock
```

```sh
uv run candlestack-bt run-all --data <folder> --ts-column unix --labels close --timeframe 30m \
  --allow-gaps --models all --thresholds 0,0.01,0.05,0.1 --stop-losses 0.05,0.07,0.1 \
  --fill signal_close --jobs 4 --out runs/all
```

| Option | Default | Meaning |
| --- | --- | --- |
| `--data DIR` | required | folder with one folder per stock holding `candles.parquet` and `predictions.parquet` |
| `--out DIR` | required | folder for the three result files, created if needed |
| `--ts-column`, `--labels`, `--timeframe` | as in `run` | the time labels of all files |
| `--allow-gaps` | off | as in `run`, for every stock and model; `run.json` counts the gaps in each align report |
| `--models NAMES` | `all` | comma-separated models (column `model`), or `all`: every model of each stock, by name |
| `--calibrate`, `--calibration-window`, `--ema`, `--rule`, `--exit`, `--k`, `--kmeans-window`, `--refit-every`, `--long-only`, `--confirm`, `--min-hold` | as in `run` | the post-processing, the same for every combination; the same mixes are refused |
| `--thresholds LIMITS` | `0` | comma-separated thresholds, each as `run`'s `--threshold`: the limits of `threshold`, or the `enter` limits of `hysteresis` (each at least `--exit`); refused with `--rule kmeans`, where the one `threshold` key is null |
| `--fill next_open\|signal_close` | `next_open` | `Settings.fill`, for every combination |
| `--stop-losses FRACTIONS` | `none` | comma-separated stop losses, each as `run`'s `--stop-loss`, or `none` for no stop |
| `--drawdown-stop FRACTION` | no drawdown stop | `Settings.drawdown_stop`, for every combination |
| `--fee-bps BPS` | `0` | `Settings.fee_bps`, for every combination |
| `--slippage-bps BPS` | `0` | `Settings.slippage_bps`, for every combination |
| `--jobs N` | `1` | run N stocks at a time in their own processes; the output does not depend on N |

A value may not repeat within a list. The combinations of a stock are the models (in the order
of `--models`, or by name) × the thresholds × the stop losses, each in the order given. The
example above with the predictions calibrated by rank (so the limits are places in [-1, 1])
and the signal confirmed, the hysteresis rule opening at each of the thresholds and closing
at 0.3:

```sh
uv run candlestack-bt run-all --data <folder> --ts-column unix --labels close --timeframe 30m \
  --allow-gaps --calibrate rank --calibration-window 500 --rule hysteresis \
  --thresholds 0.5,0.8 --exit 0.3 --confirm 2 --stop-losses none,0.05 --jobs 4 --out runs/all
```

What it writes to `--out`:

| File | Content |
| --- | --- |
| `stats.parquet` | one row per stock × model × threshold × stop loss, stocks by folder name: the keys `stock`, `model`, `threshold`, `stop_loss` (null for no stop); the shared settings `long_only`, `fill`, `fee_bps`, `slippage_bps`; then every statistic of [Metrics](#metrics) in its order, null when `run` writes `null` |
| `trades.parquet` | the engine's `trades` of every combination (all their columns, `slippage_cost` and the exit reason `ruin` included), in the same order, after the four keys |
| `run.json` | `engine_version`; `data`; `settings`: the time label options, `allow_gaps`, `models`, `thresholds` (`[null]` with `kmeans`), `long_only`, `fill`, `stop_losses`, `drawdown_stop`, `fee_bps`, `slippage_bps`; `post_processing`: as in `run`, but the rule's `limit` (or `enter`) is the list of `--thresholds`, as each run takes one of them; `models`: the models run, by name; `stocks`: for each stock whose files were read, its name, `inputs` (path and SHA-256 of both files) and `align` (the `AlignReport` of each model that ran); `failed`: each failure as `stock`, `model` (null when the whole stock failed) and `error` |

It prints how many stocks ran and failed, how many models ran and failed on a stock, and how
many runs and trades it wrote.

A failure does not stop the others; it is listed in `run.json` under `failed` and printed on
one line, never as a traceback:

- A stock fails when its files are refused as `run` refuses them (a missing or unreadable file,
  a missing or mistyped column, any other data a step or Polars refuses) or when its column
  `model` names no model: `candlestack-bt run-all: error: <stock>: <reason>`. It is left out of
  `stocks` and of both Parquet files.
- A model of a stock fails when the stock lacks a model asked for in `--models`, or when `align`
  or a run refuses it (a candle without a prediction without `--allow-gaps`, a missing or NaN
  prediction, a repeated `ts`, an error from Polars):
  `candlestack-bt run-all: error: <stock> '<model>': <reason>`. Only that model's rows are left
  out; the stock's other models run.

Exit codes: 0 when everything ran, 1 when a stock or a model of a stock failed, 2 for a wrong
option or a wrong mix of options (as in `run`), a missing data folder, a data folder without a
stock folder or an `--out` it cannot write to. The same files and options write the same bytes
again, whatever `--jobs` is.

## The result document

What a finished backtest is when the app shows it, the API returns it or a user downloads it as
JSON. Its models are `experiments/document.py`, and `experiments/assembly.py` runs the steps and
writes its results; the routes come with the backtests API. One shape serves all three uses: the
page and the API get a lighter view of the two long tables
([The light view](#the-light-view)), the download gets everything.

### Stored, and built on request

The engine is deterministic: the same candles, predictions and settings give the same bytes. A
backtest stores only what cannot be recomputed quickly:

| Stored | Size | Why |
| --- | --- | --- |
| the setup: the model (ID, name, SHA-256, a copy of its manifest), the instrument and period, the decision and the execution settings | a few KB | the run cannot be repeated without it |
| the predictions: `ts` and `prediction` per row, Parquet | about 140 KB for 10 years of 30-minute stock candles, 700 KB for crypto | running the model is the slow part |
| `stats`, the 12 metrics | 1 KB | the Backtests list and the Dashboard sort by them |
| the fingerprint and range of the loaded candles | under 1 KB | tells whether the candles changed since |

The series, the trades and the document itself are built when they are asked for: the candles
from the cache, the stored predictions, then `signals`, `backtest` and `metrics`, well under a
second. A source can re-adjust old prices (after a split or a dividend); then the candles'
fingerprint differs, the document says so (`data.changed` is true) and is built from the
candles as they are now, so its `results.stats` can differ from the stored ones.

### The shape

The engine's worked example ([Worked example](#worked-example)) with `next_open`, a 5% stop and
10 bps fees, as a document. `//` comments are notes here, not part of the format.

```jsonc
{
  "version": 1,
  "backtest": {
    "id": "bt_7f3k2m9qx4", "number": 12, "status": "finished",   // running, finished or failed
    "created_at": "2026-10-25T14:02:11Z", "finished_at": "2026-10-25T14:04:53Z",
    "duration_s": 162.0, "error": null
  },
  "software": {
    "engine_version": "0.3.0", "app_version": "2026.10.24-3f2a1c9",
    "runner": {"format": "keras", "keras": "3.15.1", "backend": "jax 0.7.2"}
  },
  "model": {
    "id": "mdl_k2m9qx47f3", "name": "raw-candles.keras", "framework": "keras",
    "sha256": "9b1e04c7…", "size_bytes": 48213,
    "manifest": {"version": 1, "input": {"…": "…"}, "signature": {"…": "…"}}   // as stored with the run
  },
  "data": {
    "instrument": {"id": "stock:AAPL", "market": "stock", "symbol": "AAPL", "name": "Apple Inc.",
                   "exchange": "NASDAQ", "source": "alpaca", "feed": "sip", "currency": "USD"},
    "timeframe": "30m", "timeframe_seconds": 1800, "session": "regular",
    "period": {"start": 1717372800, "end": 1717459200},
    "loaded": {"start": 1717081200, "end": 1717430400, "count": 29, "fingerprint": "sha256:3b1f…", "gaps_total": 0},
    "changed": false,
    "warm_up": {"candles": 24, "kind": "exact", "dropped": 24},
    "rows": {"first_ts": 1717421400, "last_ts": 1717428600, "count": 5},
    "predictions": {"count": 5, "sha256": "e1aa90d2…"},
    "align": {"predictions_without_candle": 0, "candles_without_prediction": 0}
  },
  "decision": {"calibrate": null, "ema": null, "rule": {"name": "threshold", "limit": 0.05},
               "long_only": false, "confirm": null, "min_hold": null},
  "execution": {"fill": "next_open", "stop_loss": 0.05, "drawdown_stop": null,
                "fee_bps": 10.0, "slippage_bps": 0.0},
  "benchmarks": [{"id": "bh", "kind": "buy_and_hold", "instrument": "stock:AAPL"}],
  "metrics": {"periods_per_year": 252, "risk_free_rate": 0.0},
  "results": {                                                    // null unless finished
    "stats": {"total_return": -0.05195, "max_drawdown": -0.060224, "sharpe": null, "sortino": null,
              "num_trades": 1, "win_rate": 0.0, "avg_trade_return": -0.05195,
              "best_trade_return": -0.05195, "worst_trade_return": -0.05195,
              "exposure": 0.2, "fees_paid": 0.00195, "buy_and_hold_return": -0.02},
    "benchmarks": {"bh": {"total_return": -0.02}},
    "series": {
      "resolution": "candle", "count": 5,
      "columns": {
        "ts":         [1717421400, 1717423200, 1717425000, 1717426800, 1717428600],
        "close":      [100.0, 101.0, 103.0, 97.0, 98.0],
        "prediction": [0.01, 0.3, 0.2, 0.1, 0.4],
        "signal":     [0.0, 1.0, 1.0, 1.0, 1.0],
        "position":   [0.0, 0.0, 1.0, 0.0, 0.0],
        "equity":     [1.0, 1.0, 1.008804, 0.94805, 0.94805],
        "drawdown":   [0.0, 0.0, 0.0, -0.060224, -0.060224]
      },
      "benchmarks": {"bh": [1.0, 1.01, 1.03, 0.97, 0.98]}
    },
    "trades": {
      "count": 1, "total": 1, "complete": true,
      "columns": {
        "signal_ts": [1717423200], "open_ts": [1717425000], "close_ts": [1717426800],
        "side": [1], "open_price": [102.0], "close_price": [96.9], "stop_price": [96.9],
        "exit_reason": ["stop"], "return": [-0.05195], "fees": [0.00195],
        "slippage_cost": [0.0], "candles_held": [1]
      }
    }
  }
}
```

The groups follow the builder's steps (Model, Data, Decision, Execution): `model`, `data`,
`decision`, `execution` and `benchmarks` together are what a new run needs, so running a
backtest again sends them back. `backend/tests/unit/experiments/test_document.py` builds this
example as a document and compares it with what is printed here; change both together.

| Group | Fields | From |
| --- | --- | --- |
| `backtest` | ID, the user's number, status, when it was created and finished (ISO 8601 UTC), seconds it took, the error of a failed run | the stored backtest |
| `software` | `ENGINE_VERSION`, the app's version, the versions of the model runner | the engine, the app, the worker |
| `model` | ID (null once the model is deleted), file name, framework, SHA-256, size, the manifest copy | the model at run time |
| `data` | the instrument, timeframe and session; `period`: the user's dates; `loaded`: the candles asked for, warm-up included, with their fingerprint; `changed`; `warm_up` as the preprocessing reports it; `rows`: what the engine ran on; the predictions' count and SHA-256; the `AlignReport` | the data module, the preprocessing, `align` |
| `decision` | each post-processing step in the order applied, null when off: exactly what `run.json` records | the steps ([Signals](#signals)) |
| `execution` | the engine's `Settings` | [Settings](#settings) |
| `benchmarks` | what the run is compared with: an ID, a kind and an instrument. Version 1 has buy and hold only | the builder |
| `metrics` | the arguments `stats` was called with | [Metrics](#metrics) |
| `results.stats` | the 12 metrics of [Metrics](#metrics), in their order | `stats`, with `max_drawdown` negative |
| `results.benchmarks` | each benchmark's `total_return` | `buy_and_hold_return` for `bh` |
| `results.series` | `ts`, `close`, `prediction`, `signal`, `position`, `equity`, `drawdown` per row; each benchmark's equity on the same rows | the engine's `series`, the candles, `drawdown` |
| `results.trades` | the engine's trade columns, plus `candles_held`: the rows from the entry candle to the exit candle | the engine's `trades` |

### Conventions

- **Tables are columns:** `{"ts": [...], "equity": [...]}`, index `i` of every array is one
  row, as the candles endpoint does. Column names are the engine's, so a download maps one to
  one onto `trades.parquet` and `series.parquet`.
- **Market time** (every `ts`, `*_ts`, `start` and `end`) is integer UTC epoch seconds of a
  candle's open, ranges `[start, end)`. **Wall-clock time** (`created_at`, `finished_at`) is ISO
  8601 UTC.
- **Returns, fees, drawdown and exposure are fractions** (0.05 is 5%). `equity` is a multiple
  of the starting capital, 1.0 at the start. Prices are in `data.instrument.currency`.
- **`max_drawdown` is negative** (-0.06 is 6% below the peak), as the app shows it; `stats.json`
  of the command keeps the engine's positive value.
- **Sharpe and Sortino** are yearly with `metrics.periods_per_year`: 252 for stocks, 365 for
  crypto, which trades every day. (The command always uses 252.)
- **Numbers are not rounded.** A value that is not finite is `null`, never NaN, so the document
  is always valid JSON.
- No field or value names a person, and the document holds no user ID, email or key.

### The light view

The page and the API get the same document with two tables made lighter. The download gets every
row and every trade.

- **Series** of more than 10,000 rows become one point per UTC day: the day's last `close`,
  `position`, `equity` and benchmark equity, and its lowest `drawdown`, at the `ts` of the day's
  last candle. These are the days Sharpe and Sortino are computed from, so the chart and the
  metrics agree, and the lowest point is `max_drawdown`. `prediction` and `signal` are left out.
  `series.resolution` is then `day` instead of `candle`.
- **Trades** above 10,000 keep the newest 10,000; `count` is how many are in the document,
  `total` how many the run had, and `complete` is false.

| Use | Route | Series | Trades |
| --- | --- | --- | --- |
| the page, the API | `GET /api/v1/backtests/{id}`, any status (`results` null until it finishes) | light | light |
| the download | `GET /api/v1/backtests/{id}/download`, an attachment; `409 backtest-not-finished` before it finishes | every row | every trade |

A 10-year run of 30-minute stock candles is about 4 MB as a download (1.4 MB compressed); a
10-year crypto run about 21 MB (7.6 MB compressed). The light view stays under 2 MB.

### The version

`version` is the format's version, now 1.

- A version only grows: a new field, a new column or a new value in an open list
  (`exit_reason`, `benchmarks[].kind`, the `decision` steps, `series.resolution`) keeps the
  version. Readers ignore fields they do not know and accept new values in those lists.
- Removing or renaming a field, or changing its type, unit or meaning, makes version 2.
- A new `ENGINE_VERSION` does not change it: the engine's version is data in the document.
- The JSON Schema generated from the models, [backtest.schema.json](backtest.schema.json), is
  kept current by a test, as the manifest's is. From `backend/`:
  `uv run python -m candlestack.experiments.json_schema > ../docs/backtest.schema.json`.

Not in version 1: benchmarks other than buy and hold (SPY, QQQ, DIA), returns per month, the
candles' OHLC (the fingerprint and `loaded` fetch them again), per-trade excursions, rolling
statistics, partial results of a failed run.

## Checking against the supervisor's backtest

`backend/tests/unit/engine/test_supervisor.py` runs the engine on the supervisor's candles and
predictions and checks that it makes his trades. His data is not public: never commit it. The
tests run only where `CANDLESTACK_SUPERVISOR_DATA` names a local copy; without it they are
skipped, as in CI.

The folder holds one folder per stock (the layout of issue #119), and must hold one for `AAPL`:

```text
<CANDLESTACK_SUPERVISOR_DATA>/
  AAPL/
    candles.parquet       unix (the candle's close), open, high, low, close
    predictions.parquet   unix, model, prediction
    trades.parquet        his trades: model, limit, stop_loss, open_date, close_date,
                          action_open, operation_end_type, trade_gain
  <another stock>/
    ...
```

From `backend/`, in Git Bash:

```sh
CANDLESTACK_SUPERVISOR_DATA=/path/to/data uv run pytest tests/unit/engine/test_supervisor.py -v
```

In PowerShell, set `$env:CANDLESTACK_SUPERVISOR_DATA` first, then run the same `uv run pytest`.

- Each stock, model, threshold and stop loss is a test of its own; a stock folder without all
  three files is skipped with the missing file named. Another test checks that every
  combination in his trades file is among the ones tested.
- Each test relabels his candles and predictions by 30 minutes, aligns them (`strict=False`),
  applies the threshold and runs `backtest` with `fill="signal_close"`, the stop loss and no
  costs.
- His file leaves out the trade still open at the end of the data, so our `end` trade is
  dropped. Then the trades must match one to one: open and close time, side and exit reason
  equal (his 0, 1, 2 are `signal`, `stop`, `gap`), and `1 + return` within 1e-4 of his
  `trade_gain`.

## No look-ahead

A backtest looks ahead when a decision or a fill uses a price from a later candle. The loop
cannot:

- It is one pass forward. On a candle it reads only that candle's open, high, low, close and
  signal, and what it carries from earlier candles: the capital, the position, its entry price
  and stop level, the peak of the equity, and a `next_open` order.
- A `next_open` order decided on candle t fills on candle t+1 at `open[t+1]`, before that
  candle's high, low and close are looked at; only then is the stop checked on that candle.
- `signal_close` decides and fills at the same close, which is right only if the signal of
  candle t was made from data up to that close. The engine cannot see how a signal was made: a
  signal built from later prices, or predictions with the wrong time labels, are look-ahead the
  engine cannot catch. That is what `relabel` and the strict `align` are for, and what the
  supervisor check confirms on his data.

The tests that prove it, in `backend/tests/unit/engine/test_properties.py` (random candles,
fixed seeds, both fills, with and without a stop, costs and a drawdown stop):

| Test | What it shows |
| --- | --- |
| `test_data_after_a_candle_changes_nothing_up_to_it` | replacing every candle after a row t with other random candles changes neither the `series` up to t, nor the trades closed by t, nor what was known of the trades opened by t |
| `test_next_open_opens_after_its_decision_at_the_open_of_the_fill_candle` | every `next_open` trade opens after its decision, on the next candle, at that candle's open |
| `test_the_fill_candle_apart_from_its_open_does_not_change_the_entry` | changing the high, low and close of the fill candle does not change the entries up to it |
| `test_without_stop_and_costs_next_open_is_signal_close_one_row_later` | without stop and costs, `next_open` makes the trades of `signal_close`, each one candle later at the open |
| `test_a_drawdown_stop_halts_at_the_first_equity_at_or_below_the_level_and_not_before` | with a drawdown stop, the run is the one without it up to the first candle whose equity is at or below the level; there a position still open leaves by `drawdown`, and every later candle is flat with the capital as its equity |

`test_rules.py` adds the order within a candle by hand:
`test_next_open_fills_the_order_before_it_checks_the_stop` and
`test_the_stop_comes_before_the_signal_of_the_same_candle`.

The post-processing in `signals` (`ema`, `hysteresis`, `confirm`) is proved the same way:
`test_values_after_a_row_change_nothing_up_to_it` in `backend/tests/unit/signals/test_filters.py`
replaces every value after a row with other random values and checks that the output up to
that row stays the same.

## ENGINE_VERSION

`ENGINE_VERSION` (in `candlestack/engine/version.py`, now `"0.3.0"`) names the rules that
produced a run: it is in every `Result` and in the command's `run.json`. It goes up with every
change to the loop that can change a run's trades or equity, so a stored run says which rules
made it.

`backend/tests/unit/engine/test_version.py` keeps the two together:

- It runs a fixed example of 50 half-hour candles, written out in the file, with both fills, a
  5% stop, a 5 bps fee and 2 bps of slippage, and both again with a 2% drawdown stop. Guard
  tests check that the example still has flips, a stop, a gap and a change on the last row,
  all with fees and slippage, with `next_open` an order that closes a position at an open
  beyond its stop (rule 2), a stop on an entry candle (rule 4) and an `end` exit, and with the
  drawdown stop a `drawdown` exit on a long before its stop, after which the run stays flat
  (rule 9).
- It writes the four runs' `trades` and `series` as JSON (column names and types, and the rows with
  each float as its shortest exact `repr`) and hashes it with SHA-256. Not the Parquet bytes:
  they depend on how Polars writes the file, which a Polars update may change while the output
  stays the same.
- `OUTPUT_SHA256` maps each version to its hash. When the output changes but the version does
  not, the test fails and says to bump the version; when the version has no hash yet, it fails
  and prints the hash to record.

To change the rules: change the loop, bump `ENGINE_VERSION`, add `"<new version>": "<hash>"` to
`OUTPUT_SHA256` (keep the old entries: they record what each version produced), and update the
rules, the worked example and `test_doc_example.py`. A refactor that keeps the output keeps the
hash, and the version stays. `test_determinism.py` checks that the same input gives the same
Parquet bytes, and `uv run pytest -m slow -s` times the engine by hand (CI skips it).

## Glossary

| Term | Meaning |
| --- | --- |
| backtest | running a trading rule over past candles to see what it would have earned |
| candle | the open, high, low and close price of one period (here labelled by its open time) |
| signal | the position the model asks for on a candle: +1 long, -1 short, 0 flat |
| decision | a change of the signal from one candle to the next; only a decision trades |
| fill | the execution of a decision at a price: the candle's close (`signal_close`) or the next open (`next_open`) |
| leg | one side of a trade, the entry or the exit; a flip has two legs on one candle |
| notional | the money value a leg trades: the capital at the entry, the position's value at the exit |
| stop-loss | a price level that closes a position once it has lost a set fraction of its entry price |
| gap | a candle that opens at or beyond the stop level, so the position exits at the open, worse than the level |
| ruin | a loss of the whole capital; the run stays flat at an equity of 0 afterwards |
| drawdown stop | a drawdown, measured on the equity recorded at the closes, at which the run halts: a position still open is closed and the run stays flat, keeping its capital |
| slippage | the difference between the price a fill is assumed at and the worse price it gets |
| bps | basis point, 0.01%: 10 bps is 0.1% |
| equity | the capital with any open position at its current value; it starts at 1.0 |
| drawdown | how far equity is below its highest level so far, as a fraction of that level |
| exposure | the share of candles that end with a position open |
| Sharpe ratio | the mean daily return divided by its standard deviation, scaled to a year |
| Sortino ratio | like Sharpe, but divided by the downside deviation: only losses count, averaged over all days |
| look-ahead | using information from a later candle in an earlier decision or fill, which makes a backtest look better than any live run |
