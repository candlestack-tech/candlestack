"""The table of the users' backtests. Changed only through migrations (``backend/migrations``)."""

import secrets
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    false,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from candlestack.core import Base
from candlestack.experiments.schemas import MODEL_NAME_MAX, EquitySeriesIn

# The alphabet of the user and model IDs (lowercase RFC 4648 base32: no 0, 1, 8 or 9), so the
# IDs of the app read alike.
_ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"
_ID_LENGTH = 10


def new_backtest_id() -> str:
    """A public backtest ID such as ``bt_7f3k2m9qx4`` (50 random bits)."""
    return "bt_" + "".join(secrets.choice(_ID_ALPHABET) for _ in range(_ID_LENGTH))


class Backtest(Base):
    """A backtest of a user: what it ran on and with, and its results. The results are
    fractions (0.246 is 24.6 %), as ``metrics.stats`` gives them, except ``max_drawdown``,
    which is stored as the fall it is: negative. They are null until it finished, and for a
    failed one, which has its ``error`` instead."""

    __tablename__ = "backtests"
    __table_args__ = (
        # Also the index of the user's backtests (user_id first).
        UniqueConstraint("user_id", "number"),
        CheckConstraint("status IN ('running', 'finished', 'failed')", name="status"),
    )

    id: Mapped[str] = mapped_column(String(14), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # The user's own sequential number, shown as #N.
    number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    # [{"market": "stock", "symbol": "AAPL"}, ...] in the order chosen.
    instruments: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    representation: Mapped[str] = mapped_column(String(16))
    # The model goes, the backtest stays: then its name and framework of when it ran are shown.
    model_id: Mapped[str | None] = mapped_column(
        ForeignKey("models.id", ondelete="SET NULL"), index=True
    )
    model_name: Mapped[str] = mapped_column(String(MODEL_NAME_MAX))
    model_framework: Mapped[str] = mapped_column(String(16))
    # The settings it ran with (schemas.BacktestSetup).
    setup: Mapped[dict[str, Any]] = mapped_column(JSONB)
    total_return: Mapped[float | None] = mapped_column(Float)
    buy_and_hold_return: Mapped[float | None] = mapped_column(Float)
    sharpe: Mapped[float | None] = mapped_column(Float)
    max_drawdown: Mapped[float | None] = mapped_column(Float)
    win_rate: Mapped[float | None] = mapped_column(Float)
    num_trades: Mapped[int | None] = mapped_column(Integer)
    # A short equity series of the strategy and of buy and hold (schemas.EquitySeries).
    equity: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    duration_s: Mapped[float | None] = mapped_column(Float)
    engine_version: Mapped[str | None] = mapped_column(String(16))
    # What the user sees of a failed backtest: what went wrong and what to do.
    error: Mapped[str | None] = mapped_column(Text)
    # What a backtest made in the app ran with and found (docs/engine.md, "The result
    # document"): its setup groups (the model with a copy of its manifest, the instrument and
    # period, decision, execution, benchmarks), what the run reported (the candles loaded and
    # their fingerprint, warm-up, rows, alignment, software, timings), the 12 statistics
    # (max_drawdown negative) and the predictions as zstd Parquet (ts, prediction), from which
    # the document is built again. Null for the seed command's backtests. Loaded only where
    # the document is built: the predictions are about 140 KB for 10 years of stock candles.
    spec: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, deferred=True, deferred_raiseload=True
    )
    report: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, deferred=True, deferred_raiseload=True
    )
    stats: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, deferred=True, deferred_raiseload=True
    )
    predictions: Mapped[bytes | None] = mapped_column(
        LargeBinary, deferred=True, deferred_raiseload=True
    )
    # The steps of the run (document.BacktestStep, in their order), written as it goes, so its
    # page shows where it is: null for a backtest stored before they were recorded.
    steps: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB, deferred=True, deferred_raiseload=True
    )
    starred: Mapped[bool] = mapped_column(Boolean, server_default=false(), default=False)
    # Made by the seed command (test data on stage and local), which replaces only these.
    seeded: Mapped[bool] = mapped_column(Boolean, server_default=false(), default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def set_equity(self, strategy: Sequence[float], benchmark: Sequence[float]) -> None:
        """Keeps the equity series of the strategy and of buy and hold (cumulative returns as
        fractions at the same evenly spaced times); what writes a backtest sets it this way.

        Raises:
            pydantic.ValidationError: The series differ in length, have fewer than 2 or more
                than ``EQUITY_POINTS`` points, or a value that is not a finite number.
        """
        series = EquitySeriesIn(strategy=list(strategy), benchmark=list(benchmark))
        self.equity = series.model_dump()


# The list and the Dashboard read a user's backtests newest first.
Index("ix_backtests_user_id_created_at", Backtest.user_id, Backtest.created_at)
