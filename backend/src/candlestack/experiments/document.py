"""The result document of a backtest (docs/engine.md, "The result document"), version 1: what
``GET /api/v1/backtests/{id}`` returns and the download is, and the groups a new backtest is
started with (``BacktestIn``), which the document gives back so that a run can be repeated.

The models document the shape and check what a client sends; the document itself is written
with orjson from the Polars columns (``assembly.py``), as the candles endpoint does, so these
models never hold the long tables.
"""

from datetime import date, datetime
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from candlestack.engine import Settings
from candlestack.experiments.schemas import BacktestStatus
from candlestack.signals import PostProcessing

VERSION = 1
# The light view of the page and the API: more series rows than this become one per UTC day,
# and only the newest trades up to this stay.
LIGHT_ROWS = 10_000
LIGHT_TRADES = 10_000
# The largest windows a run takes. Each build of a document runs the steps again, in the API
# process: ``rank`` compares a prediction with every other one of its window, and ``kmeans``
# fits on its window at every refit, which keeps the GIL. These keep a run of CANDLES_MAX rows
# to a second or two (a ``rank`` window of 5,000 takes 8 s, k-means with a window of 2,000
# refitted on every row a minute and a half).
CALIBRATION_WINDOW_MAX = 1_000
KMEANS_WINDOW_MAX = 5_000
# A k-means fit is used for at least ``window / 20`` rows.
KMEANS_WINDOW_PER_REFIT = 20

Number = Annotated[float, Field(allow_inf_nan=False)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


# --- What a new backtest is made of: the groups model, data, decision, execution, benchmarks --


class ModelRef(BaseModel):
    """The model to run; the other fields of the document's `model` group are ignored, so the
    group can be sent back as it is."""

    id: str = Field(
        description="Public ID of one of the user's models", examples=["mdl_k2m9qx47f3"]
    )


class InstrumentRef(BaseModel):
    """The instrument to run on; the document's other fields of it are ignored."""

    id: str = Field(description="`<market>:<symbol>`", examples=["stock:AAPL"])


class Period(_Strict):
    """The test period `[start, end)` in UTC epoch seconds of a candle's open."""

    start: int = Field(ge=0, description="First second of the test", examples=[1717372800])
    end: int = Field(ge=0, description="End of the test, excluded", examples=[1717459200])

    @model_validator(mode="after")
    def _moves_forward(self) -> Self:
        if self.start >= self.end:
            raise ValueError("start must be before end")
        return self


class DataIn(BaseModel):
    """One instrument and the period; the timeframe and session are the model's. The document's
    other fields of the group are ignored."""

    instrument: InstrumentRef
    period: Period


class CalibrateStep(_Strict):
    """`zscore` or `rank` over the `window` predictions ending at each one."""

    name: Literal["zscore", "rank"]
    window: int = Field(ge=2, le=CALIBRATION_WINDOW_MAX)


class EmaStep(_Strict):
    span: int = Field(ge=1, description="The span of the exponential moving average")


class ThresholdRule(_Strict):
    name: Literal["threshold"]
    limit: Number = Field(
        ge=0, description="Long at a prediction of `limit` or more, short at `-limit` or less"
    )


class HysteresisRule(_Strict):
    name: Literal["hysteresis"]
    enter: Number = Field(ge=0, description="The limit a position opens at")
    exit: Number = Field(ge=0, description="The limit it closes below, at most `enter`")

    @model_validator(mode="after")
    def _exit_within_enter(self) -> Self:
        if self.exit > self.enter:
            raise ValueError("exit must be at most enter")
        return self


class KmeansRule(_Strict):
    name: Literal["kmeans"]
    k: int = Field(ge=2, le=5, description="Groups: long in the highest, short in the lowest")
    window: int = Field(
        le=KMEANS_WINDOW_MAX, description="Earlier predictions a fit uses, at least `2 * k`"
    )
    refit_every: int = Field(
        ge=1,
        description=f"Rows a fit is used for, at least `window / {KMEANS_WINDOW_PER_REFIT}`",
    )

    @model_validator(mode="after")
    def _window_fits_the_groups(self) -> Self:
        if self.window < 2 * self.k:
            raise ValueError("window must be at least 2 * k")
        if self.window > KMEANS_WINDOW_PER_REFIT * self.refit_every:
            raise ValueError(f"refit_every must be at least window / {KMEANS_WINDOW_PER_REFIT}")
        return self


class CandlesStep(_Strict):
    candles: int = Field(ge=1)


Rule = Annotated[ThresholdRule | HysteresisRule | KmeansRule, Field(discriminator="name")]


class Decision(_Strict):
    """How the predictions become a signal: each step of docs/engine.md (Signals) in the order
    applied, null when off; exactly what the command's `run.json` records."""

    calibrate: CalibrateStep | None = None
    ema: EmaStep | None = None
    rule: Rule = ThresholdRule(name="threshold", limit=0.0)
    long_only: bool = Field(default=False, description="Flat instead of short")
    confirm: CandlesStep | None = Field(
        default=None, description="Count a value only once it held this many candles"
    )
    min_hold: CandlesStep | None = Field(
        default=None, description="Keep each new position for at least this many candles"
    )

    def post_processing(self) -> PostProcessing:
        """The steps as ``signals.make_signal`` takes them."""
        rule = self.rule
        return PostProcessing(
            calibrate=None if self.calibrate is None else self.calibrate.name,
            calibration_window=None if self.calibrate is None else self.calibrate.window,
            ema=None if self.ema is None else self.ema.span,
            rule=rule.name,
            threshold=_limit(rule),
            exit=rule.exit if isinstance(rule, HysteresisRule) else None,
            k=rule.k if isinstance(rule, KmeansRule) else None,
            kmeans_window=rule.window if isinstance(rule, KmeansRule) else None,
            refit_every=rule.refit_every if isinstance(rule, KmeansRule) else None,
            long_only=self.long_only,
            confirm=None if self.confirm is None else self.confirm.candles,
            min_hold=None if self.min_hold is None else self.min_hold.candles,
        )


def _limit(rule: ThresholdRule | HysteresisRule | KmeansRule) -> float | None:
    if isinstance(rule, ThresholdRule):
        return rule.limit
    if isinstance(rule, HysteresisRule):
        return rule.enter
    return None


class Execution(Settings):
    """How the engine fills orders and what they cost: the engine's `Settings`
    (docs/engine.md, Settings). Fractions: 0.05 is 5%; a basis point is 0.01%."""


class BenchmarkIn(_Strict):
    """What the run is compared with. Version 1 has buy and hold of the run's instrument only."""

    id: str = Field(pattern=r"^[a-z0-9_-]{1,16}$", examples=["bh"])
    kind: Literal["buy_and_hold"]
    instrument: str = Field(description="The run's instrument", examples=["stock:AAPL"])


class BacktestIn(BaseModel):
    """A new backtest: the setup groups of the result document. The document of a backtest can
    be sent back as it is to run it again; its other groups are ignored."""

    model: ModelRef
    data: DataIn
    decision: Decision = Decision()
    execution: Execution = Execution()
    benchmarks: list[BenchmarkIn] = Field(
        default_factory=list,
        max_length=1,
        description="Buy and hold of the instrument, or nothing",
    )


# --- The document -------------------------------------------------------------------------


StepId = Literal["candles", "queue", "model", "trades"]
StepState = Literal["pending", "running", "done", "failed"]


class BacktestStep(BaseModel):
    """A step of the run. `candles` loads the candles with the warm-up, `queue` hands the job
    to the worker until a worker takes it, `model` is the worker's job (it loads the model,
    builds its input and predicts), `trades` runs the engine and the statistics and stores the
    result. The fields after `duration_s` belong to one step each and are null for the others.
    """

    # Every field is in every step (null when it does not apply).
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    id: StepId
    state: StepState = Field(
        description="`pending` until it begins (a step after a failed one stays `pending`), "
        "`running`, `done` or `failed`"
    )
    started_at: datetime | None = Field(
        default=None, description="When it began (UTC); null while pending or when unknown"
    )
    duration_s: float | None = Field(
        default=None,
        description="Seconds it took, once done or failed; null while it runs, or when it is "
        "not known",
        examples=[192.4],
    )
    loaded: int | None = Field(
        default=None,
        description="`candles`: the chunks loaded so far (closed months or years, then days, "
        "as the source splits the period); null until the load has planned them",
        examples=[37],
    )
    total: int | None = Field(
        default=None, description="`candles`: the chunks of the load", examples=[128]
    )
    cached: int | None = Field(
        default=None,
        description="`candles`: of the chunks loaded, those that came from the cache",
        examples=[3],
    )
    loaded_until: date | None = Field(
        default=None,
        description="`candles`: the last day of the candles loaded so far from the start of "
        "the load on (UTC)",
    )
    candles: int | None = Field(
        default=None, description="`candles`, once done: the candles loaded", examples=[35912]
    )
    from_cache: bool | None = Field(
        default=None, description="`candles`, once done: every chunk came from the cache"
    )
    waiting_until: datetime | None = Field(
        default=None,
        description="`candles`: while the load waits for the market data source's request "
        "limit, when it goes on (UTC); else null",
    )
    load_s: float | None = Field(
        default=None, description="`model`, once done: seconds to load the model", examples=[1.2]
    )
    input_s: float | None = Field(
        default=None,
        description="`model`, once done: seconds to build the model's input",
        examples=[0.4],
    )
    predict_s: float | None = Field(
        default=None, description="`model`, once done: seconds to predict", examples=[6.1]
    )


class BacktestHeader(BaseModel):
    id: str = Field(examples=["bt_7f3k2m9qx4"])
    number: int = Field(ge=1, description="The user's own number, shown as #N", examples=[12])
    status: BacktestStatus
    created_at: datetime = Field(description="When it started (UTC)")
    finished_at: datetime | None = Field(description="When it finished or failed (UTC)")
    duration_s: float | None = Field(description="Seconds it took", examples=[162.0])
    error: str | None = Field(description="What went wrong and what to do, for a failed one")
    steps: list[BacktestStep] = Field(
        description="The steps of the run in their order, while it runs and after: the "
        "failed one of a failed backtest is `failed` and its error is `error`. Empty for a "
        "backtest that failed before steps were recorded"
    )


class RunnerOut(BaseModel):
    """What ran the model; null until the worker answered."""

    format: str = Field(examples=["keras"])
    keras: str | None = Field(examples=["3.15.1"])
    backend: str | None = Field(description="The backend and its release", examples=["jax 0.7.2"])


class Software(BaseModel):
    engine_version: str = Field(examples=["0.3.0"])
    app_version: str = Field(examples=["2026.10.24-3f2a1c9"])
    runner: RunnerOut | None


class BacktestModel(BaseModel):
    id: str | None = Field(
        description="Null once the model is deleted", examples=["mdl_k2m9qx47f3"]
    )
    name: str = Field(description="The file name when it ran", examples=["raw-candles.keras"])
    framework: str = Field(examples=["keras"])
    sha256: str = Field(description="Of the model file, hex", examples=["9b1e04c7" + "0" * 56])
    size_bytes: int = Field(examples=[48213])
    manifest: dict[str, Any] = Field(description="The manifest it ran with, as stored with the run")


class InstrumentOut(BaseModel):
    id: str = Field(examples=["stock:AAPL"])
    market: str = Field(examples=["stock"])
    symbol: str = Field(examples=["AAPL"])
    name: str = Field(examples=["Apple Inc."])
    exchange: str | None = Field(examples=["NASDAQ"])
    source: str = Field(examples=["alpaca"])
    feed: str = Field(examples=["sip"])
    currency: str = Field(description="What the prices are in", examples=["USD"])


class Loaded(BaseModel):
    """The candles asked for, warm-up included, with their fingerprint (docs/data.md)."""

    start: int
    end: int
    count: int
    fingerprint: str = Field(examples=["sha256:3b1f" + "0" * 60])
    gaps_total: int


class WarmUpReport(BaseModel):
    candles: int = Field(description="Candles loaded before the period for the warm-up")
    kind: Literal["exact", "estimate"]
    dropped: int | None = Field(
        description="The candles the model's input dropped at the start; null until it ran"
    )


class Rows(BaseModel):
    """What the engine ran on."""

    first_ts: int
    last_ts: int
    count: int


class PredictionsOut(BaseModel):
    count: int
    sha256: str = Field(description="Of the stored predictions (Parquet), hex")


class AlignOut(BaseModel):
    predictions_without_candle: int
    candles_without_prediction: int


class DataOut(BaseModel):
    """The data a run used. `loaded`, `changed`, `rows`, `predictions` and `align` are null
    until it finished."""

    instrument: InstrumentOut
    timeframe: str = Field(examples=["30m"])
    timeframe_seconds: int = Field(examples=[1800])
    session: str = Field(examples=["regular"])
    period: Period
    loaded: Loaded | None
    changed: bool | None = Field(
        description="The candles' fingerprint differs from the run's: the source changed them"
    )
    warm_up: WarmUpReport
    rows: Rows | None
    predictions: PredictionsOut | None
    align: AlignOut | None


class BenchmarkOut(BaseModel):
    id: str = Field(examples=["bh"])
    kind: str = Field(description="`buy_and_hold`; later more", examples=["buy_and_hold"])
    instrument: str = Field(examples=["stock:AAPL"])


class MetricsArgs(BaseModel):
    """The arguments the statistics were computed with."""

    periods_per_year: int = Field(description="252 for stocks, 365 for crypto", examples=[252])
    risk_free_rate: float = Field(examples=[0.0])


class Stats(BaseModel):
    """The 12 statistics of docs/engine.md (Metrics), in their order. Fractions; `max_drawdown`
    negative; null when undefined."""

    total_return: float | None
    max_drawdown: float | None
    sharpe: float | None
    sortino: float | None
    num_trades: int
    win_rate: float | None
    avg_trade_return: float | None
    best_trade_return: float | None
    worst_trade_return: float | None
    exposure: float | None
    fees_paid: float
    buy_and_hold_return: float | None


class BenchmarkResult(BaseModel):
    total_return: float | None


class SeriesColumns(BaseModel):
    """One value per row in each column. `prediction` and `signal` are left out of a series of
    days."""

    ts: list[int]
    close: list[float]
    prediction: list[float] | None = None
    signal: list[float] | None = None
    position: list[float]
    equity: list[float]
    drawdown: list[float]


class Series(BaseModel):
    resolution: str = Field(
        description=(
            f"`candle`, or `day` when the light view made one point per UTC day of more than "
            f"{LIGHT_ROWS} rows"
        ),
        examples=["candle"],
    )
    count: int
    columns: SeriesColumns
    benchmarks: dict[str, list[float]] = Field(
        description="Each benchmark's equity on the same rows, by its ID"
    )


class TradeColumns(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    signal_ts: list[int]
    open_ts: list[int]
    close_ts: list[int]
    side: list[int]
    open_price: list[float]
    close_price: list[float]
    stop_price: list[float | None]
    exit_reason: list[str] = Field(description="`signal`, `stop`, `gap`, `end`, `ruin`, `drawdown`")
    return_: list[float] = Field(alias="return")
    fees: list[float]
    slippage_cost: list[float]
    candles_held: list[int] = Field(description="Rows from the entry candle to the exit candle")


class Trades(BaseModel):
    count: int = Field(description="The trades in the document")
    total: int = Field(description="The trades the run had")
    complete: bool = Field(description=f"False when the light view kept the newest {LIGHT_TRADES}")
    columns: TradeColumns


class Results(BaseModel):
    stats: Stats
    benchmarks: dict[str, BenchmarkResult]
    series: Series
    trades: Trades


class BacktestDocument(BaseModel):
    """A backtest as the app shows it, the API returns it and a user downloads it
    (docs/engine.md, "The result document"). Market time is UTC epoch seconds of a candle's
    open, ranges `[start, end)`; returns are fractions."""

    model_config = ConfigDict(title="CandleStack backtest")

    version: Literal[1] = Field(description="The format's version")
    backtest: BacktestHeader
    software: Software
    model: BacktestModel
    data: DataOut
    decision: Decision
    execution: Execution
    benchmarks: list[BenchmarkOut]
    metrics: MetricsArgs
    results: Results | None = Field(description="Null unless it finished")
