"""Instruments, timeframes and candle sets: the vocabulary of the data module.

Times are UTC epoch seconds everywhere; a candle is labelled by its open time.
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Self, override

import polars as pl

from candlestack.data.errors import InvalidRequest

Source = Literal["binance", "alpaca"]
Feed = Literal["spot", "sip"]

_ID_EXAMPLE = "for example crypto:BTCUSDT or stock:AAPL"
_SYMBOL_MAX = 32
# Letters and digits of any script (Binance lists pairs such as 币安人生USDT) and dots
# (BRK.B); the first character is a letter or digit.
_SYMBOL = re.compile(r"[^\W_](?:[^\W_]|\.)*")


class Market(StrEnum):
    CRYPTO = "crypto"
    STOCK = "stock"


class Exchange(StrEnum):
    """Where the instruments of the catalog trade: for stocks the listing exchanges Alpaca
    reports for tradable US equities without OTC (docs/data.md, "Alpaca"), for crypto pairs
    Binance."""

    AMEX = "AMEX"
    ARCA = "ARCA"
    BATS = "BATS"
    BINANCE = "BINANCE"
    NASDAQ = "NASDAQ"
    NYSE = "NYSE"

    @property
    def market(self) -> Market:
        """The market whose instruments trade there."""
        return Market.CRYPTO if self is Exchange.BINANCE else Market.STOCK


class InstrumentSort(StrEnum):
    """Orders of the instrument list. The stats sorts (volume, change, -change) put instruments
    without stats last; ``symbol`` is the order by symbol alone."""

    VOLUME = "volume"  # 24 h volume in USD, highest first
    CHANGE = "change"  # 24 h change, highest first: the top gainers
    LOSERS = "-change"  # 24 h change, lowest first: the top losers
    SYMBOL = "symbol"


class Timeframe(StrEnum):
    """Candle durations; iteration goes from the shortest to the longest."""

    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"

    @property
    def seconds(self) -> int:
        return _SECONDS[self]

    def larger(self) -> "list[Timeframe]":
        """The longer timeframes, shortest first."""
        members = list(Timeframe)
        return members[members.index(self) + 1 :]


_SECONDS = {
    Timeframe.M1: 60,
    Timeframe.M5: 300,
    Timeframe.M15: 900,
    Timeframe.M30: 1800,
    Timeframe.H1: 3600,
    Timeframe.H4: 14400,
    Timeframe.D1: 86400,
}


@dataclass(frozen=True, slots=True)
class InstrumentId:
    """``<market>:<symbol>``, e.g. ``crypto:BTCUSDT``; build it from text with ``parse``."""

    market: Market
    symbol: str

    @classmethod
    def parse(cls, value: str) -> Self:
        """Parses an id, trimming whitespace, lower-casing the market and upper-casing the symbol.

        Raises ``InvalidRequest`` (a ``ValueError``) with a message for the user.
        """
        market, colon, symbol = value.partition(":")
        market, symbol = market.strip().lower(), symbol.strip().upper()
        if not colon or not symbol:
            raise InvalidRequest(
                f"Instrument id '{value}' must be <market>:<symbol>, {_ID_EXAMPLE}."
            )
        if market not in Market:
            raise InvalidRequest(
                f"Unknown market '{market}' in instrument id '{value}'. "
                f"The markets are crypto and stock, {_ID_EXAMPLE}."
            )
        if len(symbol) > _SYMBOL_MAX or not _SYMBOL.fullmatch(symbol):
            raise InvalidRequest(
                f"Symbol '{symbol}' in instrument id '{value}' is not valid: use letters, "
                f"digits and dots, at most {_SYMBOL_MAX} characters, {_ID_EXAMPLE}."
            )
        return cls(Market(market), symbol)

    @override
    def __str__(self) -> str:
        return f"{self.market}:{self.symbol}"


@dataclass(frozen=True, slots=True)
class Instrument:
    """One tradable instrument of a source's catalog.

    ``exchange`` is where it trades (``NASDAQ``, ``NYSE``, ... for stocks, ``BINANCE`` for crypto
    pairs); ``base`` and ``quote`` are set for crypto pairs (``BTC``, ``USDT``).
    """

    id: InstrumentId
    name: str
    source: Source
    feed: Feed
    exchange: str | None = None
    base: str | None = None
    quote: str | None = None

    @property
    def market(self) -> Market:
        return self.id.market

    @property
    def symbol(self) -> str:
        return self.id.symbol


CANDLE_SCHEMA = pl.Schema(
    {
        "ts": pl.Int64,  # open time, UTC epoch seconds
        "open": pl.Float64,
        "high": pl.Float64,
        "low": pl.Float64,
        "close": pl.Float64,
        "volume": pl.Float64,
    }
)


# The column of the mark of the last candle of each session, as preprocessing reads it.
SESSION_LAST = "session_last"


def empty_candles() -> pl.DataFrame:
    return pl.DataFrame(schema=CANDLE_SCHEMA)


@dataclass(frozen=True, eq=False)
class CandleSet:
    """The answer to one candles request: ``[start, end)`` of one instrument and timeframe.

    ``frame`` has ``CANDLE_SCHEMA`` and holds only closed candles, sorted by ``ts``. ``gaps``
    lists merged ``(start, end)`` ranges of missing candles, earliest first and capped (see
    ``find_gaps``); ``gaps_total`` counts all missing candles. ``session_last`` (stocks) says
    for each row of ``frame`` whether it is the last candle of its session (see
    ``candles.session_last``); it is None for markets without sessions.
    """

    instrument: InstrumentId
    timeframe: Timeframe
    start: int
    end: int
    source: Source
    feed: Feed
    frame: pl.DataFrame
    gaps: list[tuple[int, int]]
    gaps_total: int
    fingerprint: str
    session_last: pl.Series | None = None

    def marked_frame(self) -> pl.DataFrame:
        """``frame`` with the column ``session_last`` when the market has sessions: the
        candles a model's input is built from (docs/data.md#last-candle-of-a-session)."""
        if self.session_last is None:
            return self.frame
        return self.frame.with_columns(self.session_last.alias(SESSION_LAST))


# The 24 h stats of the instruments of one market, as a source reports them: the last price
# (in the quote asset for a crypto pair, USD for a stock), the change against 24 hours ago (for
# stocks: the previous session's close) in percent, and the volume in USD. A row per instrument
# the source has numbers for; change and volume are null when they cannot be computed.
STATS_SCHEMA = pl.Schema(
    {
        "symbol": pl.String,
        "price": pl.Float64,
        "change_24h": pl.Float64,
        "volume_24h": pl.Float64,
    }
)


# The last daily candles of the instruments of one market, for the mini charts of the list: a
# row per candle, ``ts`` its open (00:00 UTC for crypto, the session open for stocks).
SPARK_SCHEMA = pl.Schema(
    {
        "symbol": pl.String,
        "ts": pl.Int64,
        "open": pl.Float64,
        "high": pl.Float64,
        "low": pl.Float64,
        "close": pl.Float64,
    }
)
# Daily candles in a mini chart.
SPARK_DAYS = 10

type Spark = list[tuple[int, float, float, float, float] | None]
"""An instrument's mini chart: a slot per day of its market's last ``SPARK_DAYS`` daily candles,
oldest first, each ``(open time, open, high, low, close)`` or ``None`` for a day without one."""


@dataclass(frozen=True, eq=False)
class SourceStats:
    """What a source reports for a market's stats or daily candles: ``frame`` (``STATS_SCHEMA``
    or ``SPARK_SCHEMA``) and the symbols it could not ask for this time (a failed request), whose
    last rows are kept."""

    frame: pl.DataFrame
    failed: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class InstrumentStats:
    """An instrument's last price, its 24 h change in percent and its 24 h volume in USD (see
    ``STATS_SCHEMA``); ``NO_STATS`` stands for an instrument without them."""

    price: float | None
    change_24h: float | None
    volume_24h: float | None


NO_STATS = InstrumentStats(None, None, None)


@dataclass(frozen=True)
class SearchResult:
    """A page of the instruments found, best first, the number of all of them, and the
    searched markets that were left out because their catalog cannot be loaded right now
    (their source is down). ``stats``, ``sparks`` and ``firsts`` have an entry per item: its
    stats, its mini chart and the open time of its first candle, or ``None`` when there are
    none (yet) or the first candle is not known yet."""

    items: list[Instrument]
    unavailable: list[Market]
    total: int
    stats: list[InstrumentStats | None]
    sparks: list[Spark | None]
    firsts: list[int | None]


@dataclass(frozen=True)
class InstrumentInfo:
    """An instrument plus what a client needs to build a valid candles request.

    ``available_from`` is the open time of the first candle (``None`` when the source has none
    or could not be asked), ``available_to`` the open time of the newest closed 1m bin at the
    time of the answer (for stocks the last minute of the latest session that has begun).
    """

    instrument: Instrument
    timeframes: tuple[Timeframe, ...]
    available_from: int | None
    available_to: int
    max_candles: int


@dataclass(frozen=True)
class SourceHealth:
    """Whether a source answered its cheapest request; ``detail`` says why not."""

    ok: bool
    latency_ms: int | None
    detail: str | None
