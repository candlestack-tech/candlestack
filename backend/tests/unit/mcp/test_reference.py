from pathlib import Path

from candlestack.mcp.reference import render

SNAPSHOT = Path(__file__).parents[4] / "docs" / "mcp-tools.json"


def test_snapshot_is_current() -> None:
    """docs/mcp-tools.json is what the website's tool reference is built from: it must change
    with the tools."""
    assert SNAPSHOT.read_text(encoding="utf-8") == render(), (
        "docs/mcp-tools.json is stale. Regenerate it from backend/: "
        "uv run python -m candlestack.mcp.reference > ../docs/mcp-tools.json"
    )
