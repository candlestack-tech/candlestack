"""The built-in candles of a model's trial run (``check``): enough for the manifest's warm-up
and a batch of samples, the same on every run.

They are a seeded random walk around 100, not market data: the trial run asks whether the
manifest builds an input the model answers with finite numbers of the right shape, not
whether the answers are good. The worker has no network, so it could not fetch real ones.
"""

import numpy as np
import polars as pl

from candlestack.preprocessing import Manifest, warm_up

# The samples of a trial run after the warm-up.
SAMPLES = 64
# Rows that span several candles (Renko, Kagi): the warm-up is an estimate, so twice as many.
_ESTIMATE_MARGIN = 2
_SEED = 20261018
# 2024-01-01 00:00 UTC
_START = 1_704_067_200
_TIMEFRAME_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14_400,
    "1d": 86_400,
}


def sample_candles(manifest: Manifest) -> pl.DataFrame:
    """Candles in the form the data module gives them (``ts``, open, high, low, close,
    volume), one every timeframe of the manifest."""
    need = warm_up(manifest)
    count = need.candles * (_ESTIMATE_MARGIN if need.kind == "estimate" else 1) + SAMPLES
    rng = np.random.default_rng(_SEED)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, count)))
    open_ = np.concatenate(([100.0], close[:-1]))
    spread = np.abs(rng.normal(0, 0.003, count))
    step = _TIMEFRAME_SECONDS[manifest.input.timeframe]
    return pl.DataFrame(
        {
            "ts": _START + np.arange(count, dtype=np.int64) * step,
            "open": open_,
            "high": np.maximum(open_, close) * (1 + spread),
            "low": np.minimum(open_, close) * (1 - spread),
            "close": close,
            "volume": rng.uniform(1_000, 100_000, count),
        }
    )
