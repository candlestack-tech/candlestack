"""Every docs link the MCP server gives an agent leads to a page that exists: the instructions,
the docs resource, the tools' descriptions and the descriptions in their schemas name only the
pages listed here, checked by hand when the list changes."""

import asyncio
import re
from collections.abc import Iterator
from typing import Any

import pytest

from candlestack.mcp import market
from candlestack.mcp.docs import DOCS_TEXT, INSTRUCTIONS, MANIFEST_DOCS, MCP_DOCS, RESULT_DOCS
from candlestack.mcp.server import build_server

TOOLS_PAGE = "https://candlestack.tech/docs/mcp/tools/"
# The pages that exist. The manifest and the result document are the website's pages
# (candlestack-tech/website#63, #64).
KNOWN = {
    "https://candlestack.tech/docs/agents/",
    "https://candlestack.tech/docs/mcp/",
    TOOLS_PAGE,
    "https://candlestack.tech/problems/",
    "https://app.candlestack.tech/api/v1/docs",
    "https://candlestack.tech/docs/manifest/",
    "https://candlestack.tech/docs/backtest-json/",
    "https://raw.githubusercontent.com/candlestack-tech/candlestack/main/docs/manifest.schema.json",
    "https://raw.githubusercontent.com/candlestack-tech/candlestack/main/docs/backtest.schema.json",
}
# A URL ends at a space, a quote, a bracket or a placeholder (problems/<slug>); a full stop or
# a comma after it ends the sentence.
_URL = re.compile(r"https?://[^\s<>\"'`()\[\]]+")


def _descriptions(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "description" and isinstance(value, str):
                yield value
            else:
                yield from _descriptions(value)
    elif isinstance(node, list):
        for value in node:
            yield from _descriptions(value)


@pytest.fixture(scope="module")
def named() -> tuple[set[str], set[str]]:
    """The URLs the server names, and the names of its tools."""
    tools = asyncio.run(build_server("docs").list_tools())
    texts = [INSTRUCTIONS, DOCS_TEXT, MCP_DOCS]
    for tool in tools:
        texts.append(tool.description or "")
        texts.extend(_descriptions(tool.input_schema))
        texts.extend(_descriptions(tool.output_schema or {}))
    urls = {url.rstrip(".,;:") for text in texts for url in _URL.findall(text)}
    return urls, {tool.name for tool in tools}


def test_every_docs_link_is_a_known_page(named: tuple[set[str], set[str]]) -> None:
    urls, tool_names = named
    # A tool's description links its own entry of the tool reference.
    unknown = {
        url
        for url in urls
        if url not in KNOWN
        and not (
            url.startswith(f"{TOOLS_PAGE}#") and url.removeprefix(f"{TOOLS_PAGE}#") in tool_names
        )
    }

    assert not sorted(unknown), "Link only pages that exist; add a new page to KNOWN"


def test_the_manifest_and_result_links_are_named(named: tuple[set[str], set[str]]) -> None:
    urls, _ = named

    assert {MANIFEST_DOCS, RESULT_DOCS} <= urls
    # The repository's Markdown is not where an agent reads the formats any more.
    assert not [url for url in urls if url.startswith("https://github.com/")]


def test_the_catalog_sends_to_the_manifest_docs() -> None:
    [catalog] = [spec for spec in market.TOOLS if spec.name == "preprocessing_catalog"]

    assert "The manifest format: get_docs page manifest." in catalog.description
