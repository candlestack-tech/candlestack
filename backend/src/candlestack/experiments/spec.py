"""What a backtest made in the app stores of its run, in the JSONB columns ``spec`` and
``report`` of ``backtests``: what it was started with and what the run found. The result
document is built from them (``runs.py``); they are read through these models, as the
preferences are, so a field added later reads as its default."""

from pydantic import BaseModel, Field

from candlestack.experiments.document import (
    AlignOut,
    BenchmarkIn,
    Decision,
    Execution,
    InstrumentOut,
    Loaded,
    MetricsArgs,
    Period,
    PredictionsOut,
    Rows,
    RunnerOut,
)


class SpecModel(BaseModel):
    """The model when the backtest started: its file and a copy of its manifest, so a later
    change to the model never changes what the backtest means."""

    name: str
    framework: str
    sha256: str
    size_bytes: int
    manifest: dict[str, object]


class SpecWarmUp(BaseModel):
    candles: int
    kind: str


class SpecData(BaseModel):
    """The instrument (as the catalog had it), the model's timeframe and session, the user's
    period and where the load with the warm-up starts."""

    instrument: InstrumentOut
    timeframe: str
    timeframe_seconds: int
    session: str
    period: Period
    load_start: int
    warm_up: SpecWarmUp


class RunSpec(BaseModel):
    """The setup groups of a backtest, the copy of the model's manifest included."""

    model: SpecModel
    data: SpecData
    decision: Decision
    execution: Execution
    benchmarks: list[BenchmarkIn]
    metrics: MetricsArgs
    app_version: str


class RunReport(BaseModel):
    """What the run found: the candles it loaded, the warm-up the model's input dropped, the
    rows the engine ran on, the stored predictions, the alignment, what ran the model and how
    long each step took (milliseconds, None for one it did not reach)."""

    loaded: Loaded
    dropped: int | None
    rows: Rows
    predictions: PredictionsOut
    align: AlignOut
    runner: RunnerOut | None
    timings: dict[str, float | None] = Field(default_factory=dict)
