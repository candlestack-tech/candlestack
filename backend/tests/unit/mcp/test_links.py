"""The links to the app's pages a tool answers, which the agent gives the user to open."""

from types import SimpleNamespace
from typing import Any, cast

import pytest

from candlestack.experiments import SweepRanking
from candlestack.mcp import sweeps
from candlestack.mcp.convention import (
    COMPARE_PAGE_MAX,
    ToolCall,
    backtest_url,
    compare_url,
    model_url,
)

APP = "https://app.candlestack.tech"
CALL = ToolCall(
    caller=cast("Any", None),
    state=cast("Any", SimpleNamespace(settings=SimpleNamespace(public_url=APP))),
)


def ranking(*ranks: int | None) -> SweepRanking:
    """A ranking of the backtests bt_0000000000, bt_0000000001, ... with these ranks."""
    return SweepRanking.model_validate(
        {
            "metric": "sharpe",
            "label": "Sharpe",
            "better": "higher",
            "backtests": [
                {
                    "rank": rank,
                    "id": f"bt_{index:010d}",
                    "number": index,
                    "name": "Silent Orion",
                    "value": None if rank is None else 1.0 / rank,
                    "mark": None,
                }
                for index, rank in enumerate(ranks)
            ],
        }
    )


def test_a_backtest_and_a_model_have_their_pages() -> None:
    assert backtest_url(CALL, "bt_7f3k2m9qx4") == f"{APP}/backtests/bt_7f3k2m9qx4"
    assert model_url(CALL, "mdl_7f3k2m9qx4") == f"{APP}/models/mdl_7f3k2m9qx4"


def test_the_compare_page_names_its_backtests_in_order() -> None:
    assert compare_url(CALL, ["bt_b", "bt_a"]) == f"{APP}/backtests/compare?ids=bt_b,bt_a"


@pytest.mark.parametrize("count", [0, 1, COMPARE_PAGE_MAX + 1])
def test_the_compare_page_is_for_two_to_four(count: int) -> None:
    assert COMPARE_PAGE_MAX == 4
    assert compare_url(CALL, [f"bt_{index}" for index in range(count)]) is None


def test_a_sweep_not_ranked_yet_has_the_page_of_all_its_backtests_if_it_fits() -> None:
    ids = ["bt_a", "bt_b", "bt_c"]

    assert sweeps._compare_url(CALL, ids, None) == f"{APP}/backtests/compare?ids=bt_a,bt_b,bt_c"
    assert sweeps._compare_url(CALL, [*ids, "bt_d", "bt_e"], None) is None


def test_a_ranked_sweep_has_the_page_of_its_best_four() -> None:
    ranked = ranking(1, 2, 2, 4, 5, None)

    assert sweeps._compare_url(CALL, [], ranked) == (
        f"{APP}/backtests/compare?ids=bt_0000000000,bt_0000000001,bt_0000000002,bt_0000000003"
    )


def test_a_ranked_sweep_leaves_out_those_without_a_value() -> None:
    assert sweeps._compare_url(CALL, [], ranking(1, 2, None)) == (
        f"{APP}/backtests/compare?ids=bt_0000000000,bt_0000000001"
    )
    # One with a value is nothing to compare.
    assert sweeps._compare_url(CALL, [], ranking(1, None)) is None


def test_a_sweep_kept_before_its_link_still_answers_a_retry() -> None:
    """run_sweep answers a retry with its key from the first answer as it was kept, which may
    have no `url` yet."""
    kept = sweeps._STARTED.model_dump(mode="json", exclude={"url"})

    assert sweeps.SweepStarted.model_validate(kept).url is None
