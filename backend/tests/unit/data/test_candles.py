import hashlib
import random
import re
import struct
from collections.abc import Sequence
from datetime import UTC, date, datetime

import polars as pl
import pytest

from candlestack.data import (
    CANDLE_SCHEMA,
    DataIntegrityError,
    Gaps,
    Session,
    Timeframe,
    candle_count,
    closed_only,
    expected_bins_fixed,
    expected_bins_sessions,
    find_gaps,
    fingerprint,
    first_of_last,
    normalise,
    resample_fixed,
    resample_sessions,
    session_last,
    slice_range,
    suggest,
    validate,
)

MINUTE, HOUR, DAY = 60, 3600, 86400
Row = tuple[int, float, float, float, float, float]


def utc(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp())


def frame(rows: Sequence[Sequence[float]]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=CANDLE_SCHEMA, orient="row")


def rows(df: pl.DataFrame) -> list[Row]:
    return [tuple(row) for row in df.iter_rows()]


def by_date(sessions: list[Session], day: str) -> Session:
    return next(session for session in sessions if session.date == date.fromisoformat(day))


def minutes(start: int, count: int) -> list[Row]:
    """1m candles from ``start``; candle i opens at 100 + i and closes at 100.25 + i."""
    return [
        (start + i * MINUTE, 100.0 + i, 100.5 + i, 99.5 + i, 100.25 + i, 1.0) for i in range(count)
    ]


def bar(start: int, first: int, last: int) -> Row:
    """What minutes(start, ...) aggregates to over minutes first..last (inclusive)."""
    return (start, 100.0 + first, 100.5 + last, 99.5 + first, 100.25 + last, last - first + 1.0)


# normalise


def test_normalise_casts_sorts_and_keeps_the_last_duplicate() -> None:
    raw = pl.DataFrame(
        {
            "volume": ["1.5", "2", "3", "4"],
            "ts": [180, 60, 120, 60],
            "open": ["10", "11", "12", "13"],
            "high": [10, 11, 12, 13],
            "low": [10, 11, 12, 13],
            "close": [10.0, 11.0, 12.0, 13.0],
            "close_time": [239, 119, 179, 119],
        }
    )

    result = normalise(raw)

    assert result.schema == CANDLE_SCHEMA
    assert rows(result) == [
        (60, 13.0, 13.0, 13.0, 13.0, 4.0),
        (120, 12.0, 12.0, 12.0, 12.0, 3.0),
        (180, 10.0, 10.0, 10.0, 10.0, 1.5),
    ]


def test_normalise_rejects_missing_columns() -> None:
    raw = pl.DataFrame({"ts": [60], "open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0]})

    with pytest.raises(DataIntegrityError, match="volume"):
        normalise(raw)


def test_normalise_rejects_unreadable_values() -> None:
    raw = pl.DataFrame(
        {"ts": [60], "open": ["abc"], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1.0]}
    )

    with pytest.raises(DataIntegrityError, match="cannot be read"):
        normalise(raw)


# validate


def test_validate_accepts_good_candles() -> None:
    validate(frame([(60, 10, 12, 9, 11, 0), (120, 11, 11, 11, 11, 5)]))
    validate(frame([]))


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        ((120, 0, 12, 9, 11, 1), "1 candle has a price at or below zero"),
        ((120, 10, 12, -9, 11, 1), "1 candle has a price at or below zero"),
        ((120, 10, 12, 10.5, 11, 1), r"1 candle has low above min\(open, close\)"),
        ((120, 10, 10.5, 9, 11, 1), r"1 candle has high below max\(open, close\)"),
        ((120, 10, 12, 9, 11, -1), "1 candle has negative volume"),
        ((120, float("nan"), 12, 9, 11, 1), "1 candle has a missing or non-finite value"),
        ((120, 10, float("inf"), 9, 11, 1), "1 candle has a missing or non-finite value"),
        ((60, 10, 12, 9, 11, 1), "1 candle has a time not after the previous candle"),
    ],
)
def test_validate_rejects(bad: Row, message: str) -> None:
    candles = frame([(60, 10, 12, 9, 11, 1), bad, (180, 10, 12, 9, 11, 1)])

    with pytest.raises(DataIntegrityError, match=message):
        validate(candles)


def test_validate_reports_every_problem_with_its_first_time() -> None:
    candles = frame(
        [
            (60, 10, 12, 9, 11, 1),
            (120, 10, 12, 9, 11, -1),
            (180, 10, 12, 9, 11, -2),
            (240, 10, 12, 10.5, 11, 1),
        ]
    )

    with pytest.raises(DataIntegrityError) as error:
        validate(candles)

    assert error.value.detail == (
        "Invalid candle data: "
        "1 candle has low above min(open, close), first at 1970-01-01T00:04:00Z (240); "
        "2 candles have negative volume, first at 1970-01-01T00:02:00Z (120)."
    )


def test_validate_checks_the_grid_of_fixed_bins() -> None:
    candles = frame([(HOUR, 10, 12, 9, 11, 1), (2 * HOUR + 1694, 10, 12, 9, 11, 1)])

    validate(candles)  # session bins: no grid
    with pytest.raises(DataIntegrityError) as error:
        validate(candles, Timeframe.H1)

    assert error.value.detail == (
        "Invalid candle data: 1 candle has an open time off the 1h UTC grid, "
        "first at 1970-01-01T02:28:14Z (8894)."
    )


def test_validate_rejects_missing_values() -> None:
    candles = pl.DataFrame(
        {
            "ts": [60, 120],
            "open": [1.0, 1.0],
            "high": [1.0, None],
            "low": [1.0, 1.0],
            "close": [1.0, 1.0],
            "volume": [1.0, 1.0],
        },
        schema=CANDLE_SCHEMA,
    )

    with pytest.raises(DataIntegrityError, match="1 candle has a missing or non-finite value"):
        validate(candles)


# resample_fixed


def test_resample_fixed_1m_to_1h_by_hand() -> None:
    day = utc("2024-01-01 00:00")
    candles = frame(
        [
            (day + 58 * MINUTE, 10, 12, 9, 11, 1),
            (day + 59 * MINUTE, 11, 13, 10, 12, 2),
            (day + HOUR, 12, 12.5, 11, 11.5, 3),
            (day + HOUR + 30 * MINUTE, 11.5, 15, 11.5, 14, 4),
            (day + HOUR + 59 * MINUTE, 14, 14, 8, 9, 5),
            # no candle in 02:00-03:00: no bin
            (day + 3 * HOUR, 9, 10, 9, 10, 6),
        ]
    )

    assert rows(resample_fixed(candles, Timeframe.H1)) == [
        (day, 10, 13, 9, 12, 3),
        (day + HOUR, 12, 15, 8, 9, 12),
        (day + 3 * HOUR, 9, 10, 9, 10, 6),
    ]


def test_resample_fixed_aligns_to_utc_epoch_multiples() -> None:
    start = utc("2024-01-01 22:00")
    candles = frame(minutes(start, 4 * 60))  # 22:00-01:59 across midnight

    assert rows(resample_fixed(candles, Timeframe.D1)) == [
        bar(utc("2024-01-01 00:00"), 0, 119),
        bar(utc("2024-01-02 00:00"), 120, 239),
    ]
    assert [row[0] for row in rows(resample_fixed(candles, Timeframe.H4))] == [
        utc("2024-01-01 20:00"),
        utc("2024-01-02 00:00"),
    ]


def test_resample_fixed_empty() -> None:
    result = resample_fixed(frame([]), Timeframe.H1)

    assert result.schema == CANDLE_SCHEMA
    assert result.height == 0


# resample_sessions


def test_resample_sessions_normal_day(sessions: list[Session]) -> None:
    session = by_date(sessions, "2024-06-03")  # 13:30-20:00 UTC
    outside = [
        (session.open - MINUTE, 999, 999, 999, 999, 99),  # pre-market print
        (session.close, 999, 999, 999, 999, 99),  # after the close
    ]
    candles = frame([outside[0], *minutes(session.open, 390), outside[1]])

    assert rows(resample_sessions(candles, Timeframe.H1, sessions)) == [
        bar(session.open + k * HOUR, k * 60, min(k * 60 + 59, 389)) for k in range(7)
    ]
    assert rows(resample_sessions(candles, Timeframe.H4, sessions)) == [
        bar(session.open, 0, 239),
        bar(session.open + 4 * HOUR, 240, 389),
    ]
    assert rows(resample_sessions(candles, Timeframe.D1, sessions)) == [bar(session.open, 0, 389)]
    assert resample_sessions(candles, Timeframe.M1, sessions).height == 390


def test_resample_sessions_30m_is_two_15m_candles(sessions: list[Session]) -> None:
    for day, count in (("2024-06-03", 390), ("2024-11-29", 210)):  # normal, early close
        session = by_date(sessions, day)
        candles = frame(minutes(session.open, count))

        halves = rows(resample_sessions(candles, Timeframe.M30, sessions))
        quarters = resample_sessions(candles, Timeframe.M15, sessions)

        assert halves == rows(resample_sessions(quarters, Timeframe.M30, sessions))
        assert halves == [
            bar(session.open + k * 1800, k * 30, k * 30 + 29) for k in range(count // 30)
        ]


def test_resample_sessions_early_close(sessions: list[Session]) -> None:
    session = by_date(sessions, "2024-11-29")  # 14:30-18:00 UTC
    after_close = (session.close, 999, 999, 999, 999, 99)
    candles = frame([*minutes(session.open, 210), after_close])

    hourly = rows(resample_sessions(candles, Timeframe.H1, sessions))
    assert hourly == [bar(session.open + k * HOUR, k * 60, min(k * 60 + 59, 209)) for k in range(4)]
    assert hourly[-1][5] == 30  # 12:30-13:00
    assert rows(resample_sessions(candles, Timeframe.H4, sessions)) == [bar(session.open, 0, 209)]
    assert rows(resample_sessions(candles, Timeframe.D1, sessions)) == [bar(session.open, 0, 209)]


def test_resample_sessions_follows_dst(sessions: list[Session]) -> None:
    friday = utc("2024-03-08 14:30")  # 09:30 EST
    monday = utc("2024-03-11 13:30")  # 09:30 EDT
    candles = frame([*minutes(friday, 90), *minutes(monday, 90)])

    assert [row[0] for row in rows(resample_sessions(candles, Timeframe.H1, sessions))] == [
        friday,
        friday + HOUR,
        monday,
        monday + HOUR,
    ]


def test_resample_sessions_one_candle_per_day(sessions: list[Session]) -> None:
    monday, tuesday = by_date(sessions, "2024-06-03"), by_date(sessions, "2024-06-04")
    candles = frame([*minutes(monday.open, 390), *minutes(tuesday.open, 390)])

    result = rows(resample_sessions(candles, Timeframe.D1, sessions))

    assert [(row[0], row[5]) for row in result] == [(monday.open, 390), (tuesday.open, 390)]


def test_resample_sessions_empty(sessions: list[Session]) -> None:
    assert resample_sessions(frame([]), Timeframe.H1, sessions).schema == CANDLE_SCHEMA
    assert (
        resample_sessions(frame(minutes(utc("2024-01-01 12:00"), 5)), Timeframe.H1, []).height == 0
    )


# session_last


def last_marked(candles: pl.DataFrame, timeframe: Timeframe, sessions: list[Session]) -> list[int]:
    """The open times of the candles marked the last of their session."""
    marks = session_last(candles, timeframe, sessions)
    assert marks.name == "session_last"
    assert marks.dtype == pl.Boolean
    assert marks.len() == candles.height
    return candles.filter(marks)["ts"].to_list()


def test_session_last_30m_normal_day(sessions: list[Session]) -> None:
    session = by_date(sessions, "2024-06-03")  # 13:30-20:00 UTC
    candles = resample_sessions(frame(minutes(session.open, 390)), Timeframe.M30, sessions)

    assert candles.height == 13
    assert last_marked(candles, Timeframe.M30, sessions) == [utc("2024-06-03 19:30")]  # 15:30


def test_session_last_30m_early_close(sessions: list[Session]) -> None:
    session = by_date(sessions, "2024-11-29")  # 14:30-18:00 UTC
    candles = resample_sessions(frame(minutes(session.open, 210)), Timeframe.M30, sessions)

    assert candles.height == 7
    assert last_marked(candles, Timeframe.M30, sessions) == [utc("2024-11-29 17:30")]  # 12:30


@pytest.mark.parametrize(
    ("timeframe", "normal", "early"),
    [
        # New York opens of the last candle on 2024-06-03 (EDT) and 2024-11-29 (EST).
        (Timeframe.M1, "2024-06-03 19:59", "2024-11-29 17:59"),
        (Timeframe.M5, "2024-06-03 19:55", "2024-11-29 17:55"),
        (Timeframe.M15, "2024-06-03 19:45", "2024-11-29 17:45"),
        (Timeframe.H1, "2024-06-03 19:30", "2024-11-29 17:30"),  # 30 minutes long
        (Timeframe.H4, "2024-06-03 17:30", "2024-11-29 14:30"),  # 13:30-16:00, 09:30-13:00
        (Timeframe.D1, "2024-06-03 13:30", "2024-11-29 14:30"),
    ],
)
def test_session_last_marks_one_candle_a_session(
    sessions: list[Session], timeframe: Timeframe, normal: str, early: str
) -> None:
    normal_day, early_day = by_date(sessions, "2024-06-03"), by_date(sessions, "2024-11-29")
    one_minute = frame([*minutes(normal_day.open, 390), *minutes(early_day.open, 210)])
    candles = resample_sessions(one_minute, timeframe, sessions)

    assert last_marked(candles, timeframe, sessions) == [utc(normal), utc(early)]


def test_session_last_is_the_calendar_not_the_last_candle_there_is(
    sessions: list[Session],
) -> None:
    # Without its 15:30 candle the day has no last candle: 15:00 does not end the session,
    # and to call it the last would need the next candle, which comes later.
    monday, tuesday = by_date(sessions, "2024-06-03"), by_date(sessions, "2024-06-04")
    one_minute = frame([*minutes(monday.open, 360), *minutes(tuesday.open, 390)])
    candles = resample_sessions(one_minute, Timeframe.M30, sessions)

    assert candles["ts"][11] == utc("2024-06-03 19:00")  # Monday's last, 15:00-15:30
    assert last_marked(candles, Timeframe.M30, sessions) == [utc("2024-06-04 19:30")]


def test_session_last_outside_sessions_and_empty(sessions: list[Session]) -> None:
    session = by_date(sessions, "2024-06-03")
    outside = frame(
        [
            (utc("2023-12-29 20:00"), 1, 1, 1, 1, 1),  # before the calendar
            (session.open - HOUR, 1, 1, 1, 1, 1),  # pre-market
            (session.close, 1, 1, 1, 1, 1),  # after the close
            (utc("2024-07-04 15:00"), 1, 1, 1, 1, 1),  # a holiday
        ]
    )

    assert last_marked(outside, Timeframe.H1, sessions) == []
    assert last_marked(frame([]), Timeframe.H1, sessions) == []
    assert last_marked(frame(minutes(session.open, 3)), Timeframe.M1, []) == []


# expected bins


def test_expected_bins_fixed() -> None:
    day = utc("2024-01-01 00:00")

    assert expected_bins_fixed(day, day + 3 * HOUR, Timeframe.H1) == [
        day,
        day + HOUR,
        day + 2 * HOUR,
    ]
    assert expected_bins_fixed(day + 1, day + 3 * HOUR + 1, Timeframe.H1) == [
        day + HOUR,
        day + 2 * HOUR,
        day + 3 * HOUR,
    ]
    assert expected_bins_fixed(day, day, Timeframe.H1) == []


def test_expected_bins_fixed_leaves_out_candles_not_closed_yet() -> None:
    day = utc("2024-01-01 00:00")

    assert expected_bins_fixed(day, day + 5 * HOUR, Timeframe.H1, now=day + 2 * HOUR + 1800) == [
        day,
        day + HOUR,
    ]
    assert expected_bins_fixed(day, day + 5 * HOUR, Timeframe.H1, now=day + 3 * HOUR) == [
        day,
        day + HOUR,
        day + 2 * HOUR,
    ]


def test_expected_bins_sessions(sessions: list[Session]) -> None:
    monday = by_date(sessions, "2024-06-03")

    day = expected_bins_sessions(
        utc("2024-06-03 00:00"), utc("2024-06-04 00:00"), Timeframe.H1, sessions
    )
    assert day == [monday.open + k * HOUR for k in range(7)]

    # Thanksgiving week: three normal days, the holiday, then an early close
    week = expected_bins_sessions(
        utc("2024-11-25 00:00"), utc("2024-11-30 00:00"), Timeframe.H1, sessions
    )
    assert len(week) == 7 + 7 + 7 + 0 + 4
    assert not [t for t in week if utc("2024-11-28 00:00") <= t < utc("2024-11-29 00:00")]

    # a start inside the session skips the bins that open before it
    late = expected_bins_sessions(monday.open + 1, monday.close, Timeframe.H1, sessions)
    assert late == [monday.open + k * HOUR for k in range(1, 7)]

    assert (
        expected_bins_sessions(
            utc("2024-07-04 00:00"), utc("2024-07-05 00:00"), Timeframe.H1, sessions
        )
        == []
    )


def test_expected_bins_sessions_leaves_out_candles_not_closed_yet(sessions: list[Session]) -> None:
    monday = by_date(sessions, "2024-06-03")

    def closed(now: int) -> int:
        return len(
            expected_bins_sessions(monday.open, monday.close, Timeframe.H1, sessions, now=now)
        )

    assert closed(monday.open + HOUR + 1800) == 1
    assert closed(monday.close - 1) == 6
    assert closed(monday.close) == 7  # the 30-minute last candle ends at the close


# find_gaps


def test_find_gaps_merges_neighbours() -> None:
    expected = [k * HOUR for k in range(10)]
    present = [t for k, t in enumerate(expected) if k not in {2, 3, 4, 7}]

    gaps = find_gaps(pl.Series(present), expected, Timeframe.H1)

    assert gaps == Gaps(ranges=[(2 * HOUR, 5 * HOUR), (7 * HOUR, 8 * HOUR)], total=4)


def test_find_gaps_prints_no_polars_deprecation(capfd: pytest.CaptureFixture[str]) -> None:
    # Polars reports some deprecations from its engine straight to stderr, past filterwarnings.
    find_gaps([0], [k * HOUR for k in range(3)], Timeframe.H1)

    assert "DeprecationWarning" not in capfd.readouterr().err


def test_find_gaps_none() -> None:
    expected = [k * HOUR for k in range(3)]

    assert find_gaps([*expected, 99 * HOUR], expected, Timeframe.H1) == Gaps([], 0)
    assert find_gaps([], [], Timeframe.H1) == Gaps([], 0)


def test_find_gaps_caps_the_ranges() -> None:
    expected = [k * MINUTE for k in range(500)]
    present = expected[1::2]  # every even candle is missing

    gaps = find_gaps(present, expected, Timeframe.M1)

    assert gaps.total == 250
    assert len(gaps.ranges) == 100
    assert gaps.ranges[0] == (0, MINUTE)
    assert gaps.ranges[-1] == (198 * MINUTE, 199 * MINUTE)


def test_find_gaps_in_sessions_end_at_the_close(sessions: list[Session]) -> None:
    monday, tuesday = by_date(sessions, "2024-06-03"), by_date(sessions, "2024-06-04")
    expected = expected_bins_sessions(monday.open, tuesday.close, Timeframe.H1, sessions)
    last_monday, first_tuesday = monday.open + 6 * HOUR, tuesday.open

    only_last = [t for t in expected if t != last_monday]
    assert find_gaps(only_last, expected, Timeframe.H1, sessions) == Gaps(
        [(last_monday, monday.close)], 1
    )

    # consecutive missing candles merge across the night
    overnight = [t for t in expected if t not in {last_monday, first_tuesday}]
    assert find_gaps(overnight, expected, Timeframe.H1, sessions) == Gaps(
        [(last_monday, tuesday.open + HOUR)], 2
    )


# closed_only and slice_range


def test_closed_only_fixed() -> None:
    candles = frame([(k * HOUR, 1, 1, 1, 1, 1) for k in range(3)])

    assert [row[0] for row in rows(closed_only(candles, Timeframe.H1, 3 * HOUR - 1))] == [0, HOUR]
    assert closed_only(candles, Timeframe.H1, 3 * HOUR).height == 3


def test_closed_only_sessions_ends_the_last_candle_at_the_close(sessions: list[Session]) -> None:
    monday = by_date(sessions, "2024-06-03")
    candles = frame([(monday.open + k * HOUR, 1, 1, 1, 1, 1) for k in range(7)])

    assert closed_only(candles, Timeframe.H1, monday.close + 300, sessions).height == 7
    assert closed_only(candles, Timeframe.H1, monday.close - 1, sessions).height == 6
    # with fixed bins the 15:30 candle would still be open at 16:05
    assert closed_only(candles, Timeframe.H1, monday.close + 300).height == 6


def test_closed_only_keeps_the_schema(sessions: list[Session]) -> None:
    assert closed_only(frame([]), Timeframe.H1, 0, sessions).schema == CANDLE_SCHEMA
    assert closed_only(frame([]), Timeframe.H1, 0).schema == CANDLE_SCHEMA


def test_slice_range_is_start_inclusive_end_exclusive() -> None:
    candles = frame([(k * HOUR, 1, 1, 1, 1, 1) for k in range(5)])

    assert [row[0] for row in rows(slice_range(candles, HOUR, 3 * HOUR))] == [HOUR, 2 * HOUR]
    assert [row[0] for row in rows(slice_range(candles, HOUR + 1, 3 * HOUR + 1))] == [
        2 * HOUR,
        3 * HOUR,
    ]


# fingerprint

CANDLES = [
    (1704067200, 42283.58, 42554.57, 42261.02, 42475.23, 1271.68108),
    (1704070800, 42475.23, 42775.0, 42431.65, 42613.56, 1196.37856),
    (1704074400, 42613.57, 42638.41, 42500.0, 42581.1, 685.0),
]
REQUEST = ("binance", "spot", "crypto:BTCUSDT", Timeframe.H1, 1704067200, 1704078000)


def test_fingerprint_byte_layout() -> None:
    n = len(CANDLES)
    columns = list(zip(*CANDLES, strict=True))
    data = b"candlestack-candles-v1\n"
    data += b"binance\nspot\ncrypto:BTCUSDT\n1h\n1704067200\n1704078000\n3\n"
    data += struct.pack(f">{n}q", *columns[0])
    for values in columns[1:]:
        data += struct.pack(f">{n}d", *values)

    assert fingerprint(*REQUEST, frame(CANDLES)) == "sha256:" + hashlib.sha256(data).hexdigest()


def test_fingerprint_ignores_row_order() -> None:
    shuffled = CANDLES.copy()
    random.Random(7).shuffle(shuffled)

    assert fingerprint(*REQUEST, frame(shuffled)) == fingerprint(*REQUEST, frame(CANDLES))


def test_fingerprint_treats_negative_zero_as_zero() -> None:
    zero = [(60, 1.0, 1.0, 1.0, 1.0, 0.0)]
    negative_zero = [(60, 1.0, 1.0, 1.0, 1.0, -0.0)]

    assert fingerprint(*REQUEST, frame(zero)) == fingerprint(*REQUEST, frame(negative_zero))


@pytest.mark.parametrize("column", range(6))
def test_fingerprint_changes_with_any_number(column: int) -> None:
    changed = [list(row) for row in CANDLES]
    changed[1][column] += 1 if column == 0 else 0.01

    assert fingerprint(*REQUEST, frame(changed)) != fingerprint(*REQUEST, frame(CANDLES))


@pytest.mark.parametrize(
    "request_",
    [
        ("alpaca", "spot", "crypto:BTCUSDT", Timeframe.H1, 1704067200, 1704078000),
        ("binance", "sip", "crypto:BTCUSDT", Timeframe.H1, 1704067200, 1704078000),
        ("binance", "spot", "crypto:ETHUSDT", Timeframe.H1, 1704067200, 1704078000),
        ("binance", "spot", "crypto:BTCUSDT", Timeframe.H4, 1704067200, 1704078000),
        ("binance", "spot", "crypto:BTCUSDT", Timeframe.H1, 1704067199, 1704078000),
        ("binance", "spot", "crypto:BTCUSDT", Timeframe.H1, 1704067200, 1704078001),
    ],
)
def test_fingerprint_changes_with_the_request(
    request_: tuple[str, str, str, Timeframe, int, int],
) -> None:
    assert fingerprint(*request_, frame(CANDLES)) != fingerprint(*REQUEST, frame(CANDLES))


def test_fingerprint_format() -> None:
    value = fingerprint(*REQUEST, frame([]))

    assert value.startswith("sha256:")
    assert len(value) == len("sha256:") + 64
    assert fingerprint(*REQUEST, frame([])) == value


# candle_count and suggest


def test_candle_count_fixed() -> None:
    assert candle_count(utc("2024-01-01 00:00"), utc("2024-04-01 00:00"), Timeframe.M1) == 131040
    assert candle_count(utc("2024-01-01 00:30"), utc("2024-01-01 03:00"), Timeframe.H1) == 2
    assert candle_count(utc("2024-01-01 00:30"), utc("2024-01-01 03:01"), Timeframe.H1) == 3
    assert candle_count(100, 100, Timeframe.M1) == 0
    assert candle_count(200, 100, Timeframe.M1) == 0


def test_candle_count_sessions(sessions: list[Session]) -> None:
    week = (utc("2024-11-25 00:00"), utc("2024-11-30 00:00"))

    assert candle_count(*week, Timeframe.H1, sessions) == 25
    assert candle_count(*week, Timeframe.D1, sessions) == 4
    assert candle_count(*week, Timeframe.M1, sessions) == 3 * 390 + 210


def test_first_of_last_fixed() -> None:
    end, now = utc("2024-01-01 10:00"), utc("2024-02-01")

    assert first_of_last(end, now, Timeframe.H1, 3) == utc("2024-01-01 07:00")
    assert first_of_last(utc("2024-01-01 10:30"), now, Timeframe.H1, 3) == utc("2024-01-01 08:00")
    assert first_of_last(end, now, Timeframe.D1, 2) == utc("2023-12-31")
    # The newest candle that has not closed by now is left out.
    assert first_of_last(end, utc("2024-01-01 09:30"), Timeframe.H1, 3) == utc("2024-01-01 06:00")
    assert first_of_last(60, now, Timeframe.H1, 5) == 0  # before the epoch: from the start


def test_first_of_last_sessions_skip_the_weekend(sessions: list[Session]) -> None:
    monday_open, now = utc("2024-06-03 13:30"), utc("2024-07-01")

    # Friday 2024-05-31 has 1h candles 13:30-19:30 (the last is 30 minutes long).
    assert first_of_last(monday_open, now, Timeframe.H1, 3, sessions) == utc("2024-05-31 17:30")
    assert first_of_last(monday_open, now, Timeframe.H1, 7, sessions) == utc("2024-05-31 13:30")
    assert first_of_last(monday_open, now, Timeframe.H1, 8, sessions) == utc("2024-05-30 19:30")
    # The 14:30 candle opens before the end 15:00 and counts, though it ends after it.
    assert first_of_last(utc("2024-06-03 15:00"), now, Timeframe.H1, 3, sessions) == utc(
        "2024-05-31 19:30"
    )
    assert first_of_last(monday_open, now, Timeframe.D1, 2, sessions) == utc("2024-05-30 13:30")
    # A candle open at ``now`` is left out.
    assert first_of_last(monday_open + HOUR, monday_open + HOUR, Timeframe.H1, 1, sessions) == (
        monday_open
    )


def test_first_of_last_stops_at_the_lower_bound(sessions: list[Session]) -> None:
    now, lower = utc("2025-01-01"), utc("2024-06-03 14:30")
    end = utc("2024-06-04 20:00")

    assert first_of_last(end, now, Timeframe.H1, 100, sessions, lower=lower) == lower
    assert first_of_last(end, now, Timeframe.H1, 3, sessions, lower=lower) == utc(
        "2024-06-04 17:30"
    )
    assert first_of_last(end, now, Timeframe.D1, 3, sessions, lower=utc("2024-06-04 15:00")) is None
    assert first_of_last(end, now, Timeframe.H1, 100, lower=lower) == lower
    assert first_of_last(end, now, Timeframe.H1, 3, lower=lower) == utc("2024-06-04 17:00")
    # No bin that opens at or after ``lower`` and is closed by now: none, for both kinds.
    assert first_of_last(lower + 1800, lower + 1800, Timeframe.H1, 3, lower=lower) is None
    assert first_of_last(lower + 1800, lower + 1800, Timeframe.H1, 3, sessions, lower=lower) is None


def test_first_of_last_with_fewer_candles_than_asked(sessions: list[Session]) -> None:
    now = utc("2025-01-01")
    one_week = [session for session in sessions if session.date <= date(2024, 1, 5)]

    assert first_of_last(utc("2024-01-06"), now, Timeframe.D1, 50, one_week) == one_week[0].open
    assert first_of_last(utc("2024-01-06"), now, Timeframe.D1, 50, []) is None
    assert first_of_last(one_week[0].open, now, Timeframe.D1, 5, one_week) is None


@pytest.mark.parametrize("timeframe", list(Timeframe))
def test_first_of_last_starts_a_period_of_exactly_limit_candles(
    sessions: list[Session], timeframe: Timeframe
) -> None:
    now = utc("2025-01-01")
    for end in (utc("2024-06-03 13:30"), utc("2024-06-03 17:12"), utc("2024-12-02 09:00")):
        for count in (1, 7, 40):
            start = first_of_last(end, now, timeframe, count)
            assert start is not None
            assert len(expected_bins_fixed(start, end, timeframe, now=now)) == count
            start = first_of_last(end, now, timeframe, count, sessions)
            assert start is not None
            assert len(expected_bins_sessions(start, end, timeframe, sessions, now=now)) == count


@pytest.mark.parametrize("timeframe", list(Timeframe))
def test_candle_count_matches_the_expected_bins(
    sessions: list[Session], timeframe: Timeframe
) -> None:
    periods = [
        (utc("2024-03-07 15:17"), utc("2024-03-12 18:41")),
        (utc("2024-06-03 13:30"), utc("2024-06-03 20:00")),
        (utc("2024-06-03 13:31"), utc("2024-06-03 19:59")),
        (utc("2024-12-20 00:00"), utc("2025-01-01 00:00")),
    ]
    for start, end in periods:
        assert candle_count(start, end, timeframe) == len(
            expected_bins_fixed(start, end, timeframe)
        )
        assert candle_count(start, end, timeframe, sessions) == len(
            expected_bins_sessions(start, end, timeframe, sessions)
        )


def test_suggest_as_in_the_contract() -> None:
    start, end = utc("2024-01-01 00:00"), utc("2024-04-01 00:00")

    assert suggest(start, end, Timeframe.M1, 50000) == (
        "Use 5m or a larger timeframe, "
        "or end the period at 2024-02-04T17:20:00Z (1707067200) or earlier."
    )


def test_suggest_the_largest_timeframe() -> None:
    start, end = utc("2022-01-01 00:00"), utc("2024-01-01 00:00")

    assert suggest(start, end, Timeframe.M1, 1000) == (
        "Use 1d, or end the period at 2022-01-01T16:40:00Z (1641055200) or earlier."
    )


def test_suggest_only_a_shorter_period() -> None:
    start, end = utc("2020-01-01 00:00"), utc("2030-01-01 00:00")

    assert suggest(start, end, Timeframe.D1, 1000) == (
        "End the period at 2022-09-27T00:00:00Z (1664236800) or earlier."
    )


def test_suggest_with_an_unaligned_start() -> None:
    start = utc("2024-01-01 00:30")

    assert suggest(start, start + DAY, Timeframe.H1, 2).endswith(
        "end the period at 2024-01-01T03:00:00Z (1704078000) or earlier."
    )


def test_suggest_sessions(sessions: list[Session]) -> None:
    start, end = utc("2024-06-03 00:00"), utc("2024-06-07 00:00")  # 4 sessions, 28 1h candles

    # 7 candles on Monday, 3 on Tuesday: the 11th candle opens Tuesday 12:30 New York time
    assert suggest(start, end, Timeframe.H1, 10, sessions) == (
        "Use 4h or a larger timeframe, "
        "or end the period at 2024-06-04T16:30:00Z (1717518600) or earlier."
    )


def test_suggest_sessions_at_a_session_boundary(sessions: list[Session]) -> None:
    start, end = utc("2024-06-03 00:00"), utc("2024-06-07 00:00")

    assert suggest(start, end, Timeframe.H1, 7, sessions).endswith(
        "end the period at 2024-06-04T13:30:00Z (1717507800) or earlier."
    )


@pytest.mark.parametrize(("timeframe", "maximum"), [(Timeframe.M1, 50000), (Timeframe.H1, 100)])
@pytest.mark.parametrize("use_sessions", [False, True])
def test_suggested_end_is_the_latest_that_fits(
    sessions: list[Session], timeframe: Timeframe, maximum: int, use_sessions: bool
) -> None:
    calendar = sessions if use_sessions else None
    start, end = utc("2024-01-02 15:07"), utc("2025-01-01 00:00")

    hint = suggest(start, end, timeframe, maximum, calendar)
    match = re.search(r"\((\d+)\) or earlier\.$", hint)
    assert match is not None
    latest = int(match[1])

    assert candle_count(start, latest, timeframe, calendar) == maximum
    assert candle_count(start, latest + 1, timeframe, calendar) == maximum + 1


def test_suggest_needs_a_period_that_does_not_fit() -> None:
    with pytest.raises(ValueError, match="fits"):
        suggest(0, HOUR, Timeframe.M1, 60)
