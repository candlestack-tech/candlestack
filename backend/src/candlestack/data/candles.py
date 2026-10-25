"""Candle frames (``CANDLE_SCHEMA``): cleaning, validation, resampling, gaps, limits, fingerprint.

Two kinds of bins exist. Fixed bins (crypto, any 24/7 data) are aligned to UTC epoch multiples
of the timeframe. Session bins (stocks) are aligned to each session's open and the last one of a
session ends at the close; functions that take ``sessions`` (sorted, as ``parse_calendar``
returns them) use session bins, without it fixed bins.
"""

import bisect
import hashlib
import struct
from collections.abc import Sequence
from typing import NamedTuple

import polars as pl

from candlestack.data.errors import DataIntegrityError, format_time
from candlestack.data.models import CANDLE_SCHEMA, SESSION_LAST, InstrumentId, Timeframe
from candlestack.data.sessions import Session, session_bins, sessions_between

MAX_GAP_RANGES = 100

_COLUMNS = list(CANDLE_SCHEMA)
_PRICES = ["open", "high", "low", "close"]
_VALUES = [*_PRICES, "volume"]
_FINGERPRINT_VERSION = b"candlestack-candles-v1\n"


class Gaps(NamedTuple):
    """Missing candles: merged ``(start, end)`` ranges (at most ``MAX_GAP_RANGES``, earliest
    first) and the total number of missing candles."""

    ranges: list[tuple[int, int]]
    total: int


def normalise(df: pl.DataFrame) -> pl.DataFrame:
    """Casts to ``CANDLE_SCHEMA`` (other columns are dropped), sorts by ``ts`` and keeps the
    last delivered row of each ``ts``. Raises ``DataIntegrityError`` when a column is missing or
    a value cannot be cast."""
    try:
        frame = df.select(pl.col(name).cast(dtype) for name, dtype in CANDLE_SCHEMA.items())
    except pl.exceptions.PolarsError as error:
        reason = str(error).splitlines()[0]
        raise DataIntegrityError(f"Candle data cannot be read: {reason}") from error
    return frame.unique(subset="ts", keep="last", maintain_order=True).sort("ts")


_RULES = [
    (
        "a missing or non-finite value",
        pl.any_horizontal(
            pl.col("ts").is_null(),
            *(pl.col(c).is_finite().not_().fill_null(value=True) for c in _VALUES),
        ),
    ),
    ("a price at or below zero", pl.any_horizontal(pl.col(_PRICES) <= 0)),
    ("low above min(open, close)", pl.col("low") > pl.min_horizontal("open", "close")),
    ("high below max(open, close)", pl.col("high") < pl.max_horizontal("open", "close")),
    ("negative volume", pl.col("volume") < 0),
    ("a time not after the previous candle", pl.col("ts").diff() <= 0),
]


def validate(df: pl.DataFrame, timeframe: Timeframe | None = None) -> None:
    """Checks a normalised frame; raises ``DataIntegrityError`` naming every broken rule, how
    many candles break it and the first one. With ``timeframe`` (fixed bins) every candle must
    also open on that timeframe's UTC grid."""
    rules = list(_RULES)
    if timeframe is not None:
        rules.append(
            (f"an open time off the {timeframe} UTC grid", pl.col("ts") % timeframe.seconds != 0)
        )
    flags = df.select("ts", *(rule.alias(f"rule{i}") for i, (_, rule) in enumerate(rules)))
    problems = []
    for i, (what, _) in enumerate(rules):
        bad = flags.filter(pl.col(f"rule{i}"))["ts"]
        if bad.len():
            has = "candle has" if bad.len() == 1 else "candles have"
            first = bad.drop_nulls()
            where = f", first at {format_time(first[0])}" if first.len() else ""
            problems.append(f"{bad.len()} {has} {what}{where}")
    if problems:
        raise DataIntegrityError(f"Invalid candle data: {'; '.join(problems)}.")


def _aggregate(frame: pl.DataFrame) -> pl.DataFrame:
    """OHLCV per ``ts`` of a frame sorted by time whose ``ts`` is already the bin open."""
    return frame.group_by("ts", maintain_order=True).agg(
        pl.col("open").first(),
        pl.col("high").max(),
        pl.col("low").min(),
        pl.col("close").last(),
        pl.col("volume").sum(),
    )


def resample_fixed(df: pl.DataFrame, timeframe: Timeframe) -> pl.DataFrame:
    """Candles of a longer timeframe in fixed UTC bins; only bins that have data."""
    step = timeframe.seconds
    return _aggregate(df.sort("ts").with_columns(pl.col("ts") // step * step))


def _sessions_frame(sessions: Sequence[Session]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "session_open": [session.open for session in sessions],
            "session_close": [session.close for session in sessions],
        },
        schema={"session_open": pl.Int64, "session_close": pl.Int64},
    ).sort("session_open")


def _with_session(df: pl.DataFrame, sessions: Sequence[Session]) -> pl.DataFrame:
    """Adds ``session_open`` and ``session_close`` of the latest session that opened at or before
    each candle (null before the first one)."""
    return df.sort("ts").join_asof(
        _sessions_frame(sessions), left_on="ts", right_on="session_open", strategy="backward"
    )


def resample_sessions(
    df: pl.DataFrame, timeframe: Timeframe, sessions: Sequence[Session]
) -> pl.DataFrame:
    """Candles in session bins, built from shorter candles (1m); candles outside every session
    are dropped. See ``session_bins`` for the bins."""
    step = timeframe.seconds
    offset = pl.col("ts") - pl.col("session_open")
    return _aggregate(
        _with_session(df, sessions)
        .filter(pl.col("ts") < pl.col("session_close"))
        .with_columns(ts=pl.col("session_open") + offset // step * step)
        .select(_COLUMNS)
    )


def session_last(df: pl.DataFrame, timeframe: Timeframe, sessions: Sequence[Session]) -> pl.Series:
    """For each candle of a frame sorted by ``ts``, in its order, whether it is the last of its
    session: its bin ends at the session close (15:30-16:00 for 30m on a normal day, 12:30-13:00
    on an early close; every 1d candle). It comes from the calendar alone, so it is known when
    the candle closes; a session whose last candle is missing has no last candle. A candle
    outside every session is not the last of one.
    """
    end = pl.col("ts") + timeframe.seconds
    return (
        _with_session(df.select("ts"), sessions)
        .select(
            ((pl.col("ts") < pl.col("session_close")) & (end >= pl.col("session_close")))
            .fill_null(value=False)
            .alias(SESSION_LAST)
        )
        .to_series()
    )


def expected_bins_fixed(
    start: int, end: int, timeframe: Timeframe, *, now: int | None = None
) -> list[int]:
    """Open times of the fixed bins in ``[start, end)``; with ``now`` only bins closed by then."""
    step = timeframe.seconds
    if now is not None:
        end = min(end, now - step + 1)
    return list(range(_ceil_div(start, step) * step, end, step))


def expected_bins_sessions(
    start: int,
    end: int,
    timeframe: Timeframe,
    sessions: Sequence[Session],
    *,
    now: int | None = None,
) -> list[int]:
    """Open times of the session bins in ``[start, end)``; with ``now`` only bins closed by
    then."""
    return [
        opens
        for session in sessions_between(sessions, start, end)
        for opens, closes in session_bins(session, timeframe)
        if start <= opens < end and (now is None or closes <= now)
    ]


def find_gaps(
    ts: pl.Series | Sequence[int],
    expected: Sequence[int],
    timeframe: Timeframe,
    sessions: Sequence[Session] | None = None,
) -> Gaps:
    """Expected bins without a candle. Neighbouring missing bins (neighbours in ``expected``, so
    for stocks also across a night or weekend) merge into one ``[start, end)`` range; a range
    ends where its last missing bin ends."""
    present = pl.Series(ts, dtype=pl.Int64)
    missing = (
        pl.DataFrame({"t": expected}, schema={"t": pl.Int64})
        .with_row_index("i")
        .filter(pl.col("t").is_in(present.implode()).not_())
    )
    runs = (
        missing.group_by(
            (pl.col("i").diff() != 1).fill_null(value=True).cum_sum().alias("run"),
            maintain_order=True,
        )
        .agg(pl.col("t").first().alias("first"), pl.col("t").last().alias("last"))
        .head(MAX_GAP_RANGES)
    )
    ranges = [
        (first, _bin_end(last, timeframe, sessions))
        for first, last in zip(runs["first"], runs["last"], strict=True)
    ]
    return Gaps(ranges, missing.height)


def _bin_end(opens: int, timeframe: Timeframe, sessions: Sequence[Session] | None) -> int:
    end = opens + timeframe.seconds
    if sessions:
        i = bisect.bisect_right(sessions, opens, key=lambda session: session.open) - 1
        if i >= 0 and opens < sessions[i].close:
            end = min(end, sessions[i].close)
    return end


def closed_only(
    df: pl.DataFrame,
    timeframe: Timeframe,
    now: int,
    sessions: Sequence[Session] | None = None,
) -> pl.DataFrame:
    """Drops candles that have not closed by ``now``: a candle ends ``timeframe`` after its
    open, a session candle at the latest at the session close."""
    end = pl.col("ts") + timeframe.seconds
    if sessions is None:
        return df.filter(end <= now)
    return (
        _with_session(df, sessions)
        .filter(pl.min_horizontal(end, pl.col("session_close")) <= now)
        .select(_COLUMNS)
    )


def slice_range(df: pl.DataFrame, start: int, end: int) -> pl.DataFrame:
    """Candles with ``start <= ts < end``."""
    return df.filter((pl.col("ts") >= start) & (pl.col("ts") < end))


# The fields of the byte layout below, in its order.
def fingerprint(  # noqa: PLR0913, PLR0917
    source: str,
    feed: str,
    instrument: InstrumentId | str,
    timeframe: Timeframe | str,
    start: int,
    end: int,
    df: pl.DataFrame,
) -> str:
    r"""``sha256:<64 hex>`` over the request and its candles; the same candles give the same
    value whatever the row order, any changed number gives another.

    Byte layout (version 1) fed to SHA-256:

    1. ``candlestack-candles-v1`` and a line feed (LF, 0x0A).
    2. source, feed, instrument id, timeframe, start, end and the number of candles n, each as
       UTF-8 text followed by LF; integers in decimal, e.g.
       ``binance\nspot\ncrypto:BTCUSDT\n1h\n1704067200\n1704078000\n3\n``.
    3. the n open times ``ts`` in ascending order, each a big-endian signed 64-bit integer.
    4. the n opens, then the n highs, n lows, n closes and n volumes, each column in the order
       of ``ts`` and each value a big-endian IEEE 754 binary64 (``-0.0`` is written as ``0.0``).
    """
    frame = df.sort("ts").select(
        "ts",
        *(pl.when(pl.col(c) == 0).then(0.0).otherwise(pl.col(c)).alias(c) for c in _VALUES),
    )
    n = frame.height
    fields = [source, feed, str(instrument), str(timeframe), str(start), str(end), str(n)]
    digest = hashlib.sha256(_FINGERPRINT_VERSION)
    digest.update("".join(f"{field}\n" for field in fields).encode())
    digest.update(struct.pack(f">{n}q", *frame["ts"].to_list()))
    for column in _VALUES:
        digest.update(struct.pack(f">{n}d", *frame[column].to_list()))
    return f"sha256:{digest.hexdigest()}"


def _ceil_div(a: int, b: int) -> int:
    return -(-a // b)


def _session_count(session: Session, step: int, start: int, end: int) -> int:
    """Number of the session's bins that open in ``[start, end)``."""
    total = _ceil_div(session.close - session.open, step)
    first = max(0, _ceil_div(start - session.open, step))
    last = min(total, _ceil_div(end - session.open, step))
    return max(0, last - first)


def candle_count(
    start: int, end: int, timeframe: Timeframe, sessions: Sequence[Session] | None = None
) -> int:
    """Number of bins that open in ``[start, end)``: the most candles the period can have."""
    step = timeframe.seconds
    if end <= start:
        return 0
    if sessions is None:
        return _ceil_div(end, step) - _ceil_div(start, step)
    return sum(
        _session_count(session, step, start, end)
        for session in sessions_between(sessions, start, end)
    )


def first_of_last(
    end: int,
    now: int,
    timeframe: Timeframe,
    count: int,
    sessions: Sequence[Session] | None = None,
    *,
    lower: int | None = None,
) -> int | None:
    """Open time of the earliest of the last ``count`` bins that open before ``end`` and are
    closed by ``now``; for stocks only session bins count, so nights, weekends and holidays are
    skipped. Only bins that open at or after ``lower`` count (the first available candle), so the
    walk back stops there: with fewer bins than ``count``, the earliest one there is; ``None``
    when there is none."""
    step = timeframe.seconds
    if sessions is None:
        newest = min(end - 1, now - step) // step * step
        if newest < (lower or 0):
            return None
        return max(lower or 0, newest - (count - 1) * step)
    earliest, remaining = None, count
    for i in range(bisect.bisect_left(sessions, end, key=lambda session: session.open) - 1, -1, -1):
        if lower is not None and sessions[i].close <= lower:
            break
        opens = [
            opened
            for opened, closed in session_bins(sessions[i], timeframe)
            if (lower is None or opened >= lower) and opened < end and closed <= now
        ]
        if len(opens) >= remaining:
            return opens[-remaining]
        if opens:
            earliest, remaining = opens[0], remaining - len(opens)
    return earliest


def latest_end(
    start: int, end: int, timeframe: Timeframe, maximum: int, sessions: Sequence[Session] | None
) -> int:
    """The latest end, up to ``end``, for which ``[start, end)`` has at most ``maximum`` bins:
    the open of bin number ``maximum + 1``."""
    step = timeframe.seconds
    if sessions is None:
        return min(end, (_ceil_div(start, step) + maximum) * step)
    remaining = maximum
    for session in sessions_between(sessions, start, end):
        count = _session_count(session, step, start, end)
        if remaining < count:
            first = max(0, _ceil_div(start - session.open, step))
            return session.open + (first + remaining) * step
        remaining -= count
    return end


def suggest(
    start: int,
    end: int,
    timeframe: Timeframe,
    maximum: int,
    sessions: Sequence[Session] | None = None,
) -> str:
    """What to change when ``[start, end)`` has more than ``maximum`` candles: the smallest
    longer timeframe that fits, and the latest end that fits with the requested timeframe.

    ``Use 5m or a larger timeframe, or end the period at 2024-02-04T17:20:00Z (1707067200) or
    earlier.`` Raises ``ValueError`` when the period fits.
    """
    if candle_count(start, end, timeframe, sessions) <= maximum:
        raise ValueError(f"{maximum} candles are enough: the period fits")
    latest = format_time(latest_end(start, end, timeframe, maximum, sessions))
    fitting = next(
        (
            longer
            for longer in timeframe.larger()
            if candle_count(start, end, longer, sessions) <= maximum
        ),
        None,
    )
    if fitting is None:
        return f"End the period at {latest} or earlier."
    or_larger = " or a larger timeframe" if fitting.larger() else ""
    return f"Use {fitting}{or_larger}, or end the period at {latest} or earlier."
