"""Publishing backtests and Experiments over HTTP, against a real Postgres (conftest.py): who
can publish what, the catalog's search, scopes, filters, sorts, pages, counts and facets, the
author's name and what never shows of them, and the Dashboard's newest published backtests.
The result document of another account's published backtest is in test_runs.py."""

from datetime import UTC, datetime

import pytest

pytestmark = pytest.mark.integration

PROBLEMS = "https://candlestack.tech/problems/"
# What a backtest made in the app stores; the catalog and publishing only need that it has one.
SPEC = {"made": "in the app"}
EQUITY = {"strategy": [0.0, 0.05, 0.1], "benchmark": [0.0, -0.02, 0.5]}


def numbers(response) -> list[tuple[str, int]]:
    """The published backtests of the page, by their author's name and their number."""
    return [(item["author"]["name"], item["number"]) for item in response.json()["experiments"]]


def name(user, display_name: str) -> None:
    user.sql("UPDATE users SET display_name = :name WHERE id = :id", name=display_name, id=user.id)


# --- Publishing ------------------------------------------------------------------------------


def test_publish_and_unpublish(ada, sql) -> None:
    backtest_id = ada.backtest(1, spec=SPEC)

    published = ada.publish(backtest_id)
    again = ada.publish(backtest_id)
    listed = ada.backtests(show="public").json()

    assert published.status_code == 200
    assert published.headers["cache-control"] == "no-store"
    assert published.json()["public"] is True
    when = datetime.fromisoformat(published.json()["published_at"])
    assert abs((datetime.now(UTC) - when).total_seconds()) < 60
    # Publishing a published backtest changes nothing.
    assert again.json()["published_at"] == published.json()["published_at"]
    assert [item["id"] for item in listed["backtests"]] == [backtest_id]
    assert listed["counts"]["public"] == 1

    unpublished = ada.publish(backtest_id, public=False)

    assert (unpublished.json()["public"], unpublished.json()["published_at"]) == (False, None)
    assert sql("SELECT published_at FROM backtests") == [(None,)]
    assert ada.backtests(show="public").json()["backtests"] == []
    assert ada.backtests().json()["counts"]["public"] == 0

    # Published again it is new in Experiments.
    republished = datetime.fromisoformat(ada.publish(backtest_id).json()["published_at"])
    assert republished > when


def test_publishing_goes_with_the_other_changes(ada) -> None:
    backtest_id = ada.backtest(1, spec=SPEC)

    changed = ada.request(
        "PATCH",
        f"/api/v1/backtests/{backtest_id}",
        json={"public": True, "starred": True, "name": "half size"},
    ).json()

    assert (changed["public"], changed["starred"], changed["name"]) == (True, True, "half size")


@pytest.mark.parametrize("status", ["running", "failed"])
def test_only_a_finished_backtest_can_be_published(ada, sql, status: str) -> None:
    backtest_id = ada.backtest(1, status=status, spec=SPEC)

    response = ada.request(
        "PATCH", f"/api/v1/backtests/{backtest_id}", json={"public": True, "starred": True}
    )

    assert response.status_code == 409
    assert response.json() == {
        "type": PROBLEMS + "backtest-not-finished",
        "title": "Backtest not finished",
        "status": 409,
        "detail": "Only a finished backtest can be published.",
    }
    # Nothing of the request changed.
    assert sql("SELECT published_at, starred FROM backtests") == [(None, False)]


def test_test_data_cannot_be_published(ada, sql) -> None:
    backtest_id = ada.backtest(1, seeded=True)

    response = ada.publish(backtest_id)

    assert response.status_code == 409
    assert response.json()["type"] == PROBLEMS + "no-document"
    assert response.json()["detail"] == (
        "This backtest is test data from before backtests ran in the app, so it cannot be "
        "published."
    )
    assert sql("SELECT published_at FROM backtests") == [(None,)]
    # Taking back what is not published is no error.
    assert ada.publish(backtest_id, public=False).status_code == 200


def test_only_the_author_publishes_and_unpublishes(ada, bob, sql) -> None:
    backtest_id = bob.backtest(1, spec=SPEC)
    published = bob.backtest(2, spec=SPEC, published=1)

    assert ada.publish(backtest_id).status_code == 404
    assert ada.publish(published, public=False).status_code == 404
    assert ada.star(published).status_code == 404
    assert sql("SELECT number FROM backtests WHERE published_at IS NOT NULL") == [(2,)]


# --- The catalog -----------------------------------------------------------------------------


def test_a_published_backtest_as_experiments_shows_it(ada, bob) -> None:
    name(bob, "Bob Stone")
    model_id = bob.model("blesk-novol-8.keras")
    backtest_id = bob.backtest(
        1,
        symbols=("NVDA",),
        model_id=model_id,
        model_name="blesk-novol-8.keras",
        benchmark_return=0.14,
        input_shape=[25, 23],
        equity=EQUITY,
        published=5,
    )

    response = ada.experiments()

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "experiments": [
            {
                "id": backtest_id,
                "name": "Backtest A",
                "number": 1,
                "instruments": [{"market": "stock", "symbol": "NVDA"}],
                "representation": "renko",
                "model": {
                    "name": "blesk-novol-8.keras",
                    "framework": "keras",
                    "input_shape": [25, 23],
                },
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
                "benchmark_return": 0.14,
                "buy_and_hold_return": 0.5,
                "sharpe": 1.0,
                "max_drawdown": -0.1,
                "win_rate": 0.5,
                "num_trades": 100,
                "equity": EQUITY,
                "author": {"id": bob.id, "name": "Bob Stone", "mine": False},
                "published_at": "2026-10-01T12:05:00Z",
            }
        ],
        "total": 1,
        "page": 1,
        "pages": 1,
        "counts": {"all": 1, "mine": 0, "people": 1},
        "facets": {
            "markets": [{"value": "stock", "count": 1}],
            "instruments": [{"market": "stock", "symbol": "NVDA", "count": 1}],
            "representations": [{"value": "renko", "count": 1}],
            "timeframes": [{"value": "30m", "count": 1}],
        },
    }
    # Never the author's email, nor the model's ID.
    assert bob.email not in response.text
    assert model_id not in response.text
    assert bob.experiments().json()["experiments"][0]["author"]["mine"] is True


def test_nothing_published_yet(ada) -> None:
    ada.backtest(1, spec=SPEC)

    assert ada.experiments().json() == {
        "experiments": [],
        "total": 0,
        "page": 1,
        "pages": 1,
        "counts": {"all": 0, "mine": 0, "people": 0},
        "facets": {"markets": [], "instruments": [], "representations": [], "timeframes": []},
    }


def test_only_published_finished_backtests_show(ada, bob) -> None:
    ada.backtest(1, published=1)
    ada.backtest(2)
    # Neither can be published; were one ever marked so, it still would not show.
    ada.backtest(3, status="failed", published=2)
    ada.backtest(4, status="running", published=3)
    bob.backtest(1, published=4)

    page = ada.experiments().json()

    assert numbers(ada.experiments()) == [(bob.id, 1), (ada.id, 1)]
    assert page["counts"] == {"all": 2, "mine": 1, "people": 2}


@pytest.fixture
def catalog(ada, bob) -> None:
    """Published backtests of two accounts, and one that is not."""
    name(ada, "Ada Lovelace")
    name(bob, "Bob Stone")
    ada.backtest(1, symbols=("AAPL",), model_name="blesk-novol-8.keras", published=10)
    ada.backtest(
        2,
        symbols=("BTCUSDT",),
        market="crypto",
        representation="windows",
        timeframe="1h",
        model_name="my-tsmixer.keras",
        published=20,
    )
    ada.backtest(3, symbols=("TSLA",), model_name="hidden.keras")
    bob.backtest(
        1,
        symbols=("MSFT", "AAPL"),
        representation="kagi",
        timeframe="1d",
        model_name="kagi_50%.keras",
        published=30,
    )
    bob.backtest(2, symbols=("NVDA",), timeframe="5m", model_name="lstm.keras", published=40)


@pytest.mark.usefixtures("catalog")
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("aapl", [("Bob Stone", 1), ("Ada Lovelace", 1)]),
        ("TSMIXER", [("Ada Lovelace", 2)]),
        ("bob st", [("Bob Stone", 2), ("Bob Stone", 1)]),
        ("lovelace", [("Ada Lovelace", 2), ("Ada Lovelace", 1)]),
        ("tsla", []),  # not published
        ("hidden", []),
        ("%", [("Bob Stone", 1)]),  # matched as it is
        ("_", [("Bob Stone", 1)]),
        ("  ", [("Bob Stone", 2), ("Bob Stone", 1), ("Ada Lovelace", 2), ("Ada Lovelace", 1)]),
    ],
)
def test_search(ada, query: str, expected: list[tuple[str, int]]) -> None:
    assert numbers(ada.experiments(q=query)) == expected


@pytest.mark.usefixtures("catalog")
def test_mine_and_one_authors(ada, bob) -> None:
    mine = ada.experiments(mine="true").json()
    bobs = ada.experiments(author=bob.id).json()

    assert numbers(ada.experiments(mine="true")) == [("Ada Lovelace", 2), ("Ada Lovelace", 1)]
    assert numbers(ada.experiments(author=bob.id)) == [("Bob Stone", 2), ("Bob Stone", 1)]
    assert numbers(ada.experiments(author="usr_nobodyxxxx")) == []
    assert numbers(ada.experiments(mine="true", author=bob.id)) == []
    # The counts are of everything published, whatever the scope.
    assert mine["counts"] == bobs["counts"] == {"all": 4, "mine": 2, "people": 2}
    assert (mine["total"], bobs["total"]) == (2, 2)


@pytest.mark.usefixtures("catalog")
@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"market": "crypto"}, [("Ada Lovelace", 2)]),
        ({"market": "stock"}, [("Bob Stone", 2), ("Bob Stone", 1), ("Ada Lovelace", 1)]),
        ({"symbol": "aapl"}, [("Bob Stone", 1), ("Ada Lovelace", 1)]),
        ({"symbol": "AAP"}, []),
        ({"representation": "kagi"}, [("Bob Stone", 1)]),
        ({"timeframe": "5m"}, [("Bob Stone", 2)]),
        ({"market": "stock", "representation": "renko"}, [("Bob Stone", 2), ("Ada Lovelace", 1)]),
        ({"symbol": "AAPL", "q": "ada"}, [("Ada Lovelace", 1)]),
    ],
)
def test_filters(ada, params: dict[str, str], expected: list[tuple[str, int]]) -> None:
    assert numbers(ada.experiments(**params)) == expected


@pytest.mark.usefixtures("catalog")
def test_the_facets_follow_the_scope_and_the_search_not_the_filters(ada, bob) -> None:
    every = ada.experiments().json()["facets"]
    filtered = ada.experiments(market="crypto", timeframe="1h", representation="windows").json()

    assert every == {
        "markets": [{"value": "stock", "count": 3}, {"value": "crypto", "count": 1}],
        "instruments": [
            {"market": "stock", "symbol": "AAPL", "count": 2},
            {"market": "crypto", "symbol": "BTCUSDT", "count": 1},
            {"market": "stock", "symbol": "MSFT", "count": 1},
            {"market": "stock", "symbol": "NVDA", "count": 1},
        ],
        "representations": [
            {"value": "renko", "count": 2},
            {"value": "kagi", "count": 1},
            {"value": "windows", "count": 1},
        ],
        "timeframes": [
            {"value": "5m", "count": 1},
            {"value": "30m", "count": 1},
            {"value": "1h", "count": 1},
            {"value": "1d", "count": 1},
        ],
    }
    assert filtered["facets"] == every
    assert filtered["total"] == 1
    assert ada.experiments(author=bob.id).json()["facets"]["markets"] == [
        {"value": "stock", "count": 2}
    ]
    assert ada.experiments(q="btc").json()["facets"]["instruments"] == [
        {"market": "crypto", "symbol": "BTCUSDT", "count": 1}
    ]


@pytest.fixture
def results(ada, bob) -> None:
    """Published backtests with results in another order than they were published, a tie and
    one without the values."""
    ada.backtest(1, sharpe=0.5, total_return=0.3, max_drawdown=-0.2, num_trades=50, published=1)
    ada.backtest(
        2,
        sharpe=None,
        total_return=None,
        max_drawdown=None,
        num_trades=None,
        published=2,
    )
    bob.backtest(1, sharpe=1.5, total_return=-0.1, max_drawdown=-0.05, num_trades=300, published=3)
    bob.backtest(2, sharpe=0.5, total_return=0.2, max_drawdown=-0.4, num_trades=10, published=4)


@pytest.mark.usefixtures("results")
@pytest.mark.parametrize(
    ("sort", "order", "expected"),
    [
        ("newest", "desc", ["b2", "b1", "a2", "a1"]),
        ("newest", "asc", ["b2", "b1", "a2", "a1"]),
        ("sharpe", "desc", ["b1", "b2", "a1", "a2"]),
        ("sharpe", "asc", ["b2", "a1", "b1", "a2"]),
        ("return", "desc", ["a1", "b2", "b1", "a2"]),
        ("return", "asc", ["b1", "b2", "a1", "a2"]),
        ("max_drawdown", "desc", ["b1", "a1", "b2", "a2"]),
        ("max_drawdown", "asc", ["b2", "a1", "b1", "a2"]),
        ("trades", "desc", ["b1", "a1", "b2", "a2"]),
        ("trades", "asc", ["b2", "a1", "b1", "a2"]),
    ],
)
def test_sort_puts_those_without_the_value_last(
    ada, bob, sort: str, order: str, expected: list[str]
) -> None:
    short = {ada.id: "a", bob.id: "b"}

    page = ada.experiments(sort=sort, order=order).json()["experiments"]

    assert [f"{short[item['author']['id']]}{item['number']}" for item in page] == expected


@pytest.fixture
def named(ada, bob) -> None:
    """Published backtests shown by their own name or their generated one, a name two
    accounts have, and one not published."""
    ada.backtest(1, generated_name="Silent Orion", name="zeta", published=1)
    ada.backtest(2, generated_name="Bright Vega", published=2)
    ada.backtest(3, generated_name="Calm Lyra")
    bob.backtest(1, generated_name="bright vega", published=3)
    bob.backtest(2, generated_name="Golden Rigel", name="Alpha", published=4)


@pytest.mark.usefixtures("named")
@pytest.mark.parametrize(
    ("order", "expected"),
    [("asc", ["b2", "b1", "a2", "a1"]), ("desc", ["a1", "b1", "a2", "b2"])],
)
def test_sort_by_the_shown_name_ties_newest_published_first(
    ada, bob, order: str, expected: list[str]
) -> None:
    short = {ada.id: "a", bob.id: "b"}

    page = ada.experiments(sort="name", order=order).json()["experiments"]

    assert [f"{short[item['author']['id']]}{item['number']}" for item in page] == expected


@pytest.mark.usefixtures("named")
def test_search_finds_the_shown_name(ada, bob) -> None:
    assert numbers(ada.experiments(q="VEGA")) == [(bob.id, 1), (ada.id, 2)]
    assert numbers(ada.experiments(q="zet")) == [(ada.id, 1)]
    assert numbers(ada.experiments(q="orion")) == []  # its own name shows instead
    assert numbers(ada.experiments(q="lyra")) == []  # not published


def test_experiments_show_the_name_its_author_gives_it(ada, bob) -> None:
    backtest_id = bob.backtest(1, generated_name="Silent Orion", spec=SPEC, published=1)

    before = ada.experiments().json()["experiments"][0]["name"]
    bob.rename(backtest_id, "half size")

    assert before == "Silent Orion"
    assert ada.experiments().json()["experiments"][0]["name"] == "half size"
    assert ada.dashboard().json()["experiments"]["backtests"][0]["name"] == "half size"


def test_the_default_sort_is_the_newest_published(ada) -> None:
    ada.backtest(1, published=2)
    ada.backtest(2, published=1)

    assert numbers(ada.experiments()) == [(ada.id, 1), (ada.id, 2)]


def test_pages(ada, bob) -> None:
    for number in range(1, 8):
        ada.backtest(number, published=number)
        bob.backtest(number, published=10 + number)

    first = ada.experiments().json()
    last = ada.experiments(page=2).json()
    past = ada.experiments(page=3).json()

    assert len(first["experiments"]) == 12
    assert [item["number"] for item in last["experiments"]] == [2, 1]
    assert (first["total"], first["pages"], last["page"]) == (14, 2, 2)
    assert past["experiments"] == []
    assert (past["page"], past["pages"]) == (3, 2)
    assert ada.experiments(mine="true").json()["pages"] == 1


def test_an_account_deleted_takes_its_published_backtests_along(ada, bob) -> None:
    ada.backtest(1, published=1)
    bob.backtest(1, published=2)

    assert bob.request("DELETE", "/api/v1/me").status_code == 204

    assert numbers(ada.experiments()) == [(ada.id, 1)]
    assert ada.experiments().json()["counts"] == {"all": 1, "mine": 1, "people": 1}


# --- The Dashboard ---------------------------------------------------------------------------


def test_the_dashboard_shows_the_newest_published_of_anyone(ada, bob) -> None:
    name(bob, "Bob Stone")
    for number in range(1, 4):
        ada.backtest(number, published=number)
        bob.backtest(number, published=10 + number)
    ada.backtest(4)

    experiments = ada.dashboard().json()["experiments"]

    assert [(item["author"]["name"], item["number"]) for item in experiments["backtests"]] == [
        ("Bob Stone", 3),
        ("Bob Stone", 2),
        ("Bob Stone", 1),
        (ada.id, 3),
    ]
    assert experiments["backtests"][3]["author"]["mine"] is True
    assert experiments["total"] == 6
    assert bob.email not in ada.dashboard().text
