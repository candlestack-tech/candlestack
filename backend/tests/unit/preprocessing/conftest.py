import copy
import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parents[2] / "fixtures" / "keras"
RAW_CANDLES: dict[str, Any] = json.loads(
    (FIXTURES / "raw_candles.manifest.json").read_text(encoding="utf-8")
)


@pytest.fixture
def raw() -> dict[str, Any]:
    """The manifest without preprocessing (docs/preprocessing.md), to change in a test."""
    return copy.deepcopy(RAW_CANDLES)
