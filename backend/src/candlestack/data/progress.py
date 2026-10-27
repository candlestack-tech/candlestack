"""How far a load of candles has got, for whoever started it (a backtest shows its steps).

The caller wraps the load in ``watch_load`` with a callback; the service and the sources report
to it through a context variable, so no signature between them carries it. Tasks started
within the load (the chunks fetched at once) copy the context and so report to the same
tracker. Without ``watch_load`` nothing is tracked.
"""

import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from candlestack.data.sources.base import Period

# Patched by tests to move the clock.
_time = time.time


@dataclass(frozen=True, slots=True)
class LoadProgress:
    """A load's chunks (closed months or years, then days, as the source splits the period):
    ``total`` of them, ``loaded`` so far, ``cached`` of those from the cache (the others came
    from the source). ``until`` is where the chunks loaded from the first one on end (epoch
    seconds; None until the first is in), so it moves on only when no earlier chunk is missing.
    ``waiting_until`` is when the load goes on while it waits for the source's request budget
    or its rate limit (epoch seconds); None while it is not waiting."""

    total: int = 0
    loaded: int = 0
    cached: int = 0
    until: int | None = None
    waiting_until: float | None = None


type LoadWatcher = Callable[[LoadProgress], None]


class _Tracker:
    def __init__(self, watcher: LoadWatcher) -> None:
        self._watcher = watcher
        self._ends: list[int] = []
        self._done: set[int] = set()
        self._next = 0
        self._progress = LoadProgress()

    def _tell(self, progress: LoadProgress) -> None:
        self._progress = progress
        self._watcher(progress)

    def plan(self, periods: Sequence[Period]) -> None:
        self._ends = [period.end for period in periods]
        self._done.clear()
        self._next = 0
        self._tell(LoadProgress(total=len(periods)))

    def loaded(self, index: int, *, cached: bool) -> None:
        self._done.add(index)
        while self._next in self._done:
            self._next += 1
        until = self._ends[self._next - 1] if self._next else None
        progress = self._progress
        self._tell(
            LoadProgress(
                total=progress.total,
                loaded=len(self._done),
                cached=progress.cached + cached,
                until=until,
                waiting_until=progress.waiting_until,
            )
        )

    def waiting(self, until: float | None) -> None:
        progress = self._progress
        if progress.waiting_until == until:
            return
        self._tell(
            LoadProgress(
                total=progress.total,
                loaded=progress.loaded,
                cached=progress.cached,
                until=progress.until,
                waiting_until=until,
            )
        )


_tracker: ContextVar[_Tracker | None] = ContextVar("candlestack_data_load", default=None)


@contextmanager
def watch_load(watcher: LoadWatcher) -> Iterator[None]:
    """Calls ``watcher`` with the progress of the loads of candles within the block, each time
    it changes. It must be quick and must not raise: it runs within the load."""
    token = _tracker.set(_Tracker(watcher))
    try:
        yield
    finally:
        _tracker.reset(token)


def tracker() -> _Tracker | None:
    """The tracker of the load going on in this context; None when nobody watches it."""
    return _tracker.get()


def waiting(seconds: float | None) -> None:
    """Tells a watched load that it waits ``seconds`` from now for the source (None: it goes
    on)."""
    if (current := _tracker.get()) is not None:
        current.waiting(None if seconds is None else _time() + seconds)
