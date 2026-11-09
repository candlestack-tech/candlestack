"""The tools' schemas as tools/list sends them: shorter than Pydantic writes them, because a
client sends every tool's input schema to the model with every turn.

What goes is what tells a model nothing the rest does not: the titles Pydantic makes of the
names (``"Fee Bps"``), the OpenAPI ``discriminator`` (each rule's ``name`` const says the same),
an object default that repeats the defaults of its fields, the examples (the tool reference has
an example call) and, for an optional argument, its ``null`` (left out means the same). The
input schemas of the shared models (``experiments``) take a shorter description from
``SHORTER``, where the API's names its docs and the result document. The arguments are still
checked against the tool's signature, not this schema, so a call works as before.

An output schema loses only its titles and the discriminator: clients check the structured
content against it, which a ``null`` left out would fail.
"""

from typing import Any

# The description an input schema gives a shared model (`Model`) or its field (`Model.field`)
# instead of the API's; None leaves it out, where the arguments' own say it or it says nothing.
SHORTER: dict[str, str | None] = {
    "BacktestIn": None,
    "BacktestIn.benchmarks": None,
    "BenchmarkIn": None,
    "DataIn": "The timeframe and session are the model's.",
    "InstrumentRef": None,
    "ModelRef": None,
    "ModelRef.id": "One of the user's models",
    "Period.start": None,
    "Period.end": None,
    "Decision": "How the predictions become a signal: the steps apply in this order, null is off.",
    "EmaStep.span": None,
    "Execution": "Fractions: 0.05 is 5%; a basis point is 0.01%.",
    "SweepVary": "Each list replaces the base's value; left out or empty keeps it. Every "
    "combination is one backtest.",
    "SweepVary.instruments": "A benchmark of the base's instrument follows each backtest's own",
    "SweepVary.models": "A model's manifest sets its representation and timeframe: vary models "
    "to vary those",
    "SweepVary.rules": "In place of `decision.rule`",
    "SweepMetric": None,
    "BacktestShow": None,
    "BacktestSort": "`default` is newest first and ignores `order`; `when` sorts like `number`.",
    "BacktestStatus": "`failed` includes those cancelled before they ran.",
    "ExperimentSort": "`newest` is the newest published first and ignores `order`.",
    "Representation": None,
    "SetupTimeframe": None,
    "InstrumentMarket": None,
    "BacktestTimeframe": None,
    "Benchmark": None,
    "Exchange": None,
    "Timeframe": None,
}

# The keywords whose value maps names to schemas, not keywords to values.
_NAMED = ("properties", "$defs")
_NULL = {"type": "null"}


def _shorter(schema: dict[str, Any], key: str, used: set[str]) -> dict[str, Any]:
    if key not in SHORTER:
        return schema
    used.add(key)
    text = SHORTER[key]
    rest = {k: v for k, v in schema.items() if k != "description"}
    return rest if text is None else {**rest, "description": text}


def _dropped(key: str, value: Any) -> bool:
    """A keyword an input schema leaves out: it tells a model nothing the rest does not."""
    return key in ("title", "discriminator", "examples") or (
        key == "default" and isinstance(value, (dict, list))
    )


def _without_null(schema: dict[str, Any]) -> dict[str, Any]:
    """An optional argument's schema without the null, which leaving it out means."""
    if schema.get("default", ...) is not None or _NULL not in schema.get("anyOf", []):
        return schema
    options = [option for option in schema["anyOf"] if option != _NULL]
    if len(options) != 1:
        return schema
    rest = {k: v for k, v in schema.items() if k not in ("anyOf", "default")}
    return {**options[0], **rest}


def _input(node: Any, used: set[str], owner: str | None = None) -> Any:
    """`node` shortened; `owner` names the shared model whose schema it is."""
    if isinstance(node, list):
        return [_input(item, used) for item in node]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for key, value in node.items():
        if _dropped(key, value):
            continue
        if key == "$defs":
            out[key] = {
                name: _shorter(_input(schema, used, name), name, used)
                for name, schema in value.items()
            }
        elif key == "properties":
            out[key] = {
                name: _shorter(_input(schema, used), f"{owner}.{name}", used)
                for name, schema in value.items()
            }
        else:
            out[key] = _input(value, used)
    return _without_null(out)


def compact_input(schema: dict[str, Any], used: set[str] | None = None) -> dict[str, Any]:
    """The input schema tools/list sends; adds the keys of ``SHORTER`` it used to `used`."""
    return _input(schema, set() if used is None else used)


def compact_output(node: Any) -> Any:
    """The output schema tools/list sends: without titles and the discriminator."""
    if isinstance(node, list):
        return [compact_output(item) for item in node]
    if not isinstance(node, dict):
        return node
    return {
        key: (
            {name: compact_output(schema) for name, schema in value.items()}
            if key in _NAMED
            else compact_output(value)
        )
        for key, value in node.items()
        if key not in ("title", "discriminator")
    }
