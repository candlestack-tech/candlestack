"""How the engine fills orders and what they cost."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Fill = Literal["next_open", "signal_close"]


class Settings(BaseModel):
    """The settings of one backtest. Frozen, so a stored run keeps the ones it ran with.

    - ``fill``: ``next_open`` (default) fills a decision at the open of the next candle, what a
      live run can do; ``signal_close`` fills it at the close of the candle that produced it,
      the convention of the reference results, optimistic because the decision and its fill
      see the same close.
    - ``stop_loss``: the loss that closes a position, as a fraction of its entry price (0.05 is
      5%); ``None`` for no stop.
    - ``drawdown_stop``: the fall of the equity from its highest level so far that halts the
      run, as a fraction of that level (0.2 is 20%); ``None`` for no drawdown stop.
    - ``position_size``: the share of the capital a position takes when it opens, kept until it
      exits (0.5 is 50%); 1 for the whole capital.
    - ``fee_bps``: the fee of each leg, in basis points of its notional.
    - ``slippage_bps``: how far each fill lands from its price, against the trade, in basis
      points.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    fill: Fill = "next_open"
    stop_loss: Annotated[float, Field(gt=0, lt=1)] | None = None
    drawdown_stop: Annotated[float, Field(gt=0, lt=1)] | None = None
    position_size: float = Field(default=1.0, gt=0, le=1, allow_inf_nan=False)
    fee_bps: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    slippage_bps: float = Field(default=0.0, ge=0, allow_inf_nan=False)
