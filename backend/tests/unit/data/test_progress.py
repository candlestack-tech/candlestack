"""The progress of a watched load of candles: its chunks as they come in any order, where the
loaded span ends, and the waits for the source."""

import asyncio

import pytest

from candlestack.data import LoadProgress, watch_load
from candlestack.data import progress as progress_module
from candlestack.data.progress import tracker, waiting
from candlestack.data.sources.base import Period

DAY = 86_400
PERIODS = [Period("closed", n * DAY, (n + 1) * DAY, f"day-{n}", 60) for n in range(3)]


def test_without_a_watcher_nothing_is_tracked() -> None:
    waiting(30)  # a source's wait outside a watched load

    assert tracker() is None


def test_the_span_moves_on_only_when_no_earlier_chunk_is_missing() -> None:
    seen: list[LoadProgress] = []

    with watch_load(seen.append):
        load = tracker()
        assert load is not None
        load.plan(PERIODS)
        load.loaded(1, cached=True)
        load.loaded(0, cached=False)
        load.loaded(2, cached=True)

    assert seen == [
        LoadProgress(total=3),
        LoadProgress(total=3, loaded=1, cached=1, until=None),
        LoadProgress(total=3, loaded=2, cached=1, until=2 * DAY),
        LoadProgress(total=3, loaded=3, cached=2, until=3 * DAY),
    ]
    assert tracker() is None


def test_a_wait_is_told_once_until_it_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(progress_module, "_time", lambda: 1000.0)
    seen: list[LoadProgress] = []

    with watch_load(seen.append):
        load = tracker()
        assert load is not None
        load.plan(PERIODS[:1])
        waiting(24)
        waiting(24)  # another chunk waits as long
        waiting(None)
        waiting(None)

    assert [progress.waiting_until for progress in seen] == [None, 1024.0, None]


@pytest.mark.anyio
async def test_tasks_of_the_load_report_to_its_tracker() -> None:
    seen: list[LoadProgress] = []

    async def chunk(index: int) -> None:
        load = tracker()
        assert load is not None
        load.loaded(index, cached=False)

    with watch_load(seen.append):
        load = tracker()
        assert load is not None
        load.plan(PERIODS)
        await asyncio.gather(*(chunk(index) for index in range(3)))

    assert seen[-1] == LoadProgress(total=3, loaded=3, cached=0, until=3 * DAY)
