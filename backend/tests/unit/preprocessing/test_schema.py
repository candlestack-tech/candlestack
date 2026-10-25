import json
from pathlib import Path

from candlestack.preprocessing import VERSION
from candlestack.preprocessing.schema import render

SNAPSHOT = Path(__file__).parents[4] / "docs" / "manifest.schema.json"


def test_snapshot_is_current() -> None:
    """docs/manifest.schema.json is the manifest's contract: it must change with the code."""
    assert SNAPSHOT.read_text(encoding="utf-8") == render(), (
        "docs/manifest.schema.json is stale. Regenerate it from backend/: "
        "uv run python -m candlestack.preprocessing.schema > ../docs/manifest.schema.json"
    )


def test_the_schema_names_its_dialect_and_the_manifest_version() -> None:
    schema = json.loads(render())

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["properties"]["version"] == {
        "const": VERSION,
        "description": "The version of the manifest's form",
        "title": "Version",
        "type": "integer",
    }
    assert set(schema["required"]) == {"version", "input", "signature"}
