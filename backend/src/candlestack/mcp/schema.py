"""The tools' schemas as tools/list sends them: shorter than Pydantic writes them, because a
client sends every tool's input schema to the model with every turn.

What goes is what tells a model nothing the rest does not: the titles Pydantic makes of the
names (``"Fee Bps"``), the OpenAPI ``discriminator`` (each rule's ``name`` const says the same),
an object default that is the defaults of its model's fields, the examples (the tool reference
has an example call) and, for a tool's own optional argument, its ``null`` (left out means the
same; inside a model the null stays, as a step that is off). The
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
# The keywords whose value is data, never a schema: kept as it is.
_DATA = ("default", "const", "enum", "examples")
_NULL = {"type": "null"}


def _redundant_default(node: dict[str, Any], defs: dict[str, Any]) -> bool:
    """An object default that is the defaults of the fields of the model it refers to, so
    leaving it out says the same. Any other default, such as a rule's, stays."""
    default, ref = node.get("default"), node.get("$ref")
    if not isinstance(default, dict) or not isinstance(ref, str):
        return False
    fields = defs.get(ref.rsplit("/", 1)[-1], {}).get("properties", {})
    own = {name: field["default"] for name, field in fields.items() if "default" in field}
    return default == own


def _without_null(schema: Any) -> Any:
    """An optional argument's schema without the null, which leaving it out means."""
    if not isinstance(schema, dict):
        return schema
    if schema.get("default", ...) is not None or _NULL not in schema.get("anyOf", []):
        return schema
    options = [option for option in schema["anyOf"] if option != _NULL]
    if len(options) != 1:
        return schema
    rest = {k: v for k, v in schema.items() if k not in ("anyOf", "default")}
    return {**options[0], **rest}


class _Shortener:
    """Shortens one input schema; `used` collects the keys of ``SHORTER`` it applied."""

    def __init__(self, defs: dict[str, Any], used: set[str]) -> None:
        self.defs = defs
        self.used = used

    def shorter(self, schema: dict[str, Any], key: str) -> dict[str, Any]:
        if key not in SHORTER:
            return schema
        self.used.add(key)
        text = SHORTER[key]
        rest = {k: v for k, v in schema.items() if k != "description"}
        return rest if text is None else {**rest, "description": text}

    def node(self, node: Any, owner: str | None = None) -> Any:
        """`node` shortened; `owner` names the shared model whose schema it is."""
        if isinstance(node, list):
            return [self.node(item) for item in node]
        if not isinstance(node, dict):
            return node
        redundant = _redundant_default(node, self.defs)
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in ("title", "discriminator", "examples") or (key == "default" and redundant):
                continue
            if key == "$defs":
                out[key] = {
                    name: self.shorter(self.node(schema, name), name)
                    for name, schema in value.items()
                }
            elif key == "properties":
                out[key] = {
                    name: self.shorter(self.node(schema), f"{owner}.{name}")
                    for name, schema in value.items()
                }
            elif key in _DATA:
                out[key] = value
            else:
                out[key] = self.node(value)
        return out


def compact_input(schema: dict[str, Any], used: set[str] | None = None) -> dict[str, Any]:
    """The input schema tools/list sends; adds the keys of ``SHORTER`` it used to `used`.

    Only a tool's own optional arguments lose their null: inside a model it stays, so a
    `setup` or the defaults sent back as they are, with a step that is off, still fit."""
    shortener = _Shortener(schema.get("$defs", {}), set() if used is None else used)
    out = shortener.node(schema)
    if "properties" in out:
        out["properties"] = {
            name: _without_null(value) for name, value in out["properties"].items()
        }
    return out


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
            else value
            if key in _DATA
            else compact_output(value)
        )
        for key, value in node.items()
        if key not in ("title", "discriminator")
    }
