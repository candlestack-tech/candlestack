"""The schemas tools/list sends: shorter than Pydantic writes them, the arguments the same, and
all the tools within a budget, since a client sends them to the model with every turn."""

import asyncio
import json
from typing import Annotated, Any

import jsonschema
from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from candlestack.experiments import BacktestIn
from candlestack.mcp.schema import SHORTER, compact_input, compact_output
from candlestack.mcp.server import TOOLS, build_server

# What tools/list costs a model: the characters of every tool's name, description and input
# schema as compact JSON (about 4 to a token). #459 cut it from 21,900 tokens to 12,800
# (50,470 characters for 42 tools); the budget leaves 10 % to grow. A new tool or a longer
# description that does not fit: say less, or move it to the tool's `details`, which only the
# tool reference shows; raise the budget only on purpose.
BUDGET_CHARS = 55_500


class Rule(BaseModel):
    """A rule."""

    name: str = Field(examples=["threshold"])
    title: str = Field(description="A field named title stays")


class Step(BaseModel):
    span: int = 3
    off: int | None = None


class Args(BaseModel):
    step: Step = Step()
    rule: Rule = Rule(name="threshold", title="t")
    limit: Annotated[float | None, Field(description="Optional")] = None
    either: int | str | None = None


def test_an_input_schema_leaves_out_what_tells_a_model_nothing() -> None:
    schema = compact_input(Args.model_json_schema())

    assert schema == {
        "$defs": {
            "Rule": {
                "description": "A rule.",
                "properties": {
                    "name": {"type": "string"},
                    "title": {"description": "A field named title stays", "type": "string"},
                },
                "required": ["name", "title"],
                "type": "object",
            },
            "Step": {
                "properties": {
                    "span": {"default": 3, "type": "integer"},
                    # Inside a model the null stays: a step sent back as null is off.
                    "off": {"anyOf": [{"type": "integer"}, {"type": "null"}], "default": None},
                },
                "type": "object",
            },
        },
        "properties": {
            # The default is the defaults of the model's fields.
            "step": {"$ref": "#/$defs/Step"},
            # A default of its own stays.
            "rule": {"$ref": "#/$defs/Rule", "default": {"name": "threshold", "title": "t"}},
            # An optional argument: left out means null.
            "limit": {"description": "Optional", "type": "number"},
            # More than one type besides null stays as it is.
            "either": {
                "anyOf": [{"type": "integer"}, {"type": "string"}, {"type": "null"}],
                "default": None,
            },
        },
        "type": "object",
    }


def _tools() -> dict[str, Any]:
    return {tool.name: tool for tool in asyncio.run(build_server("schema").list_tools())}


def _example(name: str) -> Any:
    return next(spec for spec in TOOLS if spec.name == name).example.result


def test_a_setup_and_the_defaults_sent_back_fit_the_short_schema() -> None:
    """get_backtest's `setup` and get_backtest_defaults' units go to start_backtest as they are,
    with the steps that are off as null."""
    schema = _tools()["start_backtest"].input_schema
    setup = _example("get_backtest").setup.model_dump(mode="json")
    defaults = _example("get_backtest_defaults").model_dump(mode="json")
    from_defaults = {
        "model": {"id": "mdl_k2m9qx47f3"},
        "data": {"instrument": {"id": "stock:AAPL"}, "period": defaults["period"]},
        "decision": defaults["decision"],
        "execution": defaults["execution"],
    }

    for arguments in (setup, from_defaults):
        assert arguments["decision"]["calibrate"] is None
        assert arguments["execution"]["drawdown_stop"] is None
        jsonschema.validate(arguments, schema)
        BacktestIn.model_validate(arguments)
    sweep = _tools()["run_sweep"].input_schema
    jsonschema.validate({"base": setup, "vary": {"rules": [setup["decision"]["rule"]]}}, sweep)


def test_the_default_rule_stays_in_the_schema() -> None:
    decision = _tools()["run_sweep"].input_schema["$defs"]["Decision"]

    assert decision["properties"]["rule"]["default"] == {"name": "threshold", "limit": 0.0}


def test_a_shared_model_takes_the_shorter_description() -> None:
    used: set[str] = set()
    schema = compact_input(BacktestIn.model_json_schema(), used)

    defs = schema["$defs"]
    assert "description" not in defs["ModelRef"]
    assert defs["ModelRef"]["properties"]["id"]["description"] == SHORTER["ModelRef.id"]
    assert defs["Execution"]["description"] == SHORTER["Execution"]
    assert {"ModelRef", "ModelRef.id", "Execution", "Period.start"} <= used


def test_every_shorter_description_is_used() -> None:
    """A renamed model or field leaves its entry behind: it fails here."""
    server = build_server("schema")
    raw = asyncio.run(MCPServer.list_tools(server))
    used: set[str] = set()
    for tool in raw:
        compact_input(tool.input_schema, used)

    assert sorted(set(SHORTER) - used) == []


def test_an_output_schema_keeps_its_nulls() -> None:
    schema = compact_output(Args.model_json_schema())

    assert "title" not in schema
    assert "title" not in schema["$defs"]["Rule"]
    assert schema["$defs"]["Rule"]["properties"]["title"]["description"]
    assert {"type": "null"} in schema["properties"]["limit"]["anyOf"]
    # A default is data: its keys stay, even one named title.
    assert schema["properties"]["rule"]["default"] == {"name": "threshold", "title": "t"}


def test_tools_list_sends_the_shorter_schemas() -> None:
    server = build_server("schema")
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
    text = json.dumps([tool.input_schema for tool in tools.values()])

    assert '"title"' not in text
    assert '"discriminator"' not in text
    assert "Fee Bps" not in text
    assert "title" not in json.dumps(tools["get_me"].output_schema)
    # Worked out once.
    assert asyncio.run(server.list_tools()) is asyncio.run(server.list_tools())


def test_the_tools_stay_within_their_budget() -> None:
    tools = asyncio.run(build_server("budget").list_tools())
    size = sum(
        len(
            json.dumps(
                {"name": t.name, "description": t.description, "inputSchema": t.input_schema},
                separators=(",", ":"),
            )
        )
        for t in tools
    )

    assert size <= BUDGET_CHARS, (
        f"tools/list costs {size} characters, over the budget of {BUDGET_CHARS}: shorten the "
        "descriptions or move what a model needs only now and then to `details`"
    )
