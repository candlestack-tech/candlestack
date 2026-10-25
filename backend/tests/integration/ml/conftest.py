"""Fixtures of the models integration tests: the app on a real Postgres (emptied before each
test) with its model storage in a temporary directory (tests/integration/conftest.py), and
signed-in users with the models API at hand.

Test modules cannot import this file, so the helpers they need hang on the ``ModelsUser`` the
``ada`` and ``bob`` fixtures return, and on ``Storage``.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2
import pytest

from candlestack.core import Settings


@pytest.fixture(autouse=True)
def _empty_tables(empty_tables: None) -> None:
    """Every test starts without users."""


class Storage:
    """The model storage's directory."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def file(self, user_id: str, model_id: str) -> Path:
        return self.root / user_id / f"{model_id}.keras"

    def incoming(self) -> list[Path]:
        """What uploads left in the incoming directory."""
        return list((self.root / ".incoming").iterdir())

    def files(self) -> list[Path]:
        """Every model file, of every user."""
        return sorted(path for path in self.root.glob("usr_*/*") if path.is_file())


@pytest.fixture
def storage(settings: Settings) -> Storage:
    return Storage(settings.model_storage_dir)


class ModelsUser:
    """A signed-in user (the shared ``Account``: ``id``, ``request``) and the models API."""

    def __init__(self, account: Any) -> None:
        self._account = account

    def __getattr__(self, name: str) -> Any:
        return getattr(self._account, name)

    def upload(self, content: bytes | Any, name: str = "functional.keras") -> httpx2.Response:
        return self.request("POST", "/api/v1/models", params={"name": name}, content=content)

    def models(self) -> httpx2.Response:
        return self.request("GET", "/api/v1/models")

    def update(self, model_id: str, **body: Any) -> httpx2.Response:
        return self.request("PATCH", f"/api/v1/models/{model_id}", json=body)

    def delete(self, model_id: str) -> httpx2.Response:
        return self.request("DELETE", f"/api/v1/models/{model_id}")

    def set_manifest(self, model_id: str, content: bytes | Any) -> httpx2.Response:
        return self.request("PUT", f"/api/v1/models/{model_id}/manifest", content=content)

    def manifest(self, model_id: str) -> httpx2.Response:
        return self.request("GET", f"/api/v1/models/{model_id}/manifest")


@pytest.fixture
def ada(account: Callable[[str], Any]) -> ModelsUser:
    return ModelsUser(account("usr_adaaaaaaaa"))


@pytest.fixture
def bob(account: Callable[[str], Any]) -> ModelsUser:
    return ModelsUser(account("usr_bobbbbbbbb"))
