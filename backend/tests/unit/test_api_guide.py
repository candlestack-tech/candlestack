"""The example of docs/api-guide.md, ``docs/examples/download_candles.py``, against a mocked API."""

import importlib.util
from pathlib import Path
from types import ModuleType

import httpx
import polars as pl
import pytest
import respx

from candlestack.cli import main as backtest_main

SCRIPT = Path(__file__).parents[3] / "docs" / "examples" / "download_candles.py"
BASE = "https://app.candlestack.tech"
DETAIL = f"{BASE}/api/v1/data/instruments/crypto:BTCUSDT"
CANDLES = f"{DETAIL}/candles"
HOUR = 3600
FIRST = 1_717_200_000  # the instrument's first candle, 2024-06-01T00:00Z
PROBLEM = {"Content-Type": "application/problem+json"}


@pytest.fixture
def script(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    spec = importlib.util.spec_from_file_location("download_candles", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "PAGE", 3)  # pages of 3 candles instead of 5000
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    monkeypatch.setenv("CANDLESTACK_SESSION", "token")
    return module


def candles(start: int, count: int, gaps: int = 0) -> httpx.Response:
    """A page of ``count`` hourly candles from ``start`` that ends ``gaps`` candles later."""
    times = [start + i * HOUR for i in range(count)]
    meta = {"start": start, "end": start + (count + gaps) * HOUR, "count": count}
    return httpx.Response(
        200,
        json={
            "meta": {**meta, "gaps": [], "gaps_total": gaps},
            "t": times,
            **{name: [100.0 + i for i in range(count)] for name in "ohlc"},
            "v": [5] * count,  # a volume can come as an integer
        },
    )


@respx.mock
def test_pages_back_by_end_and_writes_what_the_backtest_reads(
    script: ModuleType, tmp_path: Path
) -> None:
    respx.get(DETAIL).respond(json={"available_from": FIRST})
    # 7 candles from FIRST: the last 3, the 3 before them, then the first one alone.
    route = respx.get(CANDLES).mock(
        side_effect=[candles(FIRST + 4 * HOUR, 3), candles(FIRST + HOUR, 3), candles(FIRST, 1)]
    )
    out = tmp_path / "candles.parquet"

    assert script.main(["crypto:BTCUSDT", "1h", "--out", str(out)]) == 0

    ends = [call.request.url.params.get("end") for call in route.calls]
    assert ends == [None, str(FIRST + 4 * HOUR), str(FIRST + HOUR)]
    assert all(call.request.url.params["limit"] == "3" for call in route.calls)
    assert route.calls[0].request.headers["Cookie"] == "__Host-session=token"
    frame = pl.read_parquet(out)
    assert frame.columns == ["ts", "open", "high", "low", "close", "volume"]
    assert frame.get_column("ts").to_list() == [FIRST + i * HOUR for i in range(7)]
    predictions = tmp_path / "predictions.parquet"
    pl.DataFrame({"ts": frame.get_column("ts"), "prediction": [1.0] * 7}).write_parquet(predictions)
    run = ["run", "--candles", str(out), "--predictions", str(predictions)]
    assert backtest_main([*run, "--out", str(tmp_path / "run")]) == 0


@respx.mock
def test_stops_at_since_and_drops_the_candles_before_it(script: ModuleType, tmp_path: Path) -> None:
    respx.get(DETAIL).respond(json={"available_from": FIRST})
    route = respx.get(CANDLES).mock(side_effect=[candles(FIRST + 3 * HOUR, 3)])
    out = tmp_path / "candles.parquet"

    since = str(FIRST + 4 * HOUR)
    assert script.main(["crypto:BTCUSDT", "1h", "--since", since, "--out", str(out)]) == 0

    assert route.call_count == 1
    assert pl.read_parquet(out).get_column("ts").to_list() == [FIRST + 4 * HOUR, FIRST + 5 * HOUR]


@respx.mock
def test_a_short_page_with_gaps_is_not_the_start_of_the_data(
    script: ModuleType, tmp_path: Path
) -> None:
    respx.get(DETAIL).respond(json={"available_from": FIRST})
    # The newest page has 2 candles and 1 gap: the 3 candles asked for, so paging goes on.
    route = respx.get(CANDLES).mock(
        side_effect=[candles(FIRST + 3 * HOUR, 2, gaps=1), candles(FIRST, 2)]
    )

    assert script.main(["crypto:BTCUSDT", "1h", "--out", str(tmp_path / "c.parquet")]) == 0

    assert route.call_count == 2


@respx.mock
def test_waits_for_retry_after(
    script: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    limited = httpx.Response(429, headers={**PROBLEM, "Retry-After": "7"}, json={})
    respx.get(DETAIL).mock(
        side_effect=[limited, httpx.Response(200, json={"available_from": FIRST})]
    )
    respx.get(CANDLES).mock(side_effect=[candles(FIRST, 2)])
    waits: list[int] = []
    monkeypatch.setattr(script.time, "sleep", waits.append)

    assert script.main(["crypto:BTCUSDT", "1h", "--out", str(tmp_path / "c.parquet")]) == 0

    assert waits == [7]


@respx.mock
def test_a_problem_is_reported_with_its_title_and_detail(
    script: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    problem = {"title": "Not signed in", "detail": "Sign in to see market data."}
    respx.get(DETAIL).respond(401, headers=PROBLEM, json=problem)
    out = tmp_path / "candles.parquet"

    assert script.main(["crypto:BTCUSDT", "1h", "--out", str(out)]) == 1

    assert capsys.readouterr().err == "401 Not signed in: Sign in to see market data.\n"
    assert not out.exists()


def test_needs_the_session(script: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CANDLESTACK_SESSION")

    with pytest.raises(SystemExit, match="2"):
        script.main(["crypto:BTCUSDT", "1h"])
