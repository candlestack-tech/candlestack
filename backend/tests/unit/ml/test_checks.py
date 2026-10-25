"""The checks of the models where they need no Postgres: which results say something about the
model, and what a check does when Postgres or the code fails; the flows are integration tests
(tests/integration/ml/test_checks.py)."""

import asyncio
import logging
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from candlestack.jobs import JobResult, Reason
from candlestack.ml import ModelChecks
from candlestack.ml.checks import says_something


class NoDatabase:
    """Sessions bound to no database: a query in one fails as with Postgres out of reach."""

    sessions = async_sessionmaker()


def checks() -> ModelChecks:
    return ModelChecks(NoDatabase(), None)  # ty: ignore[invalid-argument-type]


def test_a_passed_check_says_something() -> None:
    result = JobResult(id="0" * 32, kind="check", status="done", verified=False)

    assert says_something(result)


@pytest.mark.parametrize(
    ("reason", "about_the_model"),
    [
        ("manifest", True),
        ("candles", True),
        ("load", True),
        ("predict", True),
        ("output", True),
        ("example", True),
        ("time-limit", True),
        ("stopped", True),
        ("model-changed", True),
        ("expired", False),
        ("worker-stopped", False),
        ("internal", False),
        ("model-missing", False),
    ],
)
def test_a_failed_check_says_something_when_it_failed_on_the_model(
    reason: Reason, about_the_model: bool
) -> None:
    result = JobResult.failed("0" * 32, "check", reason, "It failed.")

    assert says_something(result) is about_the_model


@pytest.mark.anyio
async def test_without_postgres_the_checks_at_startup_wait(caplog: pytest.LogCaptureFixture):
    with caplog.at_level(logging.WARNING):
        await checks()._recover()

    assert "The unchecked models were not looked up (UnboundExecutionError)" in caplog.text


@pytest.mark.anyio
async def test_without_postgres_a_check_is_not_kept(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        ended = await checks()._check("usr_adaaaaaaaa", "mdl_7f3k2m9qx4")

    assert ended is False
    assert (
        "The check of model mdl_7f3k2m9qx4 could not reach Postgres (UnboundExecutionError)"
        in caplog.text
    )


@pytest.mark.anyio
async def test_a_bug_in_a_check_is_an_error_for_a_developer(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*_args: Any) -> bool:
        raise ValueError("a bug")

    model_checks = checks()
    monkeypatch.setattr(model_checks, "_run", broken)

    ended = await model_checks._check("usr_adaaaaaaaa", "mdl_7f3k2m9qx4")

    assert ended is True
    (record,) = caplog.records
    assert record.levelno == logging.ERROR
    assert record.message == "The check of model mdl_7f3k2m9qx4 failed with an error of CandleStack"


@pytest.mark.anyio
async def test_close_stops_the_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    started = asyncio.Event()

    async def forever(*_args: Any) -> bool:
        started.set()
        await asyncio.Event().wait()
        return True

    model_checks = checks()
    monkeypatch.setattr(model_checks, "_run", forever)
    model_checks.start("usr_adaaaaaaaa", "mdl_7f3k2m9qx4")
    await started.wait()

    await model_checks.close()

    assert model_checks._tasks == set()
    assert model_checks._waiting == {}


@pytest.mark.anyio
async def test_a_users_checks_run_one_after_the_other(monkeypatch: pytest.MonkeyPatch) -> None:
    running, release, other_user_checked = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls: list[tuple[str, str]] = []

    async def check(user_id: str, model_id: str) -> bool:
        calls.append((user_id, model_id))
        if user_id == "usr_bbbbbbbbbb":
            other_user_checked.set()
        elif len(calls) == 1:
            running.set()
            await release.wait()
        return True

    model_checks = checks()
    monkeypatch.setattr(model_checks, "_check", check)
    model_checks.start("usr_adaaaaaaaa", "mdl_first00000")
    await asyncio.wait_for(running.wait(), 5)
    model_checks.start("usr_adaaaaaaaa", "mdl_second0000")
    # Set again while its check runs, and again before its turn.
    model_checks.start("usr_adaaaaaaaa", "mdl_first00000")
    model_checks.start("usr_adaaaaaaaa", "mdl_second0000")
    model_checks.start("usr_bbbbbbbbbb", "mdl_other00000")

    # Another user's check does not wait for them.
    await asyncio.wait_for(other_user_checked.wait(), 5)
    assert calls == [("usr_adaaaaaaaa", "mdl_first00000"), ("usr_bbbbbbbbbb", "mdl_other00000")]
    release.set()
    await asyncio.gather(*model_checks._tasks)

    assert [model for user, model in calls if user == "usr_adaaaaaaaa"] == [
        "mdl_first00000",
        "mdl_second0000",
        "mdl_first00000",
    ]
    assert model_checks._waiting == {}
