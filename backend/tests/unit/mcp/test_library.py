"""The docs tool: the website's pages through get_docs, with the website mocked (respx). What it
lists and reads, the pages it never fetches, its cache, the parts and sections of a long page,
and the problems when the website fails."""

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from mcp import Client
from mcp.types import CallToolResult

from candlestack.mcp import library
from candlestack.mcp.library import (
    CACHE,
    FRESH_S,
    MAX_BYTES,
    PART_CHARS,
    STALE_S,
    DocsCache,
    DocsUnavailable,
)

DOCS = Path(__file__).parents[4] / "docs"
INDEX_URL = "https://candlestack.tech/llms.txt"
MANIFEST_URL = "https://candlestack.tech/docs/manifest.md"
INDEX = """\
# CandleStack

> CandleStack is a web app to backtest your own trading models on market data.

## MCP

- [Tools](https://candlestack.tech/docs/mcp/tools.md): Every tool of the MCP server.

## API

- [Errors](https://candlestack.tech/docs/api/errors.md): Every error is a short JSON document.

## Formats

- [The model manifest](https://candlestack.tech/docs/manifest.md): The manifest.json you upload.
- [No summary](https://candlestack.tech/docs/bare.md)
- [Elsewhere](https://example.com/docs/other.md): Not a page of the docs.
"""
MANIFEST = """\
# The model manifest

The manifest.json you upload with a model.

## What a manifest is

A JSON document.

```md
## Not a heading, inside code
```

## Fields

### The window rule

Rows make one sample.

## Checks

Every check.
"""


def result(answer: CallToolResult) -> dict[str, Any]:
    assert not answer.is_error, answer.content
    assert answer.structured_content is not None
    return answer.structured_content


def problem(answer: CallToolResult) -> dict[str, Any]:
    assert answer.is_error
    [content] = answer.content
    assert content.type == "text"
    return json.loads(content.text)


@pytest.fixture(autouse=True)
def empty_cache() -> Iterator[None]:
    CACHE.clear()
    yield
    CACHE.clear()


@pytest.fixture
def website() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        router.get(INDEX_URL).respond(200, text=INDEX)
        router.get(MANIFEST_URL).respond(200, text=MANIFEST)
        yield router


def docs(mcp, **arguments: Any) -> CallToolResult:
    async def call(session: Client) -> CallToolResult:
        return await session.call_tool("get_docs", arguments)

    return mcp(call)


def test_without_a_page_it_lists_the_pages_and_the_references(mcp, website) -> None:
    listed = result(docs(mcp))

    assert listed["pages"] == [
        {
            "page": "mcp/tools",
            "title": "Tools",
            "summary": "Every tool of the MCP server.",
            "section": "MCP",
        },
        {
            "page": "api/errors",
            "title": "Errors",
            "summary": "Every error is a short JSON document.",
            "section": "API",
        },
        {
            "page": "manifest",
            "title": "The model manifest",
            "summary": "The manifest.json you upload.",
            "section": "Formats",
        },
        {"page": "bare", "title": "No summary", "summary": None, "section": "Formats"},
    ]
    assert [ref["page"] for ref in listed["references"]] == [
        "schemas/manifest",
        "schemas/backtest-result",
        "api/errors",
    ]


def test_a_page_is_its_markdown_with_its_sections(mcp, website) -> None:
    page = result(docs(mcp, page="manifest"))

    assert page == {
        "pages": None,
        "references": None,
        "page": "manifest",
        "title": "The model manifest",
        "url": "https://candlestack.tech/docs/manifest/",
        "sections": ["What a manifest is", "Fields", "The window rule", "Checks"],
        "section": None,
        "part": 1,
        "parts": 1,
        "content": MANIFEST,
    }


def test_a_section_is_its_text_with_its_subsections(mcp, website) -> None:
    fields = result(docs(mcp, page="manifest", section="FIELDS"))
    window = result(docs(mcp, page="manifest", section="the window rule"))

    assert fields["section"] == "Fields"
    assert fields["content"] == "## Fields\n\n### The window rule\n\nRows make one sample.\n\n"
    assert window["content"] == "### The window rule\n\nRows make one sample.\n\n"
    # A heading inside code is not one.
    assert "Not a heading, inside code" not in fields["sections"]


def test_a_section_the_page_lacks_is_not_found(mcp, website) -> None:
    answer = problem(docs(mcp, page="manifest", section="Versions"))

    assert answer["type"] == "https://candlestack.tech/problems/not-found"
    assert "What a manifest is, Fields, The window rule, Checks" in answer["detail"]


def test_a_page_the_index_lacks_is_never_fetched(mcp, website) -> None:
    other = website.get("https://candlestack.tech/docs/secret.md").respond(200, text="no")

    answer = problem(docs(mcp, page="secret"))

    assert answer["type"] == "https://candlestack.tech/problems/not-found"
    assert "without a page" in answer["detail"]
    assert not other.called


@pytest.mark.parametrize(
    "page", ["../etc/passwd", "https://example.com/x", "Manifest", "manifest.md", "a//b"]
)
def test_a_page_that_is_no_page_name_is_refused(mcp, website, page: str) -> None:
    answer = problem(docs(mcp, page=page))

    assert answer["type"] == "https://candlestack.tech/problems/validation"
    assert website.calls.call_count == 0


def test_the_schemas_come_from_the_code_as_the_repository_has_them(mcp) -> None:
    for page, file in (
        ("schemas/manifest", "manifest.schema.json"),
        ("schemas/backtest-result", "backtest.schema.json"),
    ):
        text = (DOCS / file).read_text(encoding="utf-8")
        parts = [
            result(docs(mcp, page=page, part=part))
            for part in range(1, len(text) // PART_CHARS + 2)
        ]

        assert {answer["parts"] for answer in parts} == {len(parts)}
        assert "".join(answer["content"] for answer in parts) == text
        assert parts[0]["sections"] is None


def test_a_long_page_comes_in_parts_cut_before_a_heading(mcp, website) -> None:
    sections = [f"## Part {i}\n\n" + "word " * (PART_CHARS // 12) + "\n\n" for i in range(5)]
    website.get(MANIFEST_URL).respond(200, text="".join(sections))

    first = result(docs(mcp, page="manifest"))
    second = result(docs(mcp, page="manifest", part=2))
    past = problem(docs(mcp, page="manifest", part=4))

    assert (first["parts"], first["content"]) == (3, sections[0] + sections[1])
    assert second["content"] == sections[2] + sections[3]
    assert past["detail"] == "There is no part 4: this text has 3."


def test_a_block_longer_than_a_part_is_cut_at_a_line() -> None:
    line = "x" * 99 + "\n"
    text = line * (PART_CHARS // 100 * 2 + 10)

    parts = library._parts(text)

    assert "".join(parts) == text
    assert all(len(part) <= PART_CHARS and part.endswith("\n") for part in parts)
    assert library._parts("y" * (PART_CHARS + 1)) == ["y" * PART_CHARS, "y"]


def test_the_website_is_asked_again_only_after_a_few_minutes(website) -> None:
    now = [0.0]
    cache = DocsCache(clock=lambda: now[0])

    async def read() -> str:
        async with httpx.AsyncClient() as http:
            return await cache.get(http, MANIFEST_URL)

    asyncio.run(read())
    now[0] = FRESH_S - 1
    asyncio.run(read())
    assert website.routes[1].call_count == 1

    now[0] = FRESH_S + 1
    website.get(MANIFEST_URL).respond(200, text="# Changed\n")
    assert asyncio.run(read()) == "# Changed\n"


def test_an_older_copy_answers_while_the_website_is_down(website) -> None:
    now = [0.0]
    cache = DocsCache(clock=lambda: now[0])

    async def read() -> str:
        async with httpx.AsyncClient() as http:
            return await cache.get(http, MANIFEST_URL)

    asyncio.run(read())
    website.get(MANIFEST_URL).respond(503)
    now[0] = FRESH_S + 1
    assert asyncio.run(read()) == MANIFEST

    now[0] = STALE_S + 1
    with pytest.raises(DocsUnavailable, match="HTTP 503"):
        asyncio.run(read())


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (httpx.Response(404), "HTTP 404"),
        # A redirect is not followed: the tool reads only the website's own addresses.
        (httpx.Response(301, headers={"Location": "https://example.com/"}), "HTTP 301"),
        (httpx.Response(200, content=b"x" * (MAX_BYTES + 1)), "larger than"),
    ],
)
def test_a_page_the_website_does_not_answer_well_is_unavailable(
    website, response: httpx.Response, reason: str
) -> None:
    website.get(MANIFEST_URL).mock(return_value=response)

    async def read() -> str:
        async with httpx.AsyncClient() as http:
            return await library._fetch(http, MANIFEST_URL)

    with pytest.raises(DocsUnavailable, match=reason):
        asyncio.run(read())


def test_a_website_that_does_not_answer_in_time_is_unavailable(
    website, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(library, "FETCH_S", 0.01)

    async def slow(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1)
        return httpx.Response(200, text="late")

    website.get(MANIFEST_URL).mock(side_effect=slow)

    async def read() -> str:
        async with httpx.AsyncClient() as http:
            return await library._fetch(http, MANIFEST_URL)

    with pytest.raises(DocsUnavailable, match="no answer within"):
        asyncio.run(read())


def test_a_website_that_cannot_be_reached_is_unavailable(website) -> None:
    website.get(MANIFEST_URL).mock(side_effect=httpx.ConnectError("refused"))

    async def read() -> str:
        async with httpx.AsyncClient() as http:
            return await library._fetch(http, MANIFEST_URL)

    with pytest.raises(DocsUnavailable, match="ConnectError"):
        asyncio.run(read())


def test_the_tool_answers_docs_unavailable_when_the_website_is_down(mcp, website) -> None:
    website.get(MANIFEST_URL).respond(502)

    page = problem(docs(mcp, page="manifest"))
    website.get(INDEX_URL).respond(502)
    CACHE.clear()
    listed = problem(docs(mcp))

    assert (page["type"], page["status"]) == (
        "https://candlestack.tech/problems/docs-unavailable",
        503,
    )
    assert "https://candlestack.tech/docs/manifest/" in page["detail"]
    assert "https://candlestack.tech/docs/" in listed["detail"]
