"""The server's prompts through the SDK's client, and what the instructions and the tools tell
an agent about the docs: read them with get_docs, and only pages that exist."""

import asyncio
import re
from typing import Any

import pytest
from mcp import Client

from candlestack.mcp.docs import INSTRUCTIONS
from candlestack.mcp.prompts import PROMPTS, RULES
from candlestack.mcp.server import build_server

# The pages of the website the prompts and the tools name, checked by hand when they change;
# engine, metrics and sweeps come with candlestack-tech/website#94.
PAGES = {
    "api/errors",
    "backtest-json",
    "engine",
    "manifest",
    "mcp/tools",
    "metrics",
    "schemas/backtest-result",
    "schemas/manifest",
    "sweeps",
}
# The texts name a page always as "get_docs page manifest".
_NAMED = re.compile(r"get_docs\s+page\s+([a-z0-9/-]*[a-z0-9])")


def _pages(text: str) -> set[str]:
    return set(_NAMED.findall(text))


def test_the_prompts_are_listed_with_their_arguments(mcp) -> None:
    async def listed(session: Client) -> Any:
        return (await session.list_prompts()).prompts

    prompts = mcp(listed)

    assert [(p.name, p.title) for p in prompts] == [(p.name, p.title) for p in PROMPTS]
    by_name = {p.name: p for p in prompts}
    assert [(a.name, a.required) for a in by_name["analyze_backtest"].arguments or []] == [
        ("backtest", True)
    ]
    assert [(a.name, a.required) for a in by_name["first_backtest"].arguments or []] == [
        ("model", False),
        ("instrument", False),
    ]
    assert all(p.description for p in prompts)


def test_a_prompt_is_the_plan_with_the_arguments_and_the_rules(mcp) -> None:
    async def get(session: Client) -> Any:
        return await session.get_prompt(
            "first_backtest", {"model": "lstm-v2", "instrument": "Apple"}
        )

    prompt = mcp(get)

    [message] = prompt.messages
    assert message.role == "user"
    assert message.content.type == "text"
    text = message.content.text
    assert text.startswith("Help me run a backtest of the model lstm-v2 on Apple in CandleStack")
    assert "1. get_me" in text
    assert text.endswith(RULES)


@pytest.mark.parametrize("prompt", PROMPTS, ids=lambda p: p.name)
def test_every_prompt_reads_the_docs_and_names_tools_that_exist(prompt) -> None:
    tools = {tool.name for tool in asyncio.run(build_server("prompts").list_tools())}
    arguments = {"backtest": "bt_7f3k2m9qx4"} if "backtest" in prompt.fn.__annotations__ else {}
    text = prompt.fn(**arguments)

    named = set(re.findall(r"\b[a-z]+(?:_[a-z0-9]+){1,3}\b", text))
    assert "get_docs" in text
    assert named & tools, "A prompt names the tools to call"
    unknown = {name for name in named - tools if name.startswith(("get_", "list_", "run_"))}
    assert not unknown
    assert _pages(text) <= PAGES


def test_the_tools_name_only_pages_that_exist() -> None:
    tools = asyncio.run(build_server("prompts").list_tools())
    named = {page for tool in tools for page in _pages(tool.description or "")}

    assert named
    assert named <= PAGES


def test_the_instructions_send_the_agent_to_the_docs_and_stay_short() -> None:
    assert "get_docs" in INSTRUCTIONS
    assert "never invent a formula" in INSTRUCTIONS
    assert "table" in INSTRUCTIONS
    assert "`url`" in INSTRUCTIONS
    assert "does not do something" in INSTRUCTIONS
    # Clients send the instructions with every turn: a few hundred tokens at most.
    assert len(INSTRUCTIONS) < 2000
