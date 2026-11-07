import importlib.util
import sys
from pathlib import Path

import pytest

# load.py is a script, not a package module: import it by path.
_spec = importlib.util.spec_from_file_location("load", Path(__file__).with_name("load.py"))
assert _spec is not None
assert _spec.loader is not None
load = importlib.util.module_from_spec(_spec)
sys.modules["load"] = load
_spec.loader.exec_module(load)


@pytest.mark.parametrize(
    ("share", "expected"),
    [(0.5, 5.0), (0.95, 10.0), (0.1, 1.0), (1.0, 10.0)],
)
def test_percentile_is_nearest_rank(share: float, expected: float) -> None:
    assert load.percentile([float(n) for n in range(10, 0, -1)], share) == expected


def test_percentile_of_nothing_is_zero() -> None:
    assert load.percentile([], 0.95) == 0.0


@pytest.mark.parametrize(
    ("answer", "error"),
    [("200", False), ("429", False), ("404", False), ("503", True), ("ReadTimeout", True)],
)
def test_errors_are_5xx_and_no_answer(answer: str, error: bool) -> None:
    assert load.is_error(answer) is error


@pytest.mark.parametrize(
    ("usage", "expected"),
    [("91.5MiB / 320MiB", 91.5 * 2**20), ("1.2GiB / 2GiB", 1.2 * 2**30), ("800kB / 1GB", None)],
)
def test_parse_memory_reads_docker_stats(usage: str, expected: float | None) -> None:
    if expected is None:
        with pytest.raises(ValueError, match="cannot read memory usage"):
            load.parse_memory(usage)
    else:
        assert load.parse_memory(usage) == pytest.approx(expected)


def test_window_is_300_candles_back() -> None:
    assert load.window_start(1_000_000, "1h", None) == 1_000_000 - 300 * 3600


def test_window_starts_at_the_first_candle_of_a_young_pair() -> None:
    assert load.window_start(1_000_000, "1d", 900_000) == 900_000


def test_report_sums_endpoints_and_counts_errors() -> None:
    stats = load.Stats()
    stats.add("search", 0.1, "200")
    stats.add("search", 0.3, "429")
    stats.add("candles", 2.0, "503")
    stats.add("candles", 1.0, "ReadTimeout")

    text = load.report(stats, 2.0, 100 * 2**20, "429 after 61 requests")

    assert "| candles | 2 | 1.0 | 1000 | 2000 | 2000 | 2000 | 503: 1, ReadTimeout: 1 |" in text
    assert "| search | 2 | 1.0 | 100 | 300 | 300 | 300 | 200: 1, 429: 1 |" in text
    assert "| all | 4 | 2.0 | 300 | 2000 | 2000 | 2000 | 2 errors (5xx or no answer) |" in text
    assert "Peak backend memory: 100 MiB" in text
    assert "Rate limit probe: 429 after 61 requests" in text


def test_report_leaves_out_what_was_not_measured() -> None:
    text = load.report(load.Stats(), 1.0, None, None)

    assert "memory" not in text
    assert "probe" not in text


def test_parse_args_defaults_to_the_local_backend() -> None:
    args = load.parse_args([])

    assert args.base_url == "http://127.0.0.1:8000"
    assert args.container is None
    assert not args.rate_limit_probe


def test_parse_args_picks_the_account_scenario() -> None:
    assert load.parse_args([]).scenario == "market"
    assert load.parse_args(["--scenario", "account"]).scenario == "account"


class _Recorder:
    """The API of a virtual user: records what it asked for and answers 200."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, dict[str, str | int]]] = []

    async def get(self, path: str, *, params: dict[str, str | int], headers: dict[str, str]):
        del headers
        self.asked.append((path, params))
        return load.httpx.Response(200, json={})


@pytest.mark.anyio
async def test_account_user_goes_through_the_account_pages() -> None:
    client = _Recorder()
    stats = load.Stats()
    user = load.AccountUser(client, stats, "198.18.0.1", think=0)

    await user.session([])

    paths = [path for path, _ in client.asked]
    assert paths == ["/api/v1/dashboard"] + ["/api/v1/backtests"] * 4 + ["/api/v1/models"]
    assert client.asked[2][1]["q"] in load.SEARCHES
    assert client.asked[3][1]["sort"] in load.SORTS
    assert 2 <= int(client.asked[4][1]["page"]) <= load.PAGES
    assert sorted(stats.latencies) == [
        "backtests",
        "backtests page",
        "backtests search",
        "backtests sort",
        "dashboard",
        "models",
    ]
