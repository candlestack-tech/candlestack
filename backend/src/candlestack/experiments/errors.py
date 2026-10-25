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


class BacktestRunning(Exception):
    """The user has a backtest running already."""


class BacktestsUnavailable(Exception):
    """The worker's queue cannot be reached, so no backtest can run now."""


class BacktestNotFinished(Exception):
    """The backtest is running or failed: it has no results."""

    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


class NoDocument(Exception):
    """The backtest was not made in the app (the seed command's test data): it has no result
    document."""
