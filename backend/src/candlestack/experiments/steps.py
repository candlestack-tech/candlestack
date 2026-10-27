"""The steps of a backtest's run (``document.BacktestStep``), stored in ``backtests.steps`` as
the run goes, so its page shows where it is and, once it ended, how long each step took or
where it failed.

The runner records them with a ``StepRecorder``, which saves them at each change of step and,
while the candles load, their progress at most once every ``PROGRESS_SECONDS``. A failure
marks the step that was running failed (``failed``), whatever failed the run: the runner's
own error, a bug or a restart of the server. Backtests that finished before steps were
recorded show them from their report's timings (``shown_steps``).
"""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from candlestack.data import LoadProgress, watch_load
from candlestack.experiments.document import BacktestStep, StepId
from candlestack.jobs import Timings

STEPS: tuple[StepId, ...] = ("candles", "queue", "model", "trades")
# The most often the progress of the candles is saved: the page asks every 2 seconds.
PROGRESS_SECONDS = 1.0

type Steps = list[dict[str, Any]]
type Save = Callable[[Steps], Awaitable[None]]


def _now() -> datetime:
    return datetime.now(UTC)


def _seconds(milliseconds: float | None) -> float | None:
    return None if milliseconds is None else round(milliseconds / 1000, 3)


def _dump(steps: list[BacktestStep]) -> Steps:
    return [step.model_dump(mode="json") for step in steps]


def pending() -> Steps:
    """The steps of a run that has not begun."""
    return _dump([BacktestStep(id=step, state="pending") for step in STEPS])


def failed(steps: Steps | None, now: datetime) -> Steps | None:
    """The stored steps with the one that was running failed at ``now``; the first pending one
    when none was running (the run failed before it began a step). None without steps."""
    if steps is None:
        return None
    parsed = [BacktestStep.model_validate(step) for step in steps]
    at = next((step for step in parsed if step.state == "running"), None)
    at = at or next((step for step in parsed if step.state == "pending"), None)
    if at is not None:
        took = None if at.started_at is None else max((now - at.started_at).total_seconds(), 0)
        parsed[parsed.index(at)] = at.model_copy(
            update={"state": "failed", "duration_s": took, "waiting_until": None}
        )
    return _dump(parsed)


def shown_steps(
    steps: Steps | None, timings: Mapping[str, float | None] | None
) -> list[BacktestStep]:
    """The steps a backtest shows: those stored, else for a finished backtest those its
    report's timings (milliseconds) tell, all done; else none.

    The timings have no start of a step, and the time from taking the job to its result
    (``job_total``) stands for the model's step."""
    if steps:
        return [BacktestStep.model_validate(step) for step in steps]
    if timings is None:
        return []

    def took(name: str) -> float | None:
        return _seconds(timings.get(name))

    return [
        BacktestStep(id="candles", state="done", duration_s=took("candles")),
        BacktestStep(id="queue", state="done", duration_s=took("job_queued")),
        BacktestStep(
            id="model",
            state="done",
            duration_s=took("job_total"),
            load_s=took("job_load"),
            input_s=took("job_input"),
            predict_s=took("job_predict"),
        ),
        BacktestStep(id="trades", state="done", duration_s=took("backtest")),
    ]


class StepRecorder:
    """The steps of one run as it goes. ``save`` stores them; it must not raise (a step that
    cannot be saved never fails the run). ``period_end`` (epoch seconds, excluded) bounds the
    day the candles show as loaded."""

    def __init__(self, save: Save, period_end: int, *, every: float = PROGRESS_SECONDS) -> None:
        self._steps = {step: BacktestStep(id=step, state="pending") for step in STEPS}
        self._clocks: dict[StepId, float] = {}
        self._save = save
        self._period_end = period_end
        self._every = every
        self._changed = False

    def dump(self) -> Steps:
        return _dump(list(self._steps.values()))

    def _set(self, step: StepId, **fields: Any) -> None:
        self._steps[step] = self._steps[step].model_copy(update=fields)

    def _took(self, step: StepId) -> float:
        return round(time.monotonic() - self._clocks[step], 3)

    async def _write(self) -> None:
        self._changed = False
        await self._save(self.dump())

    async def start(self, step: StepId) -> None:
        self._clocks[step] = time.monotonic()
        self._set(step, state="running", started_at=_now())
        await self._write()

    async def loaded(self, candles: int) -> None:
        """The candles' step done, with how many candles it loaded."""
        step = self._steps["candles"]
        cached = None if step.total is None else step.cached == step.total
        self._set(
            "candles",
            state="done",
            duration_s=self._took("candles"),
            candles=candles,
            from_cache=cached,
            waiting_until=None,
        )
        await self._write()

    def progress(self, load: LoadProgress) -> None:
        """The progress of the candles' load (a ``data.LoadWatcher``), saved by ``loading``."""
        until = None
        if load.until is not None:
            until = datetime.fromtimestamp(min(load.until, self._period_end) - 1, UTC).date()
        waiting = load.waiting_until
        self._set(
            "candles",
            loaded=load.loaded,
            total=load.total,
            cached=load.cached,
            loaded_until=until,
            waiting_until=None if waiting is None else datetime.fromtimestamp(waiting, UTC),
        )
        self._changed = True

    @asynccontextmanager
    async def loading(self) -> AsyncIterator[None]:
        """Watches the loads of candles within the block, and saves their progress when it
        changed, at most once every ``every`` seconds."""
        stop = asyncio.Event()
        flusher = asyncio.create_task(self._flush(stop))
        try:
            with watch_load(self.progress):
                yield
        finally:
            # A save going on ends first: none is cut off, and none comes after the block.
            stop.set()
            await flusher

    async def _flush(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                async with asyncio.timeout(self._every):
                    await stop.wait()
            except TimeoutError:
                if self._changed:
                    await self._write()

    async def claimed(self) -> None:
        """A worker took the job: the queue's step is done and the model's begins."""
        if self._steps["queue"].state != "running":
            return
        self._set("queue", state="done", duration_s=self._took("queue"))
        await self.start("model")

    async def answered(self, timings: Timings) -> None:
        """The job's result came: the worker's own measure of its wait in the queue (from the
        job's making to its claim) replaces what the backend saw, which is up to a second late
        or missing when the job ran within that second. Without it the steps stay as they are,
        so a job no worker took fails the queue's step."""
        if timings.queued is None:
            return
        queued = timings.queued / 1000
        began = self._steps["queue"].started_at
        assert began is not None  # the runner began the queue's step before it sent the job
        self._set("queue", state="done", duration_s=round(queued, 3))
        self._clocks["model"] = self._clocks["queue"] + queued
        self._set("model", state="running", started_at=began + timedelta(seconds=queued))
        await self._write()

    async def ran(self, timings: Timings) -> None:
        """The model's step done, with the worker's times of its parts."""
        took = None
        if self._steps["model"].state == "running":
            took = self._took("model")
        else:
            # A worker took the job unseen and did not say when (the worker always does):
            # both steps are done, and how long each took is not known.
            self._set("queue", state="done")
        self._set(
            "model",
            state="done",
            duration_s=took,
            load_s=_seconds(timings.load),
            input_s=_seconds(timings.input),
            predict_s=_seconds(timings.predict),
        )
        await self._write()

    def finished(self) -> Steps:
        """The steps of a run whose result is being stored, all done: stored with it."""
        self._set("trades", state="done", duration_s=self._took("trades"))
        return self.dump()
