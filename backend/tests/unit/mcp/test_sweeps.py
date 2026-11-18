"""The progress a wait for a sweep reports: its backtests that ended, failed ones included, and
how far each running one is by its steps."""

from types import SimpleNamespace

from candlestack.experiments import SweepProgress
from candlestack.mcp.sweeps import sweep_progress


def steps(*states: str) -> list[SimpleNamespace]:
    ids = ["candles", "queue", "model", "trades"]
    return [
        SimpleNamespace(id=step_id, state=state, loaded=None, total=None)
        for step_id, state in zip(ids, states, strict=True)
    ]


def test_ended_backtests_count_whole_failed_ones_too() -> None:
    counts = SweepProgress(queued=2, running=0, finished=1, failed=2)

    assert sweep_progress(counts, []) == (
        3,
        5,
        "3 of 5 ended: 1 finished, 2 failed, 0 running, 2 queued",
    )


def test_a_running_backtest_counts_by_its_steps() -> None:
    counts = SweepProgress(queued=1, running=2, finished=0, failed=1)
    running = [steps("done", "done", "running", "pending"), steps("running", *["pending"] * 3)]

    value, total, message = sweep_progress(counts, running)

    # The failed one, then 0.6 and 0.2 of the two running ones.
    assert (value, total) == (1.8, 4)
    assert message == "1 of 4 ended: 0 finished, 1 failed, 2 running, 1 queued"
