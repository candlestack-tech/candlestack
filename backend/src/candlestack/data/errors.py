"""Errors of the data module. They know nothing about HTTP: the API maps them to problems.

Every error has ``detail``, a sentence for the user that says what went wrong and what to
change, plus the fields the API puts into the problem's extensions.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from candlestack.data.models import InstrumentId, Timeframe


def format_time(ts: int) -> str:
    """An epoch second for a message: ``2024-02-04T17:20:00Z (1707067200)``."""
    return f"{datetime.fromtimestamp(ts, UTC):%Y-%m-%dT%H:%M:%SZ} ({ts})"


class DataError(Exception):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class InvalidRequest(DataError, ValueError):
    """The request is malformed, e.g. a bad instrument id. Also a ``ValueError``, so Pydantic
    validators can raise it."""


class InstrumentNotFound(DataError):
    def __init__(self, instrument: "InstrumentId | str") -> None:
        self.instrument = str(instrument)
        super().__init__(
            f"Instrument {instrument} does not exist. Search the instruments to find a valid id."
        )


class PeriodOutOfRange(DataError):
    """The period asks for candles before the first one or after the newest closed one.

    Pass the request's ``start`` (``None`` for the last N candles) and ``end`` to get a detail
    about the side that failed.
    ``available_from`` is ``None`` when the first candle is not known.
    """

    def __init__(
        self,
        instrument: "InstrumentId | str",
        timeframe: "Timeframe | str",
        available_from: int | None,
        available_to: int,
        *,
        start: int | None = None,
        end: int | None = None,
    ) -> None:
        self.instrument = str(instrument)
        self.timeframe = str(timeframe)
        self.available_from = available_from
        self.available_to = available_to
        has = f"{instrument} has {timeframe} candles"
        sentences = []
        if start is not None and available_from is not None and start < available_from:
            sentences.append(
                f"{has} from {format_time(available_from)}. Set start to {available_from} or later."
            )
        if (
            start is None
            and end is not None
            and available_from is not None
            and end <= available_from
        ):
            sentences.append(
                f"{has} from {format_time(available_from)}. Set end after {available_from}."
            )
        if end is not None and end > available_to:
            sentences.append(
                f"{has} up to {format_time(available_to)}. Set end to {available_to} or earlier."
            )
        if not sentences:
            period = f"up to {format_time(available_to)}"
            if available_from is not None:
                period = f"from {format_time(available_from)} to {format_time(available_to)}"
            sentences.append(f"{has} {period}. Choose start and end within this period.")
        super().__init__(" ".join(sentences))


class WarmUpOutOfRange(DataError):
    """The period starts too early for the candles a model needs before it (its warm-up): the
    data has fewer of them before ``start``. ``earliest_start`` is the first start that has
    them, None when the first candle is not known."""

    def __init__(
        self,
        instrument: "InstrumentId | str",
        timeframe: "Timeframe | str",
        candles: int,
        earliest_start: int | None,
    ) -> None:
        self.instrument = str(instrument)
        self.timeframe = str(timeframe)
        self.candles = candles
        self.earliest_start = earliest_start
        detail = (
            f"The model needs {candles} {timeframe} candles of {instrument} before the start of "
            "the period, and the data has fewer."
        )
        if earliest_start is not None:
            detail += f" Set start to {format_time(earliest_start)} or later."
        super().__init__(detail)


class TooManyCandles(DataError):
    """More expected candles than allowed; ``suggestion`` comes from ``suggest()``."""

    def __init__(self, requested: int, maximum: int, suggestion: str) -> None:
        self.requested = requested
        self.maximum = maximum
        self.suggestion = suggestion
        super().__init__(
            f"Requested {requested} candles, the maximum is {maximum}. {suggestion}".rstrip()
        )


class SourceUnavailable(DataError):
    """A source is down, timed out, or our request budget for it is spent.

    ``retry_after`` (seconds) is set when waiting helps, e.g. until the budget window resets.
    ``rejected`` is set when the source refused this very request (a 4xx other than 429), so
    sending it again unchanged will not help.
    """

    def __init__(
        self,
        source: str,
        detail: str,
        *,
        retry_after: int | None = None,
        rejected: bool = False,
    ) -> None:
        self.source = source
        self.retry_after = retry_after
        self.rejected = rejected
        super().__init__(detail)


class DataIntegrityError(DataError):
    """Source data failed validation; it must not be cached or returned."""

    def __init__(self, detail: str, *, source: str | None = None) -> None:
        self.source = source
        super().__init__(detail)
