"""The backtests and dashboard API over HTTP, against a real Postgres (conftest.py): the list's
search, filters, sorts, pages and counts, starring, the Dashboard, the models' usage, and what
happens to backtests when their model or their account goes."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import event

pytestmark = pytest.mark.integration

PROBLEMS = "https://candlestack.tech/problems/"
KERAS_FILE = Path(__file__).parents[2] / "fixtures" / "keras" / "functional.keras"


def numbers(response) -> list[int]:
    return [backtest["number"] for backtest in response.json()["backtests"]]


# --- The list --------------------------------------------------------------------------------


def test_a_backtest_as_the_list_shows_it(ada) -> None:
    model_id = ada.model("blesk-novol-8.keras")
    backtest_id = ada.backtest(
        1, symbols=("MSFT", "GOOGL"), model_id=model_id, model_name="blesk-novol-8.keras"
    )

    response = ada.backtests()

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "backtests": [
            {
                "id": backtest_id,
                "name": None,
                "number": 1,
                "status": "finished",
                "instruments": [
                    {"market": "stock", "symbol": "MSFT"},
                    {"market": "stock", "symbol": "GOOGL"},
                ],
                "representation": "renko",
                "model": {"id": model_id, "name": "blesk-novol-8.keras", "framework": "keras"},
                "setup": {
                    "timeframe": "30m",
                    "period_start": "2021-01-04",
                    "period_end": "2024-12-31",
                    "direction": "long",
                    "threshold": 0.05,
                    "stop_loss_pct": 7,
                    "fees_pct": 0.1,
                    "slippage_pct": 0.02,
                    "benchmark": "bh",
                },
                "total_return": 0.1,
                "buy_and_hold_return": 0.5,
                "sharpe": 1.0,
                "max_drawdown": -0.1,
                "win_rate": 0.5,
                "num_trades": 100,
                "equity": None,
                "duration_s": 20.0,
                "error": None,
                "starred": False,
                "created_at": "2026-10-01T13:00:00Z",
                "finished_at": "2026-10-01T13:00:20Z",
            }
        ],
        "total": 1,
        "page": 1,
        "pages": 1,
        "counts": {"all": 1, "starred": 0, "failed": 0},
        "models": [{"name": "blesk-novol-8.keras", "framework": "keras", "count": 1}],
    }


def test_no_backtests_yet(ada) -> None:
    assert ada.backtests().json() == {
        "backtests": [],
        "total": 0,
        "page": 1,
        "pages": 1,
        "counts": {"all": 0, "starred": 0, "failed": 0},
        "models": [],
    }


def test_only_the_users_own_backtests(ada, bob) -> None:
    ada.backtest(1)
    bob.backtest(1)
    bob.backtest(2)

    page = ada.backtests().json()

    assert page["total"] == 1
    assert page["counts"]["all"] == 1
    assert page["models"] == [{"name": "lstm.keras", "framework": "keras", "count": 1}]


def test_a_failed_backtest_has_its_error_and_no_results(ada) -> None:
    ada.backtest(1, status="failed")

    [backtest] = ada.backtests().json()["backtests"]

    assert backtest["status"] == "failed"
    assert backtest["error"] == "It failed."
    assert [backtest[key] for key in ("total_return", "sharpe", "max_drawdown", "num_trades")] == [
        None
    ] * 4


EQUITY = {"strategy": [0.0, 0.05, 0.1], "benchmark": [0.0, -0.02, 0.5]}


def test_a_backtest_shows_its_equity_series(ada) -> None:
    ada.backtest(1, equity=EQUITY)
    ada.backtest(2, equity=EQUITY, status="failed")

    shown = {item["number"]: item["equity"] for item in ada.backtests().json()["backtests"]}
    starred = ada.backtest(3, equity=EQUITY, starred=True)

    assert shown == {1: EQUITY, 2: None}  # a failed backtest shows none
    assert ada.star(starred).json()["equity"] == EQUITY
    assert ada.dashboard().json()["starred"]["backtests"][0]["equity"] == EQUITY
    # Its numbers are no part of the search.
    assert numbers(ada.backtests(q="0.05")) == []


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("#12", [12]),
        ("12", [12]),
        ("#1", [12, 11, 10, 1]),
        ("aapl", [10, 1]),
        ("Nvd", [11]),
        ("BLESK", [12, 11]),
        ("tsmixer", [10]),
        ("  ", [12, 11, 10, 1]),
        ("50%", []),
        ("_", []),
        ("zzz", []),
    ],
)
def test_search(ada, query, expected) -> None:
    ada.backtest(1, symbols=("AAPL",))
    ada.backtest(10, symbols=("MSFT", "AAPL"), model_name="my-tsmixer.keras")
    ada.backtest(11, symbols=("NVDA",), model_name="blesk-novol-8.keras")
    ada.backtest(12, symbols=("SPY",), model_name="blesk-novol-9.keras")

    assert numbers(ada.backtests(q=query)) == expected


def test_search_finds_the_models_current_name(ada) -> None:
    model_id = ada.model("renamed.keras")
    ada.backtest(1, model_id=model_id, model_name="original.keras")

    assert numbers(ada.backtests(q="renamed")) == [1]
    assert numbers(ada.backtests(q="original")) == []


def test_show(ada) -> None:
    ada.backtest(1, starred=True)
    ada.backtest(2, status="failed", starred=True)
    ada.backtest(3, status="failed")
    ada.backtest(4)

    assert numbers(ada.backtests(show="all")) == [4, 3, 2, 1]
    assert numbers(ada.backtests(show="starred")) == [2, 1]
    assert numbers(ada.backtests(show="failed")) == [3, 2]
    assert ada.backtests(show="failed").json()["counts"] == {"all": 4, "starred": 2, "failed": 2}


def test_the_model_filter_and_the_models_list(ada) -> None:
    current = ada.model("current.keras")
    ada.backtest(1, model_name="b.keras")
    ada.backtest(2, model_name="a.keras")
    ada.backtest(3, model_name="old.keras", model_id=current)
    ada.backtest(4, model_name="current.keras", model_id=current, status="failed")
    ada.backtest(5, model_name="b.keras")

    page = ada.backtests(model="CURRENT.keras", q="", show="all")

    assert numbers(page) == [4, 3]
    assert page.json()["models"] == [
        {"name": "b.keras", "framework": "keras", "count": 2},
        {"name": "current.keras", "framework": "keras", "count": 2},
        {"name": "a.keras", "framework": "keras", "count": 1},
    ]
    # The panel's lists ignore the search and the filters.
    assert ada.backtests(model="a.keras", show="failed").json()["models"] == page.json()["models"]
    assert numbers(ada.backtests(model="a.keras", show="failed")) == []
    assert numbers(ada.backtests(model="b.keras", q="#5")) == [5]


def test_the_models_list_has_one_entry_per_name_in_any_case(ada, sql) -> None:
    ada.backtest(1, model_name="lstm.keras")
    ada.backtest(2, model_name="LSTM.keras")
    ada.backtest(3, model_name="lstm.keras")
    ada.backtest(4, model_name="gru.keras")

    first = ada.backtests().json()["models"]
    ada.backtest(5, model_name="LSTM.keras")
    sql("UPDATE backtests SET model_framework = 'onnx' WHERE number = 5")
    then = ada.backtests().json()["models"]

    # Spelled as and with the framework of the newest backtest of the name.
    assert first == [
        {"name": "lstm.keras", "framework": "keras", "count": 3},
        {"name": "gru.keras", "framework": "keras", "count": 1},
    ]
    assert then[0] == {"name": "LSTM.keras", "framework": "onnx", "count": 4}
    for name in ("lstm.keras", "LSTM.keras"):
        assert ada.backtests(model=name).json()["total"] == 4  # what the entry counts


def test_a_stored_setup_beyond_todays_limits_still_lists(ada, sql) -> None:
    ada.backtest(1)
    sql(
        "UPDATE backtests SET setup = setup || CAST(:change AS jsonb)",
        change='{"stop_loss_pct": 50, "threshold": 0.9, "period_end": "2020-01-01"}',
    )

    response = ada.backtests()

    assert response.status_code == 200
    setup = response.json()["backtests"][0]["setup"]
    assert (setup["stop_loss_pct"], setup["threshold"], setup["period_end"]) == (
        50,
        0.9,
        "2020-01-01",
    )
    assert ada.dashboard().status_code == 200


@pytest.fixture
def results(ada) -> None:
    """Backtests with results in another order than their numbers, a tie and two failed ones."""
    ada.backtest(1, sharpe=0.5, total_return=0.3, max_drawdown=-0.2, num_trades=50)
    ada.backtest(2, status="failed", model_name="b.keras")
    ada.backtest(3, sharpe=1.5, total_return=-0.1, max_drawdown=-0.05, num_trades=300)
    ada.backtest(4, sharpe=0.5, total_return=0.2, max_drawdown=-0.4, num_trades=10)
    ada.backtest(5, status="failed", model_name="a.keras")


@pytest.mark.usefixtures("results")
@pytest.mark.parametrize(
    ("sort", "order", "expected"),
    [
        ("default", "desc", [5, 4, 3, 2, 1]),
        ("default", "asc", [5, 4, 3, 2, 1]),
        ("number", "asc", [1, 2, 3, 4, 5]),
        ("number", "desc", [5, 4, 3, 2, 1]),
        ("when", "asc", [1, 2, 3, 4, 5]),
        ("sharpe", "desc", [3, 4, 1, 5, 2]),
        ("sharpe", "asc", [4, 1, 3, 5, 2]),
        ("return", "desc", [1, 4, 3, 5, 2]),
        ("return", "asc", [3, 4, 1, 5, 2]),
        ("max_drawdown", "desc", [3, 1, 4, 5, 2]),
        ("max_drawdown", "asc", [4, 1, 3, 5, 2]),
        ("trades", "desc", [3, 1, 4, 5, 2]),
        ("trades", "asc", [4, 1, 3, 5, 2]),
        ("model", "asc", [5, 2, 4, 3, 1]),
        ("model", "desc", [4, 3, 1, 2, 5]),
    ],
)
def test_sort_puts_backtests_without_the_value_last(ada, sort, order, expected) -> None:
    assert numbers(ada.backtests(sort=sort, order=order)) == expected


def test_the_default_order_is_descending(ada) -> None:
    ada.backtest(1)
    ada.backtest(2)

    assert numbers(ada.backtests(sort="number")) == [2, 1]


def test_pages(ada) -> None:
    for number in range(1, 24):
        ada.backtest(number)

    first = ada.backtests().json()
    last = ada.backtests(page=3).json()
    past = ada.backtests(page=4).json()

    assert [backtest["number"] for backtest in first["backtests"]] == list(range(23, 13, -1))
    assert [backtest["number"] for backtest in last["backtests"]] == [3, 2, 1]
    assert (first["total"], first["pages"], last["page"]) == (23, 3, 3)
    assert past["backtests"] == []
    assert (past["page"], past["pages"]) == (4, 3)


def test_pages_of_the_filtered_backtests(ada) -> None:
    for number in range(1, 13):
        ada.backtest(number, starred=number % 2 == 0)

    page = ada.backtests(show="starred").json()

    assert (page["total"], page["pages"]) == (6, 1)


# --- Starring --------------------------------------------------------------------------------


def test_star_and_unstar(ada, sql) -> None:
    backtest_id = ada.backtest(1)

    starred = ada.star(backtest_id)
    unstarred = ada.star(backtest_id, starred=False)

    assert starred.status_code == 200
    assert starred.headers["cache-control"] == "no-store"
    assert starred.json()["starred"] is True
    assert starred.json()["id"] == backtest_id
    assert unstarred.json()["starred"] is False
    ada.star(backtest_id)
    assert sql("SELECT starred FROM backtests") == [(True,)]
    assert ada.backtests().json()["counts"]["starred"] == 1


def test_rename_and_take_the_name_away(ada, sql) -> None:
    backtest_id = ada.backtest(1)
    ada.star(backtest_id)

    renamed = ada.rename(backtest_id, "  half size ")

    assert renamed.status_code == 200
    assert renamed.json()["name"] == "half size"
    assert renamed.json()["starred"] is True
    assert sql("SELECT name FROM backtests") == [("half size",)]
    assert numbers(ada.backtests(q="half")) == [1]
    assert ada.rename(backtest_id, None).json()["name"] is None
    assert numbers(ada.backtests(q="half")) == []


def test_another_users_backtest_cannot_be_renamed(ada, bob) -> None:
    assert ada.rename(bob.backtest(1), "mine").status_code == 404


def test_another_users_backtest_is_not_found(ada, bob, sql) -> None:
    backtest_id = bob.backtest(1)

    response = ada.star(backtest_id)

    assert response.status_code == 404
    assert response.json() == {
        "type": PROBLEMS + "not-found",
        "title": "Not Found",
        "status": 404,
        "detail": "You have no backtest with this ID.",
    }
    assert sql("SELECT starred FROM backtests") == [(False,)]


def test_an_unknown_backtest_is_not_found(ada) -> None:
    assert ada.star("bt_zzzzzzzzzz").status_code == 404


# --- The Dashboard ---------------------------------------------------------------------------


def test_the_dashboard(ada, bob) -> None:
    ada.backtest(1, sharpe=0.9, total_return=0.4, max_drawdown=-0.3, win_rate=0.6, starred=True)
    ada.backtest(2, sharpe=1.3, total_return=0.1, max_drawdown=-0.1, win_rate=0.4)
    ada.backtest(3, sharpe=0.9, total_return=0.2, max_drawdown=-0.2, win_rate=0.5, starred=True)
    ada.backtest(4, status="failed", starred=True)
    ada.backtest(5, sharpe=-0.2, total_return=-0.1, max_drawdown=-0.5, win_rate=0.3)
    ada.backtest(6, sharpe=1.1, total_return=0.3, max_drawdown=0.0, win_rate=0.55, starred=True)
    ada.backtest(7, sharpe=0.5, total_return=0.05, max_drawdown=-0.25, win_rate=0.45)
    ada.backtest(8, sharpe=None, total_return=0.0, max_drawdown=-0.01, win_rate=None, starred=True)
    ada.backtest(9, status="failed")
    ada.backtest(10, sharpe=0.7, total_return=0.15, max_drawdown=-0.15, win_rate=0.5, starred=True)
    bob.backtest(1, sharpe=9.0, starred=True)

    response = ada.dashboard()

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    dashboard = response.json()
    assert dashboard["counts"] == {"all": 10, "starred": 6, "failed": 2}
    assert [backtest["number"] for backtest in dashboard["last"]] == [10, 9, 8, 7, 6, 5]

    def best(result: str) -> list[int]:
        return [backtest["number"] for backtest in dashboard["best"][result]]

    assert best("sharpe") == [2, 6, 3, 1, 10]  # without one, failed or not: never
    assert best("total_return") == [1, 6, 3, 10, 2]
    assert best("max_drawdown") == [6, 8, 2, 10, 3]  # the smallest fall first
    assert best("win_rate") == [1, 6, 10, 3, 7]
    assert [backtest["number"] for backtest in dashboard["starred"]["backtests"]] == [6, 3, 1, 10]
    assert dashboard["starred"]["total"] == 6


def test_the_starred_without_a_sharpe_ratio_come_last(ada) -> None:
    ada.backtest(1, status="failed", starred=True)
    ada.backtest(2, sharpe=0.1, starred=True)
    ada.backtest(3, status="failed", starred=True)

    starred = ada.dashboard().json()["starred"]

    assert [backtest["number"] for backtest in starred["backtests"]] == [2, 3, 1]


def test_an_empty_dashboard(ada) -> None:
    assert ada.dashboard().json() == {
        "counts": {"all": 0, "starred": 0, "failed": 0},
        "last": [],
        "best": {"sharpe": [], "total_return": [], "max_drawdown": [], "win_rate": []},
        "starred": {"backtests": [], "total": 0},
    }


# --- The models' usage -----------------------------------------------------------------------


@pytest.fixture
def statements(app) -> Iterator[list[str]]:
    """The SQL statements the app sends while the test runs."""
    sent: list[str] = []

    def record(_connection, _cursor, statement, *_args) -> None:
        sent.append(statement)

    engine = app.state.db.engine.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    yield sent
    event.remove(engine, "before_cursor_execute", record)


def test_the_models_list_counts_their_backtests_in_one_query(ada, bob, statements) -> None:
    used = ada.model("used.keras")
    unused = ada.model("unused.keras")
    failed_only = ada.model("failed.keras")
    ada.backtest(1, model_id=used, sharpe=0.4)
    ada.backtest(2, model_id=used, sharpe=1.2)
    ada.backtest(3, model_id=used, status="failed")
    ada.backtest(4, model_id=failed_only, status="failed")
    ada.backtest(5, model_id=used, sharpe=None)
    bob.backtest(1, model_id=used, sharpe=5.0)  # cannot happen, but would not count
    statements.clear()

    models = ada.request("GET", "/api/v1/models").json()["models"]

    usage = {model["id"]: (model["backtest_count"], model["best_sharpe"]) for model in models}
    assert usage == {used: (4, 1.2), unused: (0, None), failed_only: (1, None)}
    assert len([sql for sql in statements if "FROM backtests" in sql]) == 1


def test_a_renamed_model_shows_its_new_name_in_its_backtests(ada) -> None:
    model_id = ada.request(
        "POST", "/api/v1/models", params={"name": "old.keras"}, content=KERAS_FILE.read_bytes()
    ).json()["id"]
    ada.backtest(1, model_id=model_id, model_name="old.keras")

    renamed = ada.request("PATCH", f"/api/v1/models/{model_id}", json={"name": "new"})

    assert renamed.json()["backtest_count"] == 1
    [backtest] = ada.backtests().json()["backtests"]
    assert backtest["model"] == {"id": model_id, "name": "new.keras", "framework": "keras"}
    assert ada.backtests().json()["models"] == [
        {"name": "new.keras", "framework": "keras", "count": 1}
    ]
    assert numbers(ada.backtests(model="new.keras")) == [1]


def test_a_deleted_models_backtests_stay_with_its_name(ada, sql) -> None:
    model_id = ada.request(
        "POST", "/api/v1/models", params={"name": "gone.keras"}, content=KERAS_FILE.read_bytes()
    ).json()["id"]
    ada.backtest(1, model_id=model_id, model_name="gone.keras")

    assert ada.request("DELETE", f"/api/v1/models/{model_id}").status_code == 204

    [backtest] = ada.backtests().json()["backtests"]
    assert backtest["model"] == {"id": None, "name": "gone.keras", "framework": "keras"}
    assert sql("SELECT model_id, model_name FROM backtests") == [(None, "gone.keras")]


# --- Deleting the account --------------------------------------------------------------------


def test_deleting_the_account_deletes_its_backtests(ada, bob, sql) -> None:
    model_id = ada.model("lstm.keras")
    ada.backtest(1, model_id=model_id)
    ada.backtest(2)
    kept = bob.backtest(1)

    assert ada.request("DELETE", "/api/v1/me").status_code == 204

    assert sql("SELECT id FROM backtests") == [(kept,)]
