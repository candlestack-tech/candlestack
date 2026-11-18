"""The tool that deletes backtests: some by their IDs or a whole sweep, with the count confirmed,
through the service of ``POST /api/v1/backtests/delete``."""

from typing import Annotated

from mcp.server.mcpserver import Context
from pydantic import Field

from candlestack.accounts import Scope
from candlestack.core import ProblemError
from candlestack.experiments import DELETE_MAX, BacktestsDeleted, build_deletes, sweep_problems
from candlestack.mcp.convention import Example, ToolSpec, changes, tool_call

_BacktestId = Annotated[str, Field(min_length=1, max_length=32, examples=["bt_7f3k2m9qx4"])]


async def delete_backtests(
    confirm_count: Annotated[
        int,
        Field(
            ge=1,
            description="How many it deletes, as the user agreed: the IDs given, or the "
            "sweep's `count` from get_sweep",
        ),
    ],
    ctx: Context,
    *,
    ids: Annotated[
        list[_BacktestId] | None,
        Field(
            min_length=1,
            max_length=DELETE_MAX,
            description="The user's backtests",
        ),
    ] = None,
    sweep_id: Annotated[
        str | None,
        Field(
            min_length=1,
            max_length=32,
            description="Instead of ids: the sweep and all its backtests",
        ),
    ] = None,
) -> BacktestsDeleted:
    """Deletes the user's backtests for good, by ids or a whole sweep; running ones stop,
    published ones leave Experiments. It cannot be undone: ask the user first. When another
    number than confirm_count matches, it answers confirmation-mismatch with the real `count`
    and deletes nothing."""
    if (ids is None) == (sweep_id is None):
        raise ProblemError(
            422, "validation", "Invalid arguments", "Give ids or sweep_id, one of the two."
        )
    call = tool_call(ctx)
    async with call.db() as db:
        with sweep_problems():
            return await build_deletes(call.state, db).delete(
                call.user.id, ids=ids or (), sweep_id=sweep_id, confirm=confirm_count
            )


TOOLS = (
    ToolSpec(
        delete_backtests,
        name="delete_backtests",
        title="Delete backtests",
        details="""
            Up to 100 backtests by their ids, with their results, or every backtest of a sweep
            with sweep_id, which deletes the sweep too. A queued one leaves the queue, a running
            one is stopped, a published one leaves Experiments and its page is gone. Another
            number matches when some were deleted already, or the sweep has more. IDs the user
            has no backtest with are listed in `not_found`.""",
        scopes=(Scope.backtests,),
        annotations=changes(destructive=True, idempotent=True),
        example=Example(
            arguments={"ids": ["bt_7f3k2m9qx4", "bt_t9n8v7d6a5"], "confirm_count": 2},
            result=BacktestsDeleted(
                deleted=["bt_7f3k2m9qx4", "bt_t9n8v7d6a5"], count=2, not_found=[]
            ),
        ),
    ),
)
