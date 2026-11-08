"""Errors of the backtests service. They know nothing about HTTP: the API turns each into its
problem."""


class BacktestNotFound(Exception):
    """The user has no backtest with the ID (another user's backtest included)."""


class UnknownUser(Exception):
    """No user has the email (the seed command)."""


class ModelNotFound(Exception):
    """The user has no model with the ID (another user's model included)."""


class ModelNeedsSetup(Exception):
    """The model has no manifest yet, so its input cannot be built."""


class InstrumentNotForModel(Exception):
    """The instrument trades in other hours than the candles the model reads (a stock model
    on a crypto pair, or the other way round)."""

    def __init__(self, session: str, market: str) -> None:
        super().__init__(session, market)
        self.session = session
        self.market = market


class InvalidBacktest(Exception):
    """The setup of a new backtest does not hold together; ``detail`` says what to change."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class QueueFull(Exception):
    """The user has as many backtests waiting in their queue as it holds."""


class BacktestNotQueued(Exception):
    """The backtest runs or has ended, so it cannot be cancelled."""

    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


class BacktestsUnavailable(Exception):
    """The worker's queue cannot be reached, so no backtest can run now."""


class BacktestNotFinished(Exception):
    """The backtest is queued, running or failed: it has no results."""

    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


class NameTaken(Exception):
    """Another backtest of the user has the name, as its own or its generated name, in any
    case; ``name`` is what that backtest shows and ``number`` its number."""

    def __init__(self, name: str, number: int) -> None:
        super().__init__(name, number)
        self.name = name
        self.number = number


class NoDocument(Exception):
    """The backtest was not made in the app (the seed command's test data): it has no result
    document."""


class NotComparable(Exception):
    """A backtest of a comparison: neither the user's nor published (``found`` False), or the
    seed command's test data without a result document (``found`` True)."""

    def __init__(self, backtest_id: str, *, found: bool) -> None:
        super().__init__(backtest_id, found)
        self.backtest_id = backtest_id
        self.found = found


class SweepNotFound(Exception):
    """The user has no sweep with the ID (another user's sweep included)."""


class SweepTooLarge(Exception):
    """The grid of a sweep makes more backtests than a sweep runs; ``count`` is how many."""

    def __init__(self, count: int) -> None:
        super().__init__(count)
        self.count = count


class SweepQueueFull(Exception):
    """The user's queue has room for fewer backtests than the sweep's ``count``: ``room``."""

    def __init__(self, count: int, room: int) -> None:
        super().__init__(count, room)
        self.count = count
        self.room = room


class SetupFailed(Exception):
    """A setup of a sweep's grid failed a check of a new backtest: ``error``, of the setup at
    ``index`` (from 0, in the grid's order)."""

    def __init__(self, index: int, error: Exception) -> None:
        super().__init__(index, error)
        self.index = index
        self.error = error


class BacktestLimitReached(Exception):
    """The user has ``count`` backtests, and ``adding`` more would pass the most an account
    keeps (``limit``)."""

    def __init__(self, limit: int, count: int, adding: int = 1) -> None:
        super().__init__(limit, count, adding)
        self.limit = limit
        self.count = count
        self.adding = adding


class CountMismatch(Exception):
    """The confirmation of a delete names another number of backtests than ``count``, how
    many match."""

    def __init__(self, count: int) -> None:
        super().__init__(count)
        self.count = count
