"""The docs tool: the published docs for an agent whose client reads no resources (ChatGPT) or
cannot open the website.

The pages come from the website as it is published, never from a copy here, so they cannot drift
from it: the list from its index for agents (``DOCS_INDEX``, llms.txt) and each page from its
Markdown version (``/docs/<page>.md``). Only the pages the index lists are fetched, from that one
host, with a client of its own (``docs_client``), so the tool reaches no other address and
never holds the app's connections. Each answer is cached for a few minutes, and calls that miss
it at once share one fetch; when the website cannot be reached, a copy at most an hour old
still answers, else the problem docs-unavailable, and the website is left alone for a while.
The JSON Schemas of the manifest and the result document come from the code that checks them,
as ``docs/*.schema.json`` does. A page longer than ``PART_CHARS`` comes in parts, and
``section`` answers one section of it (a ``##`` or ``###`` heading and what is under it).
"""

import asyncio
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

import httpx
from mcp.server.mcpserver import Context
from pydantic import BaseModel, Field

from candlestack.accounts import Scope
from candlestack.core import ProblemError
from candlestack.experiments import BacktestDocument
from candlestack.mcp.convention import Example, ToolSpec, reads, tool_call
from candlestack.mcp.docs import DOCS, DOCS_INDEX, MANIFEST_SCHEMA, RESULT_SCHEMA
from candlestack.preprocessing import Manifest

logger = logging.getLogger(__name__)

# How long a page stays fresh, and how old a copy may be to answer while the website is down.
FRESH_S = 300.0
STALE_S = 3600.0
# How long the website is left alone after a failed fetch.
RETRY_S = 30.0
# One fetch, connection included; the connections to the website at most; the largest page.
FETCH_S = 5.0
CONNECTIONS = 2
MAX_BYTES = 1024 * 1024
# The text of one answer: about 5,000 tokens.
PART_CHARS = 20_000
PAGE_MAX = 100
SECTION_MAX = 200
PARTS_MAX = 100

# A page as the website names it: lowercase words joined by hyphens, folders by slashes.
_WORD = r"[a-z0-9]+(?:-[a-z0-9]+)*"
PAGE_PATTERN = rf"^{_WORD}(?:/{_WORD})*$"
# A line of the index: "- [Title](https://candlestack.tech/docs/<page>.md): summary".
_ENTRY = re.compile(
    rf"^- \[(?P<title>[^\]]+)\]\({re.escape(DOCS)}/(?P<page>{_WORD}(?:/{_WORD})*)\.md\)"
    r"(?::\s*(?P<summary>.*))?$"
)
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_HEADING = re.compile(r"^(#{2,3}) +(?P<text>.+?)\s*#*\s*$")
_NOT_WORD = re.compile(r"[^a-z0-9]+")

# The page that lists every problem type with what to do.
PROBLEMS_PAGE = "api/errors"
_DIALECT = "https://json-schema.org/draft/2020-12/schema"


class DocsPage(BaseModel):
    """A docs page get_docs can read."""

    page: str = Field(description="What to pass as `page` to read it")
    title: str
    summary: str | None = Field(default=None, description="What the page is about")
    section: str | None = Field(default=None, description="The part of the docs it belongs to")


class DocsResult(BaseModel):
    """Without a page, the list of pages; with one, its text or a part of it."""

    pages: list[DocsPage] | None = Field(
        default=None, description="Every page of the docs, in the website's order"
    )
    references: list[DocsPage] | None = Field(
        default=None,
        description="The JSON Schemas of the manifest and the result document, and the page "
        "of every problem type",
    )
    page: str | None = Field(default=None, description="The page answered")
    title: str | None = None
    url: str | None = Field(default=None, description="The page on the web, for the user")
    sections: list[str] | None = Field(
        default=None,
        description="The headings of the page's sections and subsections, each a `section` "
        "you can ask for",
    )
    section: str | None = Field(default=None, description="The section answered")
    part: int | None = Field(default=None, description="The part answered, from 1")
    parts: int | None = Field(
        default=None, description="How many parts the page (or section) has; ask for each"
    )
    content: str | None = Field(default=None, description="The text: Markdown, or JSON")


@dataclass(frozen=True, slots=True)
class _Reference:
    title: str
    summary: str
    url: str
    text: str


def _schema(schema: dict[str, object]) -> str:
    """A JSON Schema as ``docs/*.schema.json`` has it."""
    return json.dumps({"$schema": _DIALECT, **schema}, indent=2, sort_keys=True) + "\n"


def _references() -> dict[str, _Reference]:
    return {
        "schemas/manifest": _Reference(
            "Manifest JSON Schema",
            "The JSON Schema every manifest is checked against",
            MANIFEST_SCHEMA,
            _schema(Manifest.model_json_schema(by_alias=True)),
        ),
        "schemas/backtest-result": _Reference(
            "Backtest result JSON Schema",
            "The JSON Schema of a backtest's result document",
            RESULT_SCHEMA,
            _schema(BacktestDocument.model_json_schema(by_alias=True, mode="serialization")),
        ),
    }


REFERENCES = _references()
# What the list names besides the pages: the schemas, and the page of every problem type.
_REFERENCE_PAGES = [
    *(DocsPage(page=name, title=r.title, summary=r.summary) for name, r in REFERENCES.items()),
    DocsPage(
        page=PROBLEMS_PAGE,
        title="Problem types",
        summary="Every problem a tool can answer, and what to do about it",
    ),
]


class DocsUnavailable(Exception):
    """The website did not answer the page."""


def docs_client() -> httpx.AsyncClient:
    """The client get_docs reads the website with, apart from the app's shared one: a website
    that hangs holds at most ``CONNECTIONS`` connections, never those of the market data and
    the sign-in. No proxy from the environment and no redirects; ``_fetch`` asks for no
    compression, so a small answer cannot inflate past the size cap."""
    return httpx.AsyncClient(
        trust_env=False,
        follow_redirects=False,
        timeout=httpx.Timeout(FETCH_S),
        limits=httpx.Limits(max_connections=CONNECTIONS, max_keepalive_connections=CONNECTIONS),
    )


@dataclass(slots=True)
class _Copy:
    text: str
    fetched: float


class DocsCache:
    """The website's answers by URL, fresh for ``FRESH_S`` and kept ``STALE_S`` for when the
    website is down. One per process: the pages are the same for everyone.

    Calls that miss the same URL at once share one fetch. After a failed fetch the website is
    left alone for ``RETRY_S``: the older copy answers at once, or the failure."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._copies: dict[str, _Copy] = {}
        self._fetching: dict[str, asyncio.Future[str]] = {}
        self._retry_at: dict[str, float] = {}
        self._clock = clock

    def clear(self) -> None:
        self._copies.clear()
        self._retry_at.clear()

    async def get(
        self, http: httpx.AsyncClient, url: str, valid: Callable[[str], bool] = bool
    ) -> str:
        """The text at `url`; a text `valid` refuses counts as a failed fetch."""
        now = self._clock()
        copy = self._copies.get(url)
        if copy is not None and now - copy.fetched < FRESH_S:
            return copy.text
        stale = copy if copy is not None and now - copy.fetched < STALE_S else None
        try:
            if now < self._retry_at.get(url, now):
                raise DocsUnavailable("it failed less than a minute ago")
            return await self._shared(http, url, valid)
        except DocsUnavailable as error:
            if stale is not None:
                logger.warning("Answering an older copy of %s: %s", url, error)
                return stale.text
            logger.warning("The docs at %s are unavailable: %s", url, error)
            raise

    async def _shared(self, http: httpx.AsyncClient, url: str, valid: Callable[[str], bool]) -> str:
        """The fetch of `url` in progress, or a new one; a caller that gives up leaves it running
        for the others."""
        fetching = self._fetching.get(url)
        if fetching is None:
            fetching = asyncio.ensure_future(self._load(http, url, valid))
            self._fetching[url] = fetching
            fetching.add_done_callback(lambda done: self._done(url, done))
        return await asyncio.shield(fetching)

    def _done(self, url: str, fetch: "asyncio.Future[str]") -> None:
        self._fetching.pop(url, None)
        if not fetch.cancelled():
            fetch.exception()  # retrieved here when every caller gave up

    async def _load(self, http: httpx.AsyncClient, url: str, valid: Callable[[str], bool]) -> str:
        try:
            text = await _fetch(http, url)
            if not valid(text):
                raise DocsUnavailable("the website answered no docs")
        except DocsUnavailable:
            self._retry_at[url] = self._clock() + RETRY_S
            raise
        self._retry_at.pop(url, None)
        self._copies[url] = _Copy(text, self._clock())
        return text


CACHE = DocsCache()


async def _fetch(http: httpx.AsyncClient, url: str) -> str:
    """The text at `url`, one of the website's: no redirect, at most ``MAX_BYTES``."""
    try:
        async with asyncio.timeout(FETCH_S):
            async with http.stream(
                "GET", url, follow_redirects=False, headers={"Accept-Encoding": "identity"}
            ) as response:
                if response.status_code != 200:
                    raise DocsUnavailable(f"HTTP {response.status_code}")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        raise DocsUnavailable(f"larger than {MAX_BYTES} bytes")
    except TimeoutError:
        raise DocsUnavailable(f"no answer within {FETCH_S:g} s") from None
    except httpx.HTTPError as error:
        raise DocsUnavailable(type(error).__name__) from None
    return body.decode("utf-8", errors="replace")


def _index(text: str) -> list[DocsPage]:
    """The pages the website's index lists, with the section each sits under."""
    pages: list[DocsPage] = []
    section = None
    for line in text.splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
        elif match := _ENTRY.match(line.strip()):
            pages.append(
                DocsPage(
                    page=match["page"],
                    title=match["title"],
                    summary=match["summary"] or None,
                    section=section,
                )
            )
    return pages


def _normal(text: str) -> str:
    """A heading as one compares it: lowercase words, so "Fees & slippage" is fees-slippage."""
    return _NOT_WORD.sub("-", text.lower()).strip("-")


def _blocks(text: str) -> list[tuple[int, str, str]]:
    """The text cut before each ``##`` and ``###`` heading outside code: (level, heading,
    text) with level 0 and no heading for what comes before the first."""
    blocks: list[tuple[int, str, list[str]]] = [(0, "", [])]
    fence = ""
    for line in text.splitlines(keepends=True):
        mark = _FENCE.match(line)
        if fence:
            if mark and mark[1][0] == fence[0] and len(mark[1]) >= len(fence):
                fence = "" if line.strip() == mark[1] else fence
        elif mark:
            fence = mark[1]
        elif heading := _HEADING.match(line):
            blocks.append((len(heading[1]), heading["text"], []))
        blocks[-1][2].append(line)
    return [(level, heading, "".join(lines)) for level, heading, lines in blocks if lines]


def _sections(text: str) -> list[str]:
    return [heading for level, heading, _ in _blocks(text) if level]


def _section(text: str, name: str) -> tuple[str, str] | None:
    """The section whose heading is `name` (in any case and punctuation): its heading, and
    its text up to the next heading of its level or above, its subsections included."""
    wanted = _normal(name)
    found: list[str] = []
    heading, depth = "", 0
    for level, title, block in _blocks(text):
        if depth:
            if level and level <= depth:
                break
            found.append(block)
        elif level and _normal(title) == wanted:
            heading, depth = title, level
            found.append(block)
    return (heading, "".join(found)) if depth else None


def _parts(text: str) -> list[str]:
    """The text in parts of at most ``PART_CHARS``, cut before a heading where one fits, else
    at the end of a line."""
    pieces: list[str] = []
    for _, _, block in _blocks(text):
        rest = block
        while len(rest) > PART_CHARS:
            cut = rest.rfind("\n", 0, PART_CHARS) + 1 or PART_CHARS
            pieces.append(rest[:cut])
            rest = rest[cut:]
        pieces.append(rest)
    parts = [""]
    for piece in pieces:
        if parts[-1] and len(parts[-1]) + len(piece) > PART_CHARS:
            parts.append("")
        parts[-1] += piece
    return parts


def docs_unavailable(url: str) -> ProblemError:
    return ProblemError(
        503,
        "docs-unavailable",
        "Docs unavailable",
        f"The docs website did not answer. Try again in a minute, or give the user {url}. "
        "Do not answer from memory what the docs would say.",
    )


def _no_page(page: str) -> ProblemError:
    return ProblemError(
        404,
        "not-found",
        "Not Found",
        f"There is no docs page {page!r}. Call get_docs without a page for the list.",
    )


def _no_section(page: str, section: str, sections: list[str]) -> ProblemError:
    known = f"Its sections: {', '.join(sections)}." if sections else "It has no sections."
    return ProblemError(
        404, "not-found", "Not Found", f"The page {page!r} has no section {section!r}. {known}"
    )


def _no_part(part: int, parts: int) -> ProblemError:
    return ProblemError(
        404, "not-found", "Not Found", f"There is no part {part}: this text has {parts}."
    )


async def _listed(http: httpx.AsyncClient) -> list[DocsPage]:
    try:
        # An index without pages is a broken build of the website, not the docs.
        return _index(await CACHE.get(http, DOCS_INDEX, valid=lambda text: bool(_index(text))))
    except DocsUnavailable:
        raise docs_unavailable(f"{DOCS}/") from None


async def _page(http: httpx.AsyncClient, page: str) -> tuple[str, str, str]:
    """The title, the web address and the text of `page`: a reference, or a page the website
    lists, the only ones fetched."""
    reference = REFERENCES.get(page)
    if reference is not None:
        return reference.title, reference.url, reference.text
    listed = {entry.page: entry for entry in await _listed(http)}
    if page not in listed:
        raise _no_page(page)
    url = f"{DOCS}/{page}/"
    try:
        text = await CACHE.get(http, f"{DOCS}/{page}.md")
    except DocsUnavailable:
        raise docs_unavailable(url) from None
    return listed[page].title, url, text


async def get_docs(
    page: Annotated[
        str | None,
        Field(
            max_length=PAGE_MAX,
            pattern=PAGE_PATTERN,
            description="A page from the list, such as manifest; leave it out for the list",
            examples=["manifest"],
        ),
    ] = None,
    section: Annotated[
        str | None,
        Field(
            min_length=1,
            max_length=SECTION_MAX,
            description="One section of the page, by its heading from `sections` (such as Metrics, "
            "or a tool's name on mcp/tools), its subsections included",
        ),
    ] = None,
    part: Annotated[
        int,
        Field(ge=1, le=PARTS_MAX, description="The part to read, from 1, of a long page"),
    ] = 1,
    *,
    ctx: Context,
) -> DocsResult:
    """CandleStack's official docs, as published on candlestack.tech/docs: how the backtest
    engine, the manifest, the metrics, the result document, the problems, the MCP server and
    the API work. Read the page before you explain any of these, and answer from it instead of
    from memory.

    Without `page`: the list of pages (page, title, summary) and the references (the JSON
    Schemas of the manifest and the result document, and the page of every problem type).
    With `page`: its text as Markdown and its `sections`. A long page comes in `parts`: ask for
    the next with `part`, or for one section with `section`."""
    http = tool_call(ctx).state.docs_http
    if page is None:
        return DocsResult(pages=await _listed(http), references=_REFERENCE_PAGES)
    title, url, text = await _page(http, page)
    sections = _sections(text)
    if section is not None:
        found = _section(text, section)
        if found is None:
            raise _no_section(page, section, sections)
        section, text = found
    parts = _parts(text)
    if part > len(parts):
        raise _no_part(part, len(parts))
    return DocsResult(
        page=page,
        title=title,
        url=url,
        sections=sections or None,
        section=section,
        part=part,
        parts=len(parts),
        content=parts[part - 1],
    )


_EXAMPLE_TEXT = (
    "# The model manifest\n\nThe manifest.json you upload with a model says which candles it "
    "takes, which columns in which order and how many rows make one sample.\n\n## What a "
    "manifest is\n\n..."
)

TOOLS = (
    ToolSpec(
        get_docs,
        name="get_docs",
        title="Read the docs",
        scopes=(Scope.read,),
        annotations=reads(),
        example=Example(
            arguments={"page": "manifest"},
            result=DocsResult(
                page="manifest",
                title="The model manifest",
                url=f"{DOCS}/manifest/",
                sections=["What a manifest is", "How you add it", "Fields", "Blocks", "Checks"],
                part=1,
                parts=1,
                content=_EXAMPLE_TEXT,
            ),
        ),
    ),
)
