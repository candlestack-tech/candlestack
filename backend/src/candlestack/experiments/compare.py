"""Comparing backtests (``GET /api/v1/compare``): the results of each with the best and the
worst of every statistic marked, the setups and what differs between them, and the equity
curves on one time axis, from what each backtest stored when it ran (``BacktestRuns.stored``):
never the candles again, so comparing many spends none of a data source's request budget.

The Compare page shows this answer as it is (``frontend/src/features/backtests/compare/``:
``rows.ts`` writes the values as the texts below, ``chartLines.ts`` draws the curves and reads a
line's value at a time as ``value_at`` does), so the app, the API and agents agree on which
backtest did best.
"""

from bisect import bisect_right
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from candlestack.experiments.document import DocumentAuthor
from candlestack.experiments.errors import (
    BacktestNotFound,
    InvalidBacktest,
    NoDocument,
    NotComparable,
)
from candlestack.experiments.runs import BacktestRuns
from candlestack.experiments.schemas import BacktestStatus

# One is listed as it is, nothing marked: the Compare page shows a single backtest that way.
COMPARE_MIN = 1
COMPARE_MAX = 20
# The longest ``ids``: 20 IDs of ``bt_`` and 10 characters, commas and spaces, with room.
IDS_MAX_LENGTH = COMPARE_MAX * 20
# The most times of the curves: enough for a chart, small enough for an agent to read.
CURVE_POINTS = 200
# A curve's values in fractions to 0.1 %: what the page's charts show of a line.
_CURVE_DECIMALS = 3

Mark = Literal["best", "worst"]
Better = Literal["higher", "lower", "none"]


class CompareMetric(BaseModel):
    """One statistic of every backtest, in the order of `backtests`."""

    key: str = Field(description="The statistic of `results.stats`", examples=["total_return"])
    label: str = Field(description="What the app calls it", examples=["Total return"])
    better: Better = Field(
        description="Which way is better: `higher`, `lower` (a fall by its size, whatever its "
        "sign; the fees) or `none` (more trades, time in the market and buy and hold are not "
        "better in themselves, so none is marked)"
    )
    values: list[float | None] = Field(
        description="The value of each backtest, as in `results.stats`; null without results "
        "(running, failed) or when undefined",
        examples=[[0.246, -0.031]],
    )
    marks: list[Mark | None] = Field(
        description="`best`, `worst` or null for each backtest. Values that read the same in the "
        "app count as equal (one decimal of a percent, two of the average trade and of a "
        "ratio), so two that look alike are never told apart; nothing is marked with fewer "
        "than two values or when all are equal",
        examples=[["best", "worst"]],
    )


class ComparedBacktest(BaseModel):
    id: str = Field(examples=["bt_7f3k2m9qx4"])
    number: int = Field(description="Its author's own number, shown as #N", examples=[12])
    name: str = Field(examples=["Silent Orion"])
    author: DocumentAuthor
    public: bool = Field(description="Published in Experiments")
    status: BacktestStatus = Field(
        description="A running or failed backtest has no values, marks or curves"
    )
    error: str | None = Field(description="What went wrong, for a failed one")
    setup: dict[str, Any] = Field(
        description="What it ran on and with: `instrument`, `model` (its file name), `reads` "
        "(the representation of its manifest), `input` (the model's input, rows by features, "
        "from its manifest's signature), `timeframe`, `period`, and the `decision`, "
        "`execution` and `benchmarks` (the instruments held) of its result document"
    )
    candles: int | None = Field(
        description="The candles of its period it ran on (`data.rows.count` of its document); "
        "null without results",
        examples=[12_480],
    )
    equity: list[float | None] | None = Field(
        description="Its cumulative return (a fraction of its starting capital) at each of "
        "`times`: its last value up to then, null outside its period; null without results. "
        "A long backtest's series has a point per day, so its line begins at the end of its "
        "first day. One that finished before its curves were stored is drawn from its short "
        "series of at most 60 points, with buy and hold only when its benchmark held its own "
        "instrument"
    )
    drawdown: list[float | None] | None = Field(
        description="Its fall from its highest equity (a fraction, 0 or below), at the same "
        "times; per day, the day's lowest"
    )
    buy_and_hold: list[float | None] | None = Field(
        description="The cumulative return of holding its instrument over its period, at the "
        "same times: its last value is the `buy_and_hold_return` statistic"
    )


class CompareOut(BaseModel):
    """Backtests side by side, in the order asked for."""

    backtests: list[ComparedBacktest]
    metrics: list[CompareMetric] = Field(
        description="The 12 statistics of the result document, in the app's order"
    )
    differs: list[str] = Field(
        description="The setup fields that differ between the backtests, as dotted paths into "
        "`setup` (a field only some have counts as differing)",
        examples=[["period.start", "decision.rule.limit"]],
    )
    times: list[int] = Field(
        description=f"The common time axis of the curves, at most {CURVE_POINTS} UTC epoch "
        "seconds: the first and the last time of each backtest's series and times evenly "
        "spaced between the earliest and the latest"
    )


def parse_ids(ids: str) -> list[str]:
    """The backtest IDs of ``ids``, separated by commas.

    Raises:
        InvalidBacktest: More than 20, an empty one, or one twice.
    """
    parsed = [part.strip() for part in ids.split(",")]
    if not COMPARE_MIN <= len(parsed) <= COMPARE_MAX:
        raise InvalidBacktest(
            f"Compare {COMPARE_MIN} to {COMPARE_MAX} backtests: `ids` has {len(parsed)}."
        )
    if not all(parsed):
        raise InvalidBacktest("`ids` has an empty ID: separate the IDs by single commas.")
    if len(set(parsed)) < len(parsed):
        raise InvalidBacktest("Each backtest can be compared once: `ids` has one twice.")
    return parsed


async def compare(runs: BacktestRuns, user_id: str, ids: Sequence[str]) -> CompareOut:
    """The comparison of the backtests ``ids``, each the user's own or another account's
    published one, from what each stored of its light result document.

    Raises:
        NotComparable: One the user cannot see, or test data without a document.
    """
    documents = []
    for backtest_id in ids:
        try:
            documents.append(await runs.stored(user_id, backtest_id))
        except BacktestNotFound:
            raise NotComparable(backtest_id, found=False) from None
        except NoDocument:
            raise NotComparable(backtest_id, found=True) from None
    return compared(documents)


def compared(documents: Sequence[dict[str, Any]]) -> CompareOut:
    """The comparison of these result documents, in their order."""
    stats = [None if doc["results"] is None else doc["results"]["stats"] for doc in documents]
    setups = [setup_of(doc) for doc in documents]
    lines = [_lines(doc) for doc in documents]
    times = time_axis([line for each in lines for line in each])
    backtests = []
    for doc, setup, (equity, fall, held) in zip(documents, setups, lines, strict=True):
        header = doc["backtest"]
        finished = doc["results"] is not None
        backtests.append(
            ComparedBacktest(
                id=header["id"],
                number=header["number"],
                name=header["name"],
                author=DocumentAuthor.model_validate(header["author"]),
                public=header["public"],
                status=BacktestStatus(header["status"]),
                error=header["error"],
                setup=setup,
                candles=(doc["data"].get("rows") or {}).get("count") if finished else None,
                equity=_curve(equity, times) if finished else None,
                drawdown=_curve(fall, times) if finished else None,
                buy_and_hold=_curve(held, times) if finished else None,
            )
        )
    return CompareOut(
        backtests=backtests, metrics=metric_rows(stats), differs=differing(setups), times=times
    )


# --- The statistics, the best and the worst marked (rows.ts) ----------------------------------


# The texts below are the page's with a hyphen for its minus sign: only whether two read the
# same counts.


def _shown(value: float, decimals: int) -> str:
    """The digits of ``Intl.NumberFormat('en-US')`` with exactly ``decimals`` decimals: half
    away from zero, of the shortest decimal that reads back as ``value``, as ICU rounds."""
    step = Decimal(1).scaleb(-decimals)
    digits = Decimal(repr(abs(value))).quantize(step, ROUND_HALF_UP)
    return f"{'-' if value < 0 else ''}{digits:,f}"


def _signed(value: float, decimals: int) -> tuple[str, bool]:
    """The digits without a sign and whether the rounded value is below zero."""
    text = _shown(abs(value), decimals)
    return text, value < 0 and any(digit in "123456789" for digit in text)


def _percent(value: float) -> str:
    text, negative = _signed(value * 100, 1)
    return f"{'-' if negative else '+'}{text} %"


def _ratio(value: float) -> str:
    text, negative = _signed(value, 2)
    return f"-{text}" if negative else text


def _share(value: float) -> str:
    return f"{_shown(value * 100, 1)} %"


def _count(value: float) -> str:
    return _shown(value, 0)


def _fall(value: float) -> str:
    # No fall is no sign.
    return "0.0 %" if value == 0 else _percent(value)


def _small_percent(value: float) -> str:
    # ``toFixed(2)``: of the exact value, a tie to the larger digits.
    sign = "+" if value > 0 else "-" if value < 0 else ""
    digits = Decimal(abs(value * 100)).quantize(Decimal("0.01"), ROUND_HALF_UP)
    return f"{sign}{digits} %"


@dataclass(frozen=True)
class _Metric:
    label: str
    key: str
    better: Literal[1, -1, 0]
    write: Callable[[float], str]


_METRICS = (
    _Metric("Total return", "total_return", 1, _percent),
    _Metric("Max drawdown", "max_drawdown", -1, _fall),
    _Metric("Sharpe", "sharpe", 1, _ratio),
    _Metric("Sortino", "sortino", 1, _ratio),
    _Metric("Win rate", "win_rate", 1, _share),
    _Metric("Avg. trade", "avg_trade_return", 1, _small_percent),
    _Metric("Best trade", "best_trade_return", 1, _percent),
    _Metric("Worst trade", "worst_trade_return", 1, _percent),
    _Metric("Fees paid", "fees_paid", -1, _share),
    _Metric("Trades", "num_trades", 0, _count),
    _Metric("Time in market", "exposure", 0, _share),
    _Metric("Buy and hold", "buy_and_hold_return", 0, _percent),
)
_BETTER: dict[int, Better] = {1: "higher", -1: "lower", 0: "none"}


def _extremes(values: list[tuple[str, float]], better: Literal[1, -1]) -> tuple[str, str] | None:
    """The texts of the best and the worst value: values that read the same count as equal, so
    a row never marks two figures that look alike. None with fewer than two values, or when all
    are equal. The sort is stable, as the page's."""
    if len(values) < 2:
        return None
    ordered = sorted(values, key=lambda value: value[1], reverse=better == 1)
    best, worst = ordered[0][0], ordered[-1][0]
    return None if best == worst else (best, worst)


def _metric_row(metric: _Metric, stats: Sequence[dict[str, Any] | None]) -> CompareMetric:
    raw: list[float | None] = [None if each is None else each.get(metric.key) for each in stats]
    texts = [None if value is None else metric.write(value) for value in raw]
    # A fall is the same however it is signed: the smaller, the better.
    size = abs if metric.key == "max_drawdown" else float
    known = [
        (text, size(value))
        for value, text in zip(raw, texts, strict=True)
        if value is not None and text is not None
    ]
    extremes = None if metric.better == 0 else _extremes(known, metric.better)
    marks: list[Mark | None] = []
    for text in texts:
        if text is None or extremes is None:
            marks.append(None)
        else:
            marks.append(
                "best" if text == extremes[0] else "worst" if text == extremes[1] else None
            )
    return CompareMetric(
        key=metric.key,
        label=metric.label,
        better=_BETTER[metric.better],
        values=raw,
        marks=marks,
    )


def metric_rows(stats: Sequence[dict[str, Any] | None]) -> list[CompareMetric]:
    """Each statistic of the backtests (``results.stats``, None without results), with the best
    and the worst marked."""
    return [_metric_row(metric, stats) for metric in _METRICS]


# The statistics a ranking can use: those with a better direction.
RANKABLE = tuple(metric.key for metric in _METRICS if metric.better != 0)


def ranks(stats: Sequence[dict[str, Any] | None], key: str) -> list[int | None]:
    """The rank of each backtest by the statistic ``key`` (one of ``RANKABLE``), 1 the best, in
    the order of ``stats``: values that read the same in the app share a rank, as the marks
    count them equal, and the next rank skips past them (1, 1, 3). None without a value."""
    metric = next(metric for metric in _METRICS if metric.key == key)
    size = abs if key == "max_drawdown" else float
    known = [
        (index, metric.write(value), size(value))
        for index, value in enumerate(None if each is None else each.get(key) for each in stats)
        if value is not None
    ]
    known.sort(key=lambda item: item[2], reverse=metric.better == 1)
    ranked: list[int | None] = [None] * len(stats)
    rank, last = 0, None
    for place, (index, text, _) in enumerate(known, start=1):
        if text != last:
            rank, last = place, text
        ranked[index] = rank
    return ranked


# --- The setups -------------------------------------------------------------------------------


def setup_of(document: dict[str, Any]) -> dict[str, Any]:
    """What a backtest ran on and with, from its result document."""
    data, model = document["data"], document["model"]
    manifest = model["manifest"]
    representation = manifest.get("input", {}).get("representation", {}).get("id")
    shape = manifest.get("signature", {}).get("input", {}).get("shape")
    return {
        "instrument": data["instrument"]["id"],
        "model": model["name"],
        "reads": representation,
        "input": shape,
        "timeframe": data["timeframe"],
        "period": data["period"],
        "decision": document["decision"],
        "execution": document["execution"],
        "benchmarks": [benchmark["instrument"] for benchmark in document["benchmarks"]],
    }


def _flat(value: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, inner in value.items():
        path = f"{prefix}{key}"
        if isinstance(inner, dict):
            flat.update(_flat(inner, f"{path}."))
        else:
            flat[path] = inner
    return flat


def differing(setups: Sequence[dict[str, Any]]) -> list[str]:
    """The dotted paths of the fields whose values differ between ``setups``, a field only some
    have included, in the order they first come up."""
    flat = [_flat(setup) for setup in setups]
    paths = list(dict.fromkeys(path for each in flat for path in each))
    missing = object()
    return [path for path in paths if len({repr(each.get(path, missing)) for each in flat}) > 1]


# --- The curves (chartLines.ts) ---------------------------------------------------------------

Point = tuple[int, float]


def _points(ts: Sequence[int], values: Sequence[float | None], base: float) -> list[Point]:
    """The cumulative return of each row over ``base``, rows without a value left out."""
    return [
        (time, value / base - 1.0)
        for time, value in zip(ts, values, strict=True)
        if value is not None
    ]


def _lines(document: dict[str, Any]) -> tuple[list[Point], list[Point], list[Point]]:
    """The equity of the backtest as a cumulative return, its drawdown and the cumulative return
    of holding its instrument, at the times of its series; none without results."""
    results = document["results"]
    if results is None:
        return [], [], []
    columns = results["series"]["columns"]
    ts, close = columns["ts"], columns["close"]
    # A series of more than 10,000 rows has a point per day, the day's last close, so its first
    # close is not the price held from: the line ends at the statistic, as the page reads it.
    held_return = results["stats"].get("buy_and_hold_return")
    last = next((value for value in reversed(close) if value), None)
    first = next((value for value in close if value), None)
    if held_return is not None and held_return > -1 and last is not None:
        held = _points(ts, close, last / (1.0 + held_return))
    else:
        held = [] if first is None else _points(ts, close, first)
    # A fall however it is stored, as the page draws it.
    fall = [
        (time, -abs(value))
        for time, value in zip(ts, columns["drawdown"], strict=True)
        if value is not None
    ]
    return _points(ts, columns["equity"], 1.0), fall, held


def value_at(points: Sequence[Point], time: int) -> float | None:
    """The value of a line at a time: its last point up to then, none outside its period."""
    return _value_at(points, [at for at, _ in points], time)


def _value_at(points: Sequence[Point], times: Sequence[int], time: int) -> float | None:
    after = bisect_right(times, time)
    if after == 0 or not points:
        return None
    if after < len(points):
        return points[after - 1][1]
    last_time, last_value = points[-1]
    return last_value if last_time == time else None


def time_axis(lines: Sequence[Sequence[Point]], points: int = CURVE_POINTS) -> list[int]:
    """The common times of ``lines``: the first and the last of each, so every line starts at
    its start and ends at its result, and times evenly spaced from the earliest to the latest
    up to ``points`` in all."""
    spans = [(line[0][0], line[-1][0]) for line in lines if line]
    if not spans:
        return []
    edges = {time for span in spans for time in span}
    first, last = min(start for start, _ in spans), max(end for _, end in spans)
    # The earliest and the latest are edges and the grid's ends.
    free = max(points - len(edges) + 2, 2)
    grid = {first + round(index * (last - first) / (free - 1)) for index in range(free)}
    return sorted(edges | grid)[:points]


def _curve(line: Sequence[Point], times: Sequence[int]) -> list[float | None]:
    own = [at for at, _ in line]
    values = (_value_at(line, own, time) for time in times)
    return [None if value is None else round(value, _CURVE_DECIMALS) for value in values]
