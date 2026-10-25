"""The job and its result as JSON: what a job may name, and what a result says."""

import time

import pytest
from pydantic import ValidationError

from candlestack.jobs import Job, JobResult, manifest_sha256

SHA256 = "ab" * 32


def job(**changes: object) -> Job:
    fields: dict[str, object] = {
        "kind": "predict",
        "model": "6f1c2a/9b3e.keras",
        "sha256": SHA256,
        "manifest": {"version": 1},
        "deadline": time.time() + 60,
    }
    return Job.model_validate({**fields, **changes})


def test_a_job_goes_through_json_unchanged() -> None:
    original = job()

    assert Job.model_validate_json(original.model_dump_json()) == original
    assert len(original.id) == 32
    assert original.version == 1


def test_each_job_gets_its_own_id() -> None:
    assert job().id != job().id


@pytest.mark.parametrize(
    "model",
    [
        "/data/models/u/m.keras",
        "../u/m.keras",
        "u/../../m.keras",
        "u/m.h5",
        "u/m.keras/",
        "u\\m.keras",
        "",
    ],
)
def test_a_job_names_a_model_file_inside_the_storage_only(model: str) -> None:
    with pytest.raises(ValidationError):
        job(model=model)


@pytest.mark.parametrize(
    "change",
    [
        {"sha256": "AB" * 32},
        {"sha256": "ab"},
        {"kind": "train"},
        {"version": 2},
        {"id": "../x"},
        {"extra": 1},
    ],
)
def test_a_job_off_its_form_is_refused(change: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        job(**change)


def test_a_failed_result_names_its_reason() -> None:
    result = JobResult.failed("a" * 32, "check", "time-limit", "Too long.")

    assert (result.status, result.reason, result.error) == ("failed", "time-limit", "Too long.")
    assert result.timings.total is None
    assert JobResult.model_validate_json(result.model_dump_json()) == result


def test_the_manifest_digest_ignores_the_order_of_keys_only() -> None:
    manifest = {"version": 1, "input": {"timeframe": "1h", "window": {"length": 25}}}
    reordered = {"input": {"window": {"length": 25}, "timeframe": "1h"}, "version": 1}
    changed = {"version": 1, "input": {"timeframe": "1h", "window": {"length": 26}}}

    assert manifest_sha256(manifest) == manifest_sha256(reordered)
    assert manifest_sha256(manifest) != manifest_sha256(changed)
    assert len(manifest_sha256(manifest)) == 64


def test_a_result_names_a_manifest_by_its_sha256_only() -> None:
    with pytest.raises(ValidationError):
        JobResult(id="a" * 32, kind="check", status="done", manifest_sha256="not a digest")
