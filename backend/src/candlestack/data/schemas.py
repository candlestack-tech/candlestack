"""Response bodies of the data API, with the descriptions and examples the API reference shows.

The candles body is the exception on the way out: ``candles_json`` serializes it with orjson
straight from the Polars columns, and ``CandlesOut`` only documents it.
"""

from collections.abc import Mapping
from dataclasses import asdict
from typing import Any, Literal, Self

import orjson
from pydantic import BaseModel, Field

from candlestack.data.models import (
    NO_STATS,
    CandleSet,
    Feed,
    Instrument,
    InstrumentInfo,
    InstrumentStats,
    Market,
    Source,
    SourceHealth,
    Spark,
    Timeframe,
)


class InstrumentOut(BaseModel):
    """One instrument of a source's catalog."""

    id: str = Field(description="Instrument id, `<market>:<symbol>`.", examples=["crypto:BTCUSDT"])
    market: Market = Field(examples=["crypto"])
    symbol: str = Field(description="Symbol as the source spells it.", examples=["BTCUSDT"])
    name: str = Field(
        description="Crypto: `<base>/<quote>`. Stocks: the company or fund name from Alpaca.",
        examples=["BTC/USDT"],
    )
    source: Source = Field(examples=["binance"])
    feed: Feed = Field(
        description="`spot` (Binance spot) or `sip` (Alpaca SIP: trades of every US exchange).",
        examples=["spot"],
    )
    exchange: str | None = Field(
        description="Where it trades: the listing exchange of a stock (`NASDAQ`, `NYSE`, "
        "`ARCA`, ...), `BINANCE` for a crypto pair (null for crypto before #182; stays nullable).",
        examples=["BINANCE"],
    )
    base: str | None = Field(
        description="Base asset of a crypto pair; null for stocks.", examples=["BTC"]
    )
    quote: str | None = Field(
        description="Quote asset of a crypto pair; null for stocks.", examples=["USDT"]
    )

    @classmethod
    def of(cls, instrument: Instrument) -> Self:
        return cls(**_instrument_fields(instrument))


def _instrument_fields(instrument: Instrument) -> dict[str, Any]:
    return {
        "id": str(instrument.id),
        "market": instrument.market,
        "symbol": instrument.symbol,
        "name": instrument.name,
        "source": instrument.source,
        "feed": instrument.feed,
        "exchange": instrument.exchange,
        "base": instrument.base,
        "quote": instrument.quote,
    }


class InstrumentItemOut(InstrumentOut):
    """An instrument of the list with its 24 h stats, refreshed every few minutes, its mini
    chart of the last daily candles, refreshed once a day, and since when there is data; null
    while there are none (yet)."""

    price: float | None = Field(
        description="Last price: in the quote asset for a crypto pair, in USD for a stock "
        "(the latest trade, delayed by 15 minutes, also while the market is closed).",
        examples=[83249.35],
    )
    change_24h: float | None = Field(
        description="Change of the price in percent: crypto against 24 hours ago, stocks "
        "against the previous session's close.",
        examples=[1.51],
    )
    volume_24h: float | None = Field(
        description="Volume in USD: crypto of the last 24 hours, stocks of the latest session "
        "on every US exchange, extended hours included.",
        examples=[1258811866.0],
    )

    spark: list[tuple[int, float, float, float, float] | None] | None = Field(
        description="The last 10 daily candles of the instrument's market, oldest first, each "
        "`[open time, open, high, low, close]` (open time in UTC epoch seconds: 00:00 UTC for "
        "crypto, the session open for stocks), or null for a day without a candle of this "
        "instrument. Null when it has none of them. Refreshed once a day after the market's "
        "daily close.",
        examples=[[[1760313600, 115271.8, 115950.1, 113000.4, 115166.0], None]],
    )
    available_from: int | None = Field(
        description="Open time of the first candle, UTC epoch seconds, as in the instrument "
        "detail. Null while it is not known yet: the list never waits for a source, and looks "
        "up a few unknown ones per request in the background, so a later request has them. "
        "Also null for an instrument without any candle yet.",
        examples=[1502942400],
    )

    @classmethod
    def of_item(
        cls,
        instrument: Instrument,
        stats: InstrumentStats | None,
        spark: Spark | None,
        available_from: int | None,
    ) -> Self:
        return cls(
            **_instrument_fields(instrument),
            **asdict(stats or NO_STATS),
            spark=spark,
            available_from=available_from,
        )


class InstrumentSearchOut(BaseModel):
    items: list[InstrumentItemOut] = Field(description="Best matches first.")
    count: int = Field(description="Number of items.", examples=[1])
    total: int = Field(
        description="Number of all matches; page with `offset` while `offset + count` is less.",
        examples=[1],
    )
    offset: int = Field(description="Number of matches before the first item.", examples=[0])
    unavailable: list[Market] = Field(
        description="Markets left out of this search because their instrument list cannot be "
        "loaded right now (the source is down); search again later. Empty normally.",
        examples=[[]],
    )


class TopInstrumentsOut(BaseModel):
    items: list[InstrumentItemOut] = Field(description="The most traded first.")


class InstrumentDetailOut(InstrumentOut):
    """An instrument and what a valid candle request for it can ask for."""

    timeframes: list[Timeframe] = Field(examples=[list(Timeframe)])
    available_from: int | None = Field(
        description="Open time of the first candle, UTC epoch seconds; null when not known.",
        examples=[1502928000],
    )
    available_to: int = Field(
        description="Open time of the newest closed 1m candle, UTC epoch seconds.",
        examples=[1790424000],
    )
    max_candles: int = Field(
        description="Most candles one candle response may hold.", examples=[50000]
    )

    @classmethod
    def of_info(cls, info: InstrumentInfo) -> Self:
        return cls(
            **InstrumentOut.of(info.instrument).model_dump(),
            timeframes=list(info.timeframes),
            available_from=info.available_from,
            available_to=info.available_to,
            max_candles=info.max_candles,
        )


class CandlesMeta(BaseModel):
    instrument: str = Field(description="Instrument id, normalised.", examples=["crypto:BTCUSDT"])
    timeframe: Timeframe = Field(examples=["1h"])
    start: int = Field(
        description="Start of the period (inclusive), UTC epoch seconds.", examples=[1717200000]
    )
    end: int = Field(
        description="End of the period (exclusive), UTC epoch seconds.", examples=[1717214400]
    )
    source: Source = Field(examples=["binance"])
    feed: Feed = Field(examples=["spot"])
    fingerprint: str = Field(
        description="`sha256:<hex>` over the request and its candles: the same candles give the "
        "same value, any changed number another.",
        examples=["sha256:3b1f0c9e5d7a4b2c8e6f1a0d9c7b5e3f2a4c6e8d0b1f3a5c7e9d2b4f6a8c0e1d"],
    )
    count: int = Field(description="Number of candles returned.", examples=[3])
    gaps: list[tuple[int, int]] = Field(
        description="Missing candles as merged `[start, end)` ranges in UTC epoch seconds, "
        "earliest first, at most 100.",
        examples=[[[1717207200, 1717210800]]],
    )
    gaps_total: int = Field(description="Number of missing candles in total.", examples=[1])


class CandlesOut(BaseModel):
    """Candles as columns: index `i` of every array is one candle, oldest first."""

    meta: CandlesMeta
    t: list[int] = Field(
        description="Open times, UTC epoch seconds, strictly increasing.",
        examples=[[1717200000, 1717203600, 1717210800]],
    )
    o: list[float] = Field(description="Open prices.", examples=[[67491.0, 67612.5, 67580.1]])
    h: list[float] = Field(description="High prices.", examples=[[67700.0, 67650.0, 67640.0]])
    l: list[float] = Field(description="Low prices.", examples=[[67420.2, 67510.0, 67488.8]])  # noqa: E741
    c: list[float] = Field(description="Close prices.", examples=[[67612.4, 67540.3, 67601.9]])
    v: list[float] = Field(
        description="Volumes: base asset for crypto, shares (every US exchange) for stocks.",
        examples=[[512.31, 398.07, 421.55]],
    )


def candles_json(candles: CandleSet) -> bytes:
    """The ``CandlesOut`` body of a candle set, serialized from its columns in one pass."""
    frame = candles.frame
    return orjson.dumps(
        {
            "meta": {
                "instrument": str(candles.instrument),
                "timeframe": str(candles.timeframe),
                "start": candles.start,
                "end": candles.end,
                "source": candles.source,
                "feed": candles.feed,
                "fingerprint": candles.fingerprint,
                "count": frame.height,
                "gaps": candles.gaps,
                "gaps_total": candles.gaps_total,
            },
            "t": frame["ts"].to_list(),
            "o": frame["open"].to_list(),
            "h": frame["high"].to_list(),
            "l": frame["low"].to_list(),
            "c": frame["close"].to_list(),
            "v": frame["volume"].to_list(),
        }
    )


class SourceHealthOut(BaseModel):
    status: Literal["ok", "error"]
    latency_ms: int | None = Field(
        description="Duration of the check; null when the source did not answer.", examples=[84]
    )
    detail: str | None = Field(description="What failed; null when ok.", examples=[None])


class SourcesHealthOut(BaseModel):
    """Reachability of the market data sources, checked at most once a minute."""

    status: Literal["ok", "error"] = Field(description="`ok` when every source is reachable.")
    sources: dict[Source, SourceHealthOut]

    @classmethod
    def of(cls, health: Mapping[str, SourceHealth]) -> Self:
        sources = {
            name: SourceHealthOut(
                status="ok" if check.ok else "error",
                latency_ms=check.latency_ms,
                detail=check.detail,
            )
            for name, check in health.items()
        }
        ok = all(check.ok for check in health.values())
        return cls.model_validate({"status": "ok" if ok else "error", "sources": sources})
