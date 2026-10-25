from datetime import UTC, datetime
from typing import cast

import polars as pl
import pytest

import candlestack.data.service as service_module
from candlestack.data import (
    CANDLE_SCHEMA,
    InstrumentId,
    Session,
    Timeframe,
    resample_sessions,
    session_last,
    slice_range,
)
from candlestack.data.cache import decode_chunk, encode_chunk
from candlestack.data.service import (
    CandleRequest,
    assemble,
    bin_open,
    daily_close,
    newest_minute,
    next_close,
)
from candlestack.data.sources.base import DAY, DataSource, Period, plan_periods

BTC, AAPL = InstrumentId.parse("crypto:BTCUSDT"), InstrumentId.parse("stock:AAPL")


class Named:
    """What ``assemble`` uses of a source: its name and feed."""

    def __init__(self, name: str, feed: str) -> None:
        self.name, self.feed = name, feed


Binance = cast("DataSource", Named("binance", "spot"))
Alpaca = cast("DataSource", Named("alpaca", "sip"))


def utc(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp())


def test_newest_minute_around_the_clock() -> None:
    assert newest_minute(utc("2026-09-26T12:34:56"), None) == utc("2026-09-26T12:33")
    assert newest_minute(utc("2026-09-26T12:34:00"), None) == utc("2026-09-26T12:33")


def test_newest_minute_of_stocks(sessions: list[Session]) -> None:
    # 2024-11-29 is an early close (14:30-18:00 UTC), 2024-12-02 a normal Monday.
    assert newest_minute(utc("2024-11-29T15:00:30"), sessions) == utc("2024-11-29T14:59")
    assert newest_minute(utc("2024-11-29T14:30:59"), sessions) == utc("2024-11-27T20:59")
    assert newest_minute(utc("2024-11-29T14:31:00"), sessions) == utc("2024-11-29T14:30")
    assert newest_minute(utc("2024-11-30T10:00"), sessions) == utc("2024-11-29T17:59")
    assert newest_minute(utc("2024-12-02T14:00"), sessions) == utc("2024-11-29T17:59")


def test_daily_close_of_crypto_is_the_last_utc_midnight() -> None:
    assert daily_close(utc("2026-09-26T12:34"), None) == utc("2026-09-26T00:00")
    assert daily_close(utc("2026-09-26T00:00"), None) == utc("2026-09-26T00:00")


def test_daily_close_of_stocks_is_the_close_of_the_last_session(sessions: list[Session]) -> None:
    # 2024-11-29 closes early (18:00 UTC); 11-28 is Thanksgiving.
    assert daily_close(utc("2024-11-29T17:59"), sessions) == utc("2024-11-27T21:00")
    assert daily_close(utc("2024-11-29T18:00"), sessions) == utc("2024-11-29T18:00")
    assert daily_close(utc("2024-12-01T12:00"), sessions) == utc("2024-11-29T18:00")
    assert daily_close(utc("2015-06-01T12:00"), sessions) is None


def test_next_close_after_weekends_and_holidays(sessions: list[Session]) -> None:
    assert next_close(utc("2026-09-26T00:00"), None) == utc("2026-09-27T00:00")
    # Wednesday before Thanksgiving: the next session is Friday's early close.
    assert next_close(utc("2024-11-27T21:00"), sessions) == utc("2024-11-29T18:00")
    assert next_close(utc("2024-11-29T18:00"), sessions) == utc("2024-12-02T21:00")
    # Past the end of the calendar: four days later.
    assert next_close(utc("2024-12-31T21:00"), sessions) == utc("2025-01-04T21:00")


def test_bin_open_of_fixed_bins() -> None:
    first = utc("2026-09-24T11:07")

    assert bin_open(first, Timeframe.M1, None) == first
    assert bin_open(first, Timeframe.H4, None) == utc("2026-09-24T08:00")
    assert bin_open(first, Timeframe.D1, None) == utc("2026-09-24")


def test_bin_open_of_session_bins(sessions: list[Session]) -> None:
    first = utc("2024-06-03T15:10")  # 11:10 EDT, the session opened 13:30 UTC

    assert bin_open(first, Timeframe.H1, sessions) == utc("2024-06-03T14:30")
    assert bin_open(first, Timeframe.H4, sessions) == utc("2024-06-03T13:30")
    assert bin_open(first, Timeframe.D1, sessions) == utc("2024-06-03T13:30")
    assert bin_open(utc("2024-06-03T21:00"), Timeframe.H1, sessions) == utc("2024-06-03T21:00")


def test_chunk_round_trip() -> None:
    frame = pl.DataFrame(
        [(60, 10.0, 12.0, 9.0, 11.0, 0.5), (120, 11.0, 11.0, 11.0, 11.0, 0.0)],
        schema=CANDLE_SCHEMA,
        orient="row",
    )

    decoded, complete_until = decode_chunk(encode_chunk(frame, 1790380620))

    assert decoded.equals(frame)
    assert decoded.schema == CANDLE_SCHEMA
    assert complete_until == 1790380620


def test_empty_chunk_round_trip() -> None:
    frame = pl.DataFrame(schema=CANDLE_SCHEMA)

    decoded, _ = decode_chunk(encode_chunk(frame, 0))

    assert decoded.schema == CANDLE_SCHEMA
    assert decoded.height == 0


def minutes(opens: list[int]) -> pl.DataFrame:
    return pl.DataFrame(
        [(ts, 100.0, 101.0, 99.0, 100.5, 1.0) for ts in opens], schema=CANDLE_SCHEMA, orient="row"
    )


def test_assemble_resamples_stock_chunks_in_batches(
    sessions: list[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_module, "RESAMPLE_CHUNKS", 5)  # 12 months in batches of 5
    start, end, now = utc("2024-01-01"), utc("2025-01-01"), utc("2025-06-01")
    bars = minutes(
        [
            s.open + m * 60
            for s in sessions
            for m in (0, 59, 60, 61, 239, 240, 389)
            if s.open + m * 60 < s.close
        ]
    )
    periods = plan_periods(
        start, end, now, yearly=False, daily=False, closed_ttl=DAY, current_ttl=DAY
    )
    blobs = [encode_chunk(slice_range(bars, p.start, p.end), p.end) for p in periods]

    for timeframe in (Timeframe.H1, Timeframe.H4):
        result = assemble(
            Alpaca,
            CandleRequest(AAPL, timeframe, start, end),
            list(zip(periods, blobs, strict=True)),
            base=Timeframe.M1,
            now=now,
            sessions=sessions,
        )

        assert len(periods) == 12
        assert result.frame.equals(resample_sessions(bars, timeframe, sessions))
        assert result.session_last is not None
        assert result.session_last.equals(session_last(result.frame, timeframe, sessions))
        # One last candle a session, but the three early closes have no 1h candle 12:30-13:00
        # here (their minutes end at 10:31); the frame carries the marks for the model.
        assert result.session_last.sum() == len(sessions) - (3 if timeframe is Timeframe.H1 else 0)
        marked = result.marked_frame()
        assert marked.columns == [*CANDLE_SCHEMA, "session_last"]
        assert marked.drop("session_last").equals(result.frame)


def test_assemble_knows_candles_only_up_to_the_live_fetch() -> None:
    # The live chunk was fetched at 12:00:30; the request comes 50 s later, when the 12:00
    # candle has closed but is not in the chunk: it is neither returned nor a gap.
    fetched, now = utc("2026-09-26T12:00:30"), utc("2026-09-26T12:01:20")
    live = Period("live", utc("2026-09-26"), fetched, "2026-09-26-live", 60)
    blob = encode_chunk(
        minutes(list(range(utc("2026-09-26T11:50"), utc("2026-09-26T12:00"), 60))), fetched
    )

    result = assemble(
        Binance,
        CandleRequest(BTC, Timeframe.M1, utc("2026-09-26T11:50"), now),
        [(live, blob)],
        base=Timeframe.M1,
        now=now,
        sessions=None,
    )

    assert result.frame["ts"][-1] == utc("2026-09-26T11:59")
    assert (result.gaps, result.gaps_total) == ([], 0)
    # Around the clock there is no session to be the last candle of.
    assert result.session_last is None
    assert result.marked_frame() is result.frame
