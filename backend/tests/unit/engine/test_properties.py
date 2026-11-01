"""Properties of the engine on random candles with fixed seeds: it never looks ahead, with and
without a stop, costs and a drawdown stop; without stop and costs ``next_open`` trades what
``signal_close`` trades, one row later; and a drawdown stop changes nothing before its halt."""

import math
import random
from typing import Any

import polars as pl
import pytest

from candlestack.engine import Result, Settings, backtest

INPUT_SCHEMA = {
    "ts": pl.Int64,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "signal": pl.Float64,
}
ROWS = 60
SEEDS = range(20)
DRAWDOWN_STOP = 0.05  # halts most of the seeds part way, some of them with a position open
COSTS = {"stop_loss": 0.02, "fee_bps": 5, "slippage_bps": 3}
SETTINGS = [
    Settings(fill="signal_close"),
    Settings(fill="signal_close", **COSTS),
    Settings(fill="signal_close", drawdown_stop=DRAWDOWN_STOP, **COSTS),
    Settings(fill="next_open"),
    Settings(fill="next_open", **COSTS),
    Settings(fill="next_open", drawdown_stop=DRAWDOWN_STOP, **COSTS),
]
IDS = [
    "signal_close",
    "signal_close-costs",
    "signal_close-drawdown",
    "next_open",
    "next_open-costs",
    "next_open-drawdown",
]
DRAWDOWN = [settings for settings in SETTINGS if settings.drawdown_stop is not None]


def random_candles(rng: random.Random) -> pl.DataFrame:
    """A random walk of 30-minute candles that gaps between them, with a signal that changes on
    about a third of the rows; big enough moves for 2% stops to fire, at the open too."""
    rows = []
    price, signal = 100.0, 0
    for row in range(ROWS):
        open_ = price * (1 + rng.gauss(0, 0.01))
        close = open_ * (1 + rng.gauss(0, 0.02))
        high = max(open_, close) * (1 + abs(rng.gauss(0, 0.01)))
        low = min(open_, close) * (1 - abs(rng.gauss(0, 0.01)))
        if rng.random() < 0.3:
            signal = rng.choice((-1, 0, 1))
        rows.append((row * 1800, open_, high, low, close, float(signal)))
        price = close
    return pl.DataFrame(rows, schema=INPUT_SCHEMA, orient="row")


def closed_by(result: Result, cutoff: int) -> list[dict[str, Any]]:
    return result.trades.filter(pl.col("close_ts") <= cutoff).rows(named=True)


def entries_by(result: Result, cutoff: int) -> list[tuple[Any, ...]]:
    """What is known of each trade opened up to ``cutoff`` when it opens."""
    opened = result.trades.filter(pl.col("open_ts") <= cutoff)
    return opened.select("signal_ts", "open_ts", "side", "open_price", "stop_price").rows()


def test_the_random_candles_reach_every_exit_reason() -> None:
    reasons = set()
    for seed in SEEDS:
        frame = random_candles(random.Random(seed))
        for settings in SETTINGS:
            reasons |= set(backtest(frame, settings).trades["exit_reason"])

    assert reasons == {"signal", "stop", "gap", "end", "drawdown"}


@pytest.mark.parametrize("settings", SETTINGS, ids=IDS)
@pytest.mark.parametrize("seed", SEEDS)
def test_data_after_a_candle_changes_nothing_up_to_it(seed: int, settings: Settings) -> None:
    rng = random.Random(seed)
    frame = random_candles(rng)
    full = backtest(frame, settings)

    for row in rng.sample(range(1, ROWS - 1), 5):
        changed = pl.concat([frame.head(row + 1), random_candles(rng).slice(row + 1)])
        result = backtest(changed, settings)

        cutoff = frame["ts"][row]
        assert result.series.head(row + 1).equals(full.series.head(row + 1))
        assert closed_by(result, cutoff) == closed_by(full, cutoff)
        assert entries_by(result, cutoff) == entries_by(full, cutoff)


@pytest.mark.parametrize("seed", SEEDS)
def test_next_open_opens_after_its_decision_at_the_open_of_the_fill_candle(seed: int) -> None:
    frame = random_candles(random.Random(seed))
    ts, opens, signals = frame["ts"].to_list(), frame["open"].to_list(), frame["signal"].to_list()

    result = backtest(frame, Settings(fill="next_open", stop_loss=0.02, fee_bps=5))

    assert not result.trades.is_empty()
    for trade in result.trades.iter_rows(named=True):
        row = ts.index(trade["open_ts"])
        assert trade["open_ts"] > trade["signal_ts"] == ts[row - 1]
        assert trade["open_price"] == opens[row]
        # The decision candle is a change of the signal to the trade's side.
        assert signals[row - 2] != signals[row - 1] == trade["side"]


@pytest.mark.parametrize("seed", SEEDS)
def test_the_fill_candle_apart_from_its_open_does_not_change_the_entry(seed: int) -> None:
    rng = random.Random(seed)
    frame = random_candles(rng)
    settings = Settings(fill="next_open", stop_loss=0.02, fee_bps=5, slippage_bps=3)
    full = backtest(frame, settings)
    ts = frame["ts"].to_list()

    for trade in full.trades.iter_rows(named=True):
        row = ts.index(trade["open_ts"])
        candles = frame.rows()
        open_, signal = candles[row][1], candles[row][5]
        move = rng.uniform(0.01, 0.3)
        close = open_ * (1 + rng.uniform(-move, move))
        candles[row] = (ts[row], open_, open_ * (1 + move), open_ * (1 - move), close, signal)
        changed = pl.DataFrame(candles, schema=INPUT_SCHEMA, orient="row")

        result = backtest(changed, settings)

        assert entries_by(result, ts[row]) == entries_by(full, ts[row])


def one_row_later(trades: pl.DataFrame, frame: pl.DataFrame) -> list[tuple[Any, ...]]:
    """``signal_close`` trades as ``next_open`` fills them: opened and closed at the open of the
    next row, except an exit on the last row, which ``next_open`` closes at the last close."""
    ts, opens, closes = frame["ts"].to_list(), frame["open"].to_list(), frame["close"].to_list()
    last = len(ts) - 1
    expected = []
    for trade in trades.iter_rows(named=True):
        entry = ts.index(trade["open_ts"]) + 1
        exit_ = ts.index(trade["close_ts"]) + 1
        if exit_ > last:
            exit_ts, exit_price, reason = ts[last], closes[last], "end"
        else:
            exit_ts, exit_price, reason = ts[exit_], opens[exit_], trade["exit_reason"]
        expected.append(
            (
                trade["signal_ts"],
                ts[entry],
                exit_ts,
                trade["side"],
                opens[entry],
                exit_price,
                reason,
            )
        )
    return expected


@pytest.mark.parametrize("seed", SEEDS)
def test_without_stop_and_costs_next_open_is_signal_close_one_row_later(seed: int) -> None:
    rng = random.Random(seed)
    frame = random_candles(rng)
    if seed % 2:  # and half of the runs change the signal on the last row
        signals = frame["signal"].to_list()
        signals[-1] = rng.choice([value for value in (-1.0, 0.0, 1.0) if value != signals[-2]])
        frame = frame.with_columns(signal=pl.Series(signals))

    signal_close = backtest(frame, Settings(fill="signal_close")).trades
    next_open = backtest(frame, Settings(fill="next_open")).trades

    assert not signal_close.is_empty()
    columns = ["signal_ts", "open_ts", "close_ts", "side", "open_price", "close_price"]
    assert next_open.select(*columns, "exit_reason").rows() == one_row_later(signal_close, frame)


@pytest.mark.parametrize("settings", SETTINGS, ids=IDS)
@pytest.mark.parametrize("seed", SEEDS)
def test_the_last_equity_compounds_the_trade_returns(seed: int, settings: Settings) -> None:
    result = backtest(random_candles(random.Random(seed)), settings)

    growth = math.prod(1 + value for value in result.trades["return"])
    assert result.series["equity"][-1] == pytest.approx(growth, rel=1e-12)
    assert result.series["position"][-1] == 0
    assert set(result.series["position"]) <= {-1.0, 0.0, 1.0}


@pytest.mark.parametrize("seed", SEEDS)
def test_fees_and_slippage_cost_add_up_to_the_cost_of_each_trade(seed: int) -> None:
    settings = Settings(fill="next_open", stop_loss=0.02, fee_bps=5, slippage_bps=3)
    s = settings.slippage_bps * 1e-4

    result = backtest(random_candles(random.Random(seed)), settings)

    assert not result.trades.is_empty()
    for trade in result.trades.iter_rows(named=True):
        side = trade["side"]
        # The prices before slippage: what the trade would have returned without costs.
        entry = trade["open_price"] / (1 + side * s)
        exit_ = trade["close_price"] / (1 - side * s)
        without_costs = side * (exit_ / entry - 1)
        assert trade["slippage_cost"] > 0
        assert trade["fees"] + trade["slippage_cost"] == pytest.approx(
            without_costs - trade["return"], rel=1e-9
        )


def halt_row(equity: list[float], drawdown_stop: float) -> int | None:
    """The first row whose equity is ``drawdown_stop`` or more below the peak so far, the
    starting 1.0 included; None when there is none."""
    peak = 1.0
    for row, value in enumerate(equity):
        peak = max(peak, value)
        if value <= peak * (1 - drawdown_stop):
            return row
    return None


@pytest.mark.parametrize("settings", SETTINGS[:2] + SETTINGS[3:5], ids=IDS[:2] + IDS[3:5])
@pytest.mark.parametrize("seed", SEEDS)
def test_a_position_size_scales_every_trade_and_changes_no_entry_or_exit(
    settings: Settings, seed: int
) -> None:
    frame = random_candles(random.Random(seed))
    whole = backtest(frame, settings)
    half = backtest(frame, settings.model_copy(update={"position_size": 0.5}))
    keys = ["signal_ts", "open_ts", "close_ts", "side", "open_price", "close_price", "stop_price"]

    assert half.trades.select(keys).equals(whole.trades.select(keys))
    assert half.series["position"].equals(whole.series["position"])
    for column in ("return", "fees", "slippage_cost"):
        assert half.trades[column].to_list() == [0.5 * value for value in whole.trades[column]]
    assert half.series["equity"][-1] == pytest.approx((1 + half.trades["return"]).product())


@pytest.mark.parametrize("settings", DRAWDOWN, ids=["signal_close", "next_open"])
@pytest.mark.parametrize("seed", SEEDS)
def test_a_drawdown_stop_halts_at_the_first_equity_at_or_below_the_level_and_not_before(
    seed: int, settings: Settings
) -> None:
    frame = random_candles(random.Random(seed))
    ts, closes = frame["ts"].to_list(), frame["close"].to_list()
    without = backtest(frame, settings.model_copy(update={"drawdown_stop": None}))
    halt = halt_row(without.series["equity"].to_list(), DRAWDOWN_STOP)

    result = backtest(frame, settings)

    if halt is None:
        assert result.trades.equals(without.trades)
        assert result.series.equals(without.series)
        return
    # Up to the halt nothing differs: the series before it, the trades closed before it and
    # what was known of the trades opened before it; on the halt row at most an open is left out.
    assert result.series.head(halt).equals(without.series.head(halt))
    assert closed_by(result, ts[halt - 1]) == closed_by(without, ts[halt - 1])
    assert entries_by(result, ts[halt - 1]) == entries_by(without, ts[halt - 1])
    assert set(entries_by(result, ts[halt])) <= set(entries_by(without, ts[halt]))
    # On the halt row, the position still open after its fills leaves at its close.
    still_open = len(entries_by(result, ts[halt])) - len(  # opened by then, not closed by then
        [trade for trade in closed_by(result, ts[halt]) if trade["exit_reason"] != "drawdown"]
    )
    drawdowns = result.trades.filter(pl.col("exit_reason") == "drawdown")
    assert drawdowns.height == still_open <= 1
    assert (result.trades["close_ts"] <= ts[halt]).all()
    if still_open:
        last = result.trades.row(-1, named=True)
        assert last["exit_reason"] == "drawdown"
        assert last["close_ts"] == ts[halt]
        slippage = settings.slippage_bps * 1e-4
        assert last["close_price"] == closes[halt] * (1 - last["side"] * slippage)
    # From the halt row on the run is flat and the equity is the capital, which stays.
    after = result.series.slice(halt)
    assert set(after["position"]) == {0.0}
    assert set(after["equity"]) == {result.series["equity"][halt]}
    assert result.series["equity"][halt] > 0
    assert halt_row(result.series["equity"].to_list(), DRAWDOWN_STOP) == halt
