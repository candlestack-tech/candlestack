"""The steps of a backtest's run: what the recorder saves as the run goes, the progress of the
candles saved at most once a period, the step a failure marks, and the steps of backtests that
finished before steps were recorded. The runs themselves are integration tests
(tests/integration/experiments/test_runs.py)."""

import asyncio
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from candlestack.data import LoadProgress
from candlestack.experiments.document import BacktestStep
from candlestack.experiments.steps import (
    STEPS,
    StepRecorder,
    Steps,
    failed,
    pending,
    shown_steps,
)
from candlestack.jobs import Timings

pytestmark = pytest.mark.anyio

DAY = 86_400
PERIOD_END = 1_717_459_200  # 2024-06-04, excluded
NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)


class Saved:
    """The saves of a recorder, each the steps by their ID."""

    def __init__(self) -> None:
        self.saves: list[dict[str, dict[str, Any]]] = []

    async def __call__(self, steps: Steps) -> None:
        assert [step["id"] for step in steps] == list(STEPS)
        self.saves.append({step["id"]: step for step in steps})

    @property
    def last(self) -> dict[str, dict[str, Any]]:
        return self.saves[-1]


def recorder(every: float = 60) -> tuple[StepRecorder, Saved]:
    saved = Saved()
    return StepRecorder(saved, PERIOD_END, every=every), saved


def states(steps: dict[str, dict[str, Any]] | Steps) -> list[str]:
    values = steps.values() if isinstance(steps, dict) else steps
    return [step["state"] for step in values]


def test_a_new_run_has_its_steps_pending() -> None:
    steps = pending()

    assert [(step["id"], step["state"]) for step in steps] == [
        ("candles", "pending"),
        ("queue", "pending"),
        ("model", "pending"),
        ("trades", "pending"),
    ]
    assert all(step["duration_s"] is None for step in steps)


async def test_a_run_saves_each_step_as_it_begins_and_ends() -> None:
    steps, saved = recorder()

    await steps.start("candles")
    assert states(saved.last) == ["running", "pending", "pending", "pending"]
    await steps.loaded(264)
    await steps.start("queue")
    await steps.claimed()
    assert states(saved.last) == ["done", "done", "running", "pending"]
    await steps.answered(Timings(queued=None))  # the worker said nothing of its wait
    await steps.ran(Timings(load=1200.0, input=100.0, predict=512.3))
    await steps.start("trades")
    finished = steps.finished()

    assert states(finished) == ["done"] * 4
    candles, queue, model, trades = finished
    assert (candles["candles"], candles["from_cache"]) == (264, None)  # no progress was told
    assert candles["duration_s"] >= 0
    assert queue["duration_s"] >= 0
    assert (model["load_s"], model["input_s"], model["predict_s"]) == (1.2, 0.1, 0.512)
    assert trades["duration_s"] >= 0
    assert all(step["started_at"] is not None for step in finished)
    # The steps are stored with the result, not saved on their own.
    assert states(saved.last) == ["done", "done", "done", "running"]


async def test_the_progress_of_the_candles_shows_how_far_the_load_got() -> None:
    steps, _ = recorder()
    await steps.start("candles")

    steps.progress(
        LoadProgress(total=4, loaded=2, cached=1, until=PERIOD_END - DAY, waiting_until=1e9)
    )
    loading = BacktestStep.model_validate(steps.dump()[0])
    # A chunk that ends after the period shows the period's last day.
    steps.progress(LoadProgress(total=4, loaded=4, cached=4, until=PERIOD_END + 27 * DAY))
    await steps.loaded(29)
    done = BacktestStep.model_validate(steps.dump()[0])

    assert (loading.loaded, loading.total, loading.cached) == (2, 4, 1)
    assert loading.loaded_until == date(2024, 6, 2)
    assert loading.waiting_until == datetime.fromtimestamp(1e9, UTC)
    assert done.state == "done"
    assert (done.loaded, done.total, done.candles) == (4, 4, 29)
    assert done.loaded_until == date(2024, 6, 3)
    assert done.from_cache is True
    assert done.waiting_until is None


async def test_a_load_from_the_source_is_not_from_the_cache() -> None:
    steps, saved = recorder()
    await steps.start("candles")

    steps.progress(LoadProgress(total=2, loaded=2, cached=1, until=PERIOD_END))
    await steps.loaded(29)

    assert saved.last["candles"]["from_cache"] is False
    assert saved.last["candles"]["loaded_until"] == "2024-06-03"


async def test_the_progress_is_saved_at_most_once_a_period() -> None:
    steps, saved = recorder(every=0.05)
    await steps.start("candles")
    before = len(saved.saves)
    began = time.monotonic()

    async with steps.loading():
        for loaded in range(1, 11):
            steps.progress(LoadProgress(total=10, loaded=loaded))
            await asyncio.sleep(0.012)  # ten changes in about 0.12 s
        changing = time.monotonic() - began
        await asyncio.sleep(0.12)  # nothing changes: nothing is saved
    after_block = len(saved.saves)
    await asyncio.sleep(0.1)

    progress_saves = after_block - before
    assert 1 <= progress_saves <= changing / 0.05 + 1
    assert saved.last["candles"]["loaded"] >= 1
    assert len(saved.saves) == after_block  # none after the block


async def test_a_load_that_fails_stops_saving_its_progress() -> None:
    steps, saved = recorder(every=0.02)
    await steps.start("candles")

    async def load() -> None:
        async with steps.loading():
            steps.progress(LoadProgress(total=3, loaded=1))
            raise RuntimeError

    with pytest.raises(RuntimeError):
        await load()
    count = len(saved.saves)
    await asyncio.sleep(0.06)

    assert len(saved.saves) == count


async def test_the_workers_wait_in_the_queue_replaces_what_the_backend_saw() -> None:
    steps, saved = recorder()
    await steps.start("queue")
    began = datetime.fromisoformat(saved.last["queue"]["started_at"])

    # The job ran within a second, so the backend never saw the worker take it.
    await steps.answered(Timings(queued=2500.0))
    await steps.ran(Timings(load=1.0))

    queue, model = saved.last["queue"], saved.last["model"]
    assert (queue["state"], queue["duration_s"]) == ("done", 2.5)
    assert model["state"] == "done"
    assert datetime.fromisoformat(model["started_at"]) == began + timedelta(seconds=2.5)
    assert model["duration_s"] is not None


async def test_a_claim_after_the_queue_changes_nothing() -> None:
    steps, saved = recorder()
    await steps.start("queue")
    await steps.claimed()
    count = len(saved.saves)

    await steps.claimed()

    assert len(saved.saves) == count


async def test_a_job_taken_unseen_without_its_wait_has_no_times() -> None:
    steps, saved = recorder()
    await steps.start("queue")

    await steps.answered(Timings())
    await steps.ran(Timings())

    assert states(saved.last)[1:3] == ["done", "done"]
    assert saved.last["queue"]["duration_s"] is None
    assert saved.last["model"]["duration_s"] is None


def stored(*running: str, started: datetime | None = NOW) -> Steps:
    """Steps with ``running`` running and those before it done."""
    steps = []
    for step in STEPS:
        if step in running:
            steps.append(BacktestStep(id=step, state="running", started_at=started))
        elif running and STEPS.index(step) < STEPS.index(running[0]):
            steps.append(BacktestStep(id=step, state="done", started_at=started, duration_s=1.0))
        else:
            steps.append(BacktestStep(id=step, state="pending"))
    return [step.model_dump(mode="json") for step in steps]


@pytest.mark.parametrize("at", STEPS)
def test_a_failure_marks_the_step_that_was_running(at: str) -> None:
    running = stored(at)
    running[STEPS.index(at)]["waiting_until"] = NOW.isoformat()

    marked = failed(running, NOW + timedelta(seconds=7.5))

    assert marked is not None
    index = STEPS.index(at)
    assert states(marked) == ["done"] * index + ["failed"] + ["pending"] * (3 - index)
    assert marked[index]["duration_s"] == 7.5
    assert marked[index]["waiting_until"] is None


def test_a_failure_before_the_first_step_marks_it() -> None:
    marked = failed(pending(), NOW)

    assert marked is not None
    assert states(marked) == ["failed", "pending", "pending", "pending"]
    assert marked[0]["duration_s"] is None


def test_a_failure_of_a_backtest_without_steps_has_none() -> None:
    assert failed(None, NOW) is None


def test_a_failure_after_every_step_changes_none() -> None:
    done = [
        BacktestStep(id=step, state="done", duration_s=1.0).model_dump(mode="json")
        for step in STEPS
    ]

    assert failed(done, NOW) == done


def test_stored_steps_are_shown_as_they_are() -> None:
    steps = stored("model")

    assert [step.model_dump(mode="json") for step in shown_steps(steps, {"candles": 1.0})] == steps


def test_a_backtest_finished_before_steps_shows_them_from_its_timings() -> None:
    timings: dict[str, float | None] = {
        "candles": 157_000.0,
        "job_queued": 1000.0,
        "job_load": 1200.0,
        "job_input": 100.0,
        "job_predict": 500.0,
        "job_total": 3000.0,
        "backtest": 1000.0,
    }

    candles, queue, model, trades = shown_steps(None, timings)

    assert [step.state for step in (candles, queue, model, trades)] == ["done"] * 4
    assert (candles.duration_s, queue.duration_s, trades.duration_s) == (157.0, 1.0, 1.0)
    assert (model.duration_s, model.load_s, model.input_s, model.predict_s) == (3.0, 1.2, 0.1, 0.5)
    assert candles.started_at is None
    # A timing the run did not record shows no time.
    assert shown_steps([], {"candles": 5.0})[1].duration_s is None


def test_a_backtest_without_steps_or_timings_shows_none() -> None:
    assert shown_steps(None, None) == []
