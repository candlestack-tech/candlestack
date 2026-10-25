"""What a new backtest stores for the Backtests list from its setup (the rounded summary of
``BacktestSetup``) and its model's manifest (the representation), and which models and
instruments go together."""

import json
from pathlib import Path
from typing import Any

import pytest

from candlestack.data import InstrumentId
from candlestack.experiments.document import BenchmarkIn, Decision, Execution, Period
from candlestack.experiments.errors import (
    InstrumentNotForModel,
    InvalidBacktest,
    ModelNeedsSetup,
    TimeframeNotSupported,
)
from candlestack.experiments.runs import (
    _check_benchmarks,
    _manifest,
    _representation,
    _setup,
)
from candlestack.experiments.spec import RunSpec
from candlestack.ml import Model
from candlestack.preprocessing import Manifest

KERAS = Path(__file__).parents[2] / "fixtures" / "keras"
MANIFEST: dict[str, Any] = json.loads((KERAS / "raw_candles.manifest.json").read_text())
AAPL = InstrumentId.parse("stock:AAPL")


def spec(decision: dict[str, Any], execution: dict[str, Any]) -> RunSpec:
    return RunSpec.model_validate(
        {
            "model": {
                "name": "m.keras",
                "framework": "keras",
                "sha256": "0" * 64,
                "size_bytes": 1,
                "manifest": MANIFEST,
            },
            "data": {
                "instrument": {
                    "id": "stock:AAPL",
                    "market": "stock",
                    "symbol": "AAPL",
                    "name": "Apple Inc.",
                    "exchange": "NASDAQ",
                    "source": "alpaca",
                    "feed": "sip",
                    "currency": "USD",
                },
                "timeframe": "1h",
                "timeframe_seconds": 3600,
                "session": "regular",
                "period": Period(start=1_704_067_200, end=1_735_689_600),
                "load_start": 1_704_000_000,
                "warm_up": {"candles": 24, "kind": "exact"},
            },
            "decision": Decision.model_validate(decision),
            "execution": Execution.model_validate(execution),
            "benchmarks": [],
            "metrics": {"periods_per_year": 252, "risk_free_rate": 0.0},
            "app_version": "dev",
        }
    )


@pytest.mark.parametrize(
    ("decision", "execution", "summary"),
    [
        (
            {"rule": {"name": "threshold", "limit": 0.05}, "long_only": True},
            {"stop_loss": 0.07, "fee_bps": 10, "slippage_bps": 2},
            {
                "direction": "long",
                "threshold": 0.05,
                "stop_loss_pct": 7,
                "fees_pct": 0.1,
                "slippage_pct": 0.02,
            },
        ),
        (
            {"rule": {"name": "hysteresis", "enter": 0.3, "exit": 0.1}},
            {},
            {
                "direction": "both",
                "threshold": 0.3,
                "stop_loss_pct": 0,
                "fees_pct": 0.0,
                "slippage_pct": 0.0,
            },
        ),
        (
            {"rule": {"name": "kmeans", "k": 3, "window": 60, "refit_every": 10}},
            {"stop_loss": 0.025},
            {
                "direction": "both",
                "threshold": 0.0,
                "stop_loss_pct": 2,
                "fees_pct": 0.0,
                "slippage_pct": 0.0,
            },
        ),
    ],
)
def test_the_list_shows_a_rounded_summary_of_the_setup(
    decision: dict[str, Any], execution: dict[str, Any], summary: dict[str, Any]
) -> None:
    assert _setup(spec(decision, execution)) == {
        "timeframe": "1h",
        "period_start": "2024-01-01",
        "period_end": "2024-12-31",  # the last day of [start, end)
        "benchmark": "bh",
        **summary,
    }


@pytest.mark.parametrize(
    ("representation", "shown"),
    [("candles@1", "windows"), ("renko-triples@1", "renko"), ("kagi@1", "kagi")],
)
def test_the_representation_the_list_shows(representation: str, shown: str) -> None:
    manifest = json.loads(json.dumps(MANIFEST))
    manifest["input"]["representation"] = {"id": representation}

    assert _representation(Manifest.model_validate(manifest)) == shown


def model(manifest: dict[str, Any] | None) -> Model:
    return Model(id="mdl_x", manifest=manifest)


def with_input(**changes: Any) -> dict[str, Any]:
    manifest = json.loads(json.dumps(MANIFEST))
    manifest["input"].update(changes)
    return manifest


def test_a_model_runs_with_its_manifest_on_an_instrument_of_its_hours() -> None:
    crypto = InstrumentId.parse("crypto:BTCUSDT")

    assert _manifest(model(MANIFEST), AAPL).input.timeframe == "30m"
    assert _manifest(model(with_input(session="24/7")), crypto).input.session == "24/7"
    with pytest.raises(ModelNeedsSetup):
        _manifest(model(None), AAPL)
    with pytest.raises(TimeframeNotSupported):
        _manifest(model(with_input(timeframe="4h")), AAPL)
    with pytest.raises(InstrumentNotForModel):
        _manifest(model(MANIFEST), crypto)
    with pytest.raises(InstrumentNotForModel):
        _manifest(model(with_input(session="24/7")), AAPL)


def test_buy_and_hold_is_of_the_backtests_own_instrument() -> None:
    own = BenchmarkIn(id="bh", kind="buy_and_hold", instrument=" STOCK:aapl ")
    other = BenchmarkIn(id="bh", kind="buy_and_hold", instrument="stock:MSFT")

    _check_benchmarks([own], AAPL)
    with pytest.raises(InvalidBacktest, match="stock:AAPL"):
        _check_benchmarks([other], AAPL)
