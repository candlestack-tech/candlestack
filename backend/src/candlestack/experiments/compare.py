"""Comparing backtests (``GET /api/v1/compare``): the results of each with the best and the
worst of every statistic marked, the setups and what differs between them, and the equity
curves on one time axis, from what each backtest stored when it ran (``BacktestRuns.compared``,
one query for all of them): never the candles again, so comparing many spends none of a data
source's request budget. The decode of the curves and the points run in a thread.

The Compare page shows this answer as it is (``frontend/src/features/backtests/compare/``:
``rows.ts`` writes the values as the texts below, ``chartLines.ts`` draws the curves and reads a
line's value at a time as ``value_at`` does), so the app, the API and agents agree on which
backtest did best.
"""

from bisect import bisect_right
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from functools import partial
from typing import Any, Literal

import anyio
import numpy as np
from pydantic import BaseModel, Field

from candlestack.experiments.assembly import CURVES, read_curves
from candlestack.experiments.document import Decision, DocumentAuthor, Execution
from candlestack.experiments.errors import InvalidBacktest, NotComparable
from candlestack.experiments.runs import BacktestRuns
from candlestack.experiments.schemas import BacktestStatus, SetupBenchmark
from candlestack.experiments.store import Compared

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
# How the setups are shown: each whole, or what all share once and what differs with each.
SetupMode = Literal["full", "diff"]


class CompareMetric(BaseModel):
    """One statistic of every backtest, in the order of `backtests`."""

    key: str = Field(description="The statistic of `results.stats`", examples=["total_return"])
    label: str = Field(description="What the app calls it", examples=["Total return"])
    better: Better = Field(
        description="Which way is better: `higher`, `lower` or `none` (more trades, time in the "
        "market and buy and hold are not better in themselves, so none is marked). `lower` is "
        "the fees, and the max drawdown by the size of the fall: its values are 0 or below and "
        "a smaller fall is better, so -0.05 beats -0.20 and its best is the highest value"
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
        "`execution` and `benchmarks` (the instruments held) of its result document. With "
        "`setup_mode=diff` only its fields of `differs`, the rest once in `shared_setup`"
    )
    candles: int | None = Field(
        description="The candles of its period it ran on (`data.rows.count` of its document); "
        "null without results",
        examples=[12_480],
    )
    equity: list[float | None] | None = Field(
        description="Its cumulative return (a fraction of its starting capital) at each of "
        "`times`: its last value up to then, null outside its period; null without results "
        "and with `curve_points` 0. A long backtest's series has a point per day, from 0 at the "
        "open of its first candle, and its stored curve has at most 1,000 points. "
        "One that finished before its curves were stored is drawn from its short series of at "
        "most 60 points, with buy and hold only when its benchmark held its own instrument"
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
        "seconds (`curve_points`; none with 0): the first and the last time of each "
        "backtest's lines and times evenly spaced between the earliest and the latest"
    )
    shared_setup: dict[str, Any] | None = Field(
        default=None,
        description="With `setup_mode=diff`, the setup fields every backtest has alike, "
        "once; each backtest's `setup` then has only its fields of `differs`. Null otherwise",
    )


def parse_ids(ids: str) -> list[str]:
    """The backtest IDs of ``ids``, separated by commas.

    Raises:
        InvalidBacktest: More than 20, an empty one, or one twice.
    """
    parts = [part.strip() for part in ids.split(",")]
    return check_ids(parts, name="`ids`", empty=": separate the IDs by single commas")


def check_ids(ids: Sequence[str], *, name: str = "The list", empty: str = "") -> list[str]:
    """The backtest IDs to compare, checked: ``name`` is what a problem calls the list and
    ``empty`` what it adds about an empty ID.

    Raises:
        InvalidBacktest: None or more than 20, an empty one, or one twice.
    """
    if not COMPARE_MIN <= len(ids) <= COMPARE_MAX:
        raise InvalidBacktest(
            f"Compare {COMPARE_MIN} to {COMPARE_MAX} backtests: {name} has {len(ids)}."
        )
    if not all(ids):
        raise InvalidBacktest(f"{name} has an empty ID{empty}.")
    if len(set(ids)) < len(ids):
        raise InvalidBacktest(f"Each backtest can be compared once: {name} has one twice.")
    return list(ids)


async def compare(
    runs: BacktestRuns,
    user_id: str,
    ids: Sequence[str],
    *,
    curve_points: int = CURVE_POINTS,
    setup_mode: SetupMode = "full",
) -> CompareOut:
    """The comparison of the backtests ``ids``, each the user's own or another account's
    published one, from what each stored when it ran: curves on at most ``curve_points``
    times (none and no curves read with 0), the setups as ``setup_mode`` says.

    Raises:
        NotComparable: One the user cannot see, or test data without a document.
    """
    found = await runs.compared(user_id, ids, curves=curve_points > 0)
    rows = []
    for backtest_id in ids:
        row = found.get(backtest_id)
        if row is None:
            raise NotComparable(backtest_id, found=False)
        if row.setup is None:
            raise NotComparable(backtest_id, found=True)
        rows.append(row)
    build = partial(_compare, rows, user_id, points=curve_points, setup_mode=setup_mode)
    return await anyio.to_thread.run_sync(build)


def _compare(
    rows: Sequence[Compared], user_id: str, *, points: int, setup_mode: SetupMode
) -> CompareOut:
    """The comparison of what the backtests stored (CPU work, in a thread)."""
    documents = [_document(row, user_id, curves=points > 0) for row in rows]
    return compared(documents, points=points, setup_mode=setup_mode)


def _document(row: Compared, user_id: str, *, curves: bool) -> dict[str, Any]:
    """The parts of a backtest's light result document the comparison reads, from what it
    stored: its results only the statistics and, with ``curves``, its series' ``CURVES``
    columns (null until it finished). The decision and the execution read as its document has
    them, a field added since with its default."""
    backtest, setup = row.backtest, row.setup
    assert setup is not None  # ``compare`` refused test data
    finished = backtest.status == BacktestStatus.finished.value
    shown = None
    if finished and row.rows is not None and row.stats is not None:
        columns: dict[str, list[Any]] = {name: [] for name in CURVES}
        if curves:
            columns = (
                _short_curves(backtest.equity, backtest.setup, row.rows)
                if row.curves is None
                else read_curves(row.curves)
            )
        shown = {"stats": row.stats, "series": {"columns": columns}}
    author = DocumentAuthor(id=backtest.user_id, name=row.author, mine=backtest.user_id == user_id)
    return {
        "backtest": {
            "id": backtest.id,
            "number": backtest.number,
            "name": backtest.shown_name,
            "author": author.model_dump(),
            "public": backtest.published_at is not None,
            "status": backtest.status,
            "error": backtest.error,
        },
        "model": {
            "name": setup["model"],
            "manifest": {
                "input": {"representation": {"id": setup["reads"]}},
                "signature": {"input": {"shape": setup["input"]}},
            },
        },
        "data": {**setup["data"], "rows": row.rows if shown is not None else None},
        "decision": Decision.model_validate(setup["decision"]).model_dump(mode="json"),
        "execution": Execution.model_validate(setup["execution"]).model_dump(mode="json"),
        "benchmarks": setup["benchmarks"],
        "results": shown,
    }


def _short_curves(
    equity: Mapping[str, list[float]] | None, setup: Mapping[str, Any], rows: Mapping[str, int]
) -> dict[str, list[Any]]:
    """The curves of a backtest that finished before they were stored, from its short equity
    series (``Backtest.set_equity``): its points at the evenly spaced times they were taken at
    from the first row to the last (``rows``, its document's ``data.rows``), the fall of those
    points, and the close as buy and hold of its own instrument when that was its benchmark
    (else no close, so no line)."""
    if equity is None:
        return {name: [] for name in CURVES}
    values = np.asarray(equity["strategy"], dtype=np.float64) + 1.0
    ts = np.linspace(rows["first_ts"], rows["last_ts"], values.size).astype(np.int64)
    fall = values / np.maximum.accumulate(values) - 1.0
    close: list[float | None] = [None] * values.size
    if setup.get("benchmark") == SetupBenchmark.buy_and_hold.value:
        close = [value + 1.0 for value in equity["benchmark"]]
    return {
        "ts": ts.tolist(),
        "close": close,
        "equity": values.tolist(),
        "drawdown": fall.tolist(),
    }


def compared(
    documents: Sequence[dict[str, Any]],
    *,
    points: int = CURVE_POINTS,
    setup_mode: SetupMode = "full",
) -> CompareOut:
    """The comparison of these result documents, in their order: curves on at most ``points``
    times (none with 0), the setups as ``setup_mode`` says."""
    stats = [None if doc["results"] is None else doc["results"]["stats"] for doc in documents]
    setups = [setup_of(doc) for doc in documents]
    differs = differing(setups)
    shared = None
    if setup_mode == "diff":
        split = [split_setup(setup, differs) for setup in setups]
        shared = split[0][0] if split else {}
        setups = [own for _, own in split]
    drawn = points > 0
    lines = [_lines(doc) if drawn else ([], [], []) for doc in documents]
    times = time_axis([line for each in lines for line in each], points) if drawn else []
    backtests = []
    for doc, setup, (equity, fall, held) in zip(documents, setups, lines, strict=True):
        header = doc["backtest"]
        finished = doc["results"] is not None
        curved = finished and drawn
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
                equity=_curve(equity, times) if curved else None,
                drawdown=_curve(fall, times) if curved else None,
                buy_and_hold=_curve(held, times) if curved else None,
            )
        )
    return CompareOut(
        backtests=backtests,
        metrics=metric_rows(stats),
        differs=differs,
        times=times,
        shared_setup=shared,
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


def split_setup(
    setup: dict[str, Any], differs: Sequence[str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """A setup in two: its fields that are not in ``differs`` (dotted paths, ``differing``) and
    those that are, each nested as in the setup."""
    paths = set(differs)

    def split(value: dict[str, Any], prefix: str) -> tuple[dict[str, Any], dict[str, Any]]:
        shared: dict[str, Any] = {}
        own: dict[str, Any] = {}
        for key, inner in value.items():
            path = f"{prefix}{key}"
            if path in paths:
                own[key] = inner
            elif isinstance(inner, dict) and any(each.startswith(f"{path}.") for each in paths):
                alike, apart = split(inner, f"{path}.")
                if alike:
                    shared[key] = alike
                if apart:
                    own[key] = apart
            else:
                shared[key] = inner
        return shared, own

    return split(setup, "")


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
    equity = _points(ts, columns["equity"], 1.0)
    start = document["data"]["rows"]["first_ts"]
    return _from_start(equity, start), _from_start(fall, start), _from_start(held, start)


def _from_start(line: list[Point], start: int) -> list[Point]:
    """The line from 0 at ``start``, the open of the backtest's first candle, when its first
    point is later: a series of days has its first at the end of the first day, and before
    that candle the backtest had its starting capital and held nothing (#466)."""
    if line and line[0][0] > start:
        return [(start, 0.0), *line]
    return line


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
