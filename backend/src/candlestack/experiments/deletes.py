"""Deleting backtests: one (``DELETE /api/v1/backtests/{id}``), up to ``DELETE_MAX`` by their IDs
or every backtest of a sweep (``POST /api/v1/backtests/delete``), for good.

A backtest is one row, which holds what its run stored (the setup, the report, the statistics,
the predictions, the curves), so deleting the row deletes it all. A published one leaves
Experiments with it, and its page and other people's comparisons answer 404. A sweep loses the
deleted backtests from its list and ranking; a sweep without any left is deleted too.

A queued backtest is taken out of its user's queue. A running one is stopped: its row goes, the
runner cancels its task (``BacktestRunner.stop``) and the user's next queued backtest starts in
the same transaction. A run the runner cannot cancel (between its steps on another process)
finds its row gone when it ends and stores nothing.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from candlestack.experiments.errors import CountMismatch, SweepNotFound
from candlestack.experiments.runner import BacktestRunner
from candlestack.experiments.schemas import BacktestStatus
from candlestack.experiments.store import BacktestStore

# The most backtests one request deletes by their IDs: ten pages of the list.
DELETE_MAX = 100

_BacktestIdIn = Annotated[str, Field(min_length=1, max_length=32, examples=["bt_7f3k2m9qx4"])]


class BacktestsDeleteIn(BaseModel):
    """The backtests to delete: `ids` or `sweep_id`, one of the two."""

    model_config = ConfigDict(extra="forbid")

    ids: list[_BacktestIdIn] | None = Field(
        default=None,
        min_length=1,
        max_length=DELETE_MAX,
        description=f"1 to {DELETE_MAX} IDs of the user's backtests",
    )
    sweep_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=32,
        description="Every backtest of this sweep of the user, and the sweep",
        examples=["sw_7f3k2m9qx4"],
    )

    @model_validator(mode="after")
    def _one_of_two(self) -> Self:
        if (self.ids is None) == (self.sweep_id is None):
            raise ValueError("Give either ids or sweep_id")
        return self


class BacktestsDeleted(BaseModel):
    """What was deleted."""

    deleted: list[str] = Field(
        description="The IDs of the backtests deleted, in the order given (a sweep's in its order)",
        examples=[["bt_7f3k2m9qx4", "bt_t9n8v7d6a5"]],
    )
    count: int = Field(description="How many were deleted", examples=[2])
    not_found: list[str] = Field(
        description="IDs of `ids` the user has no backtest with (deleted already, or another "
        "account's): nothing was deleted for them",
        examples=[[]],
    )


class BacktestDeletes:
    def __init__(self, store: BacktestStore, runner: BacktestRunner) -> None:
        self._store = store
        self._runner = runner

    async def delete(
        self,
        user_id: str,
        *,
        ids: Sequence[str] = (),
        sweep_id: str | None = None,
        confirm: int | None = None,
    ) -> BacktestsDeleted:
        """Deletes the user's backtests with the IDs, or every backtest of their sweep and the
        sweep. With ``confirm`` it deletes only when that many match.

        Raises:
            SweepNotFound, CountMismatch (``confirm`` is not how many match).
        """
        wanted = list(dict.fromkeys(ids))
        # The runner cannot start one of them between the read and the commit.
        await self._store.lock_user(user_id)
        if sweep_id is not None and await self._store.sweep(user_id, sweep_id) is None:
            raise SweepNotFound
        doomed = await self._store.to_delete(user_id, ids=wanted, sweep_id=sweep_id)
        if confirm is not None and confirm != len(doomed):
            raise CountMismatch(len(doomed))
        found = {backtest.id for backtest in doomed}
        sweeps = {sweep_id} | {backtest.sweep_id for backtest in doomed}
        running = [
            backtest.id for backtest in doomed if backtest.status == BacktestStatus.running.value
        ]
        await self._store.delete(sorted(found))
        await self._store.delete_empty_sweeps(user_id, sorted(filter(None, sweeps)))
        # The user's queue moves on, as when a run ends; also when none ran (a queue that stood
        # still starts now instead of at the runner's next check).
        started = await self._store.take_next(user_id, datetime.now(UTC))
        await self._store.commit()
        self._runner.stop(running)
        if started is not None:
            self._runner.start(started.id)
        if sweep_id is not None:
            deleted = [backtest.id for backtest in doomed]
        else:
            deleted = [backtest_id for backtest_id in wanted if backtest_id in found]
        return BacktestsDeleted(
            deleted=deleted,
            count=len(deleted),
            not_found=[backtest_id for backtest_id in wanted if backtest_id not in found],
        )
