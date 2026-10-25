"""Prints the JSON Schema of the backtest result document as stable JSON:
``python -m candlestack.experiments.json_schema``.

The committed ``docs/backtest.schema.json`` must match this output (a unit test checks). From
``backend/``:

    uv run python -m candlestack.experiments.json_schema > ../docs/backtest.schema.json
"""

import json
import sys

from candlestack.experiments.document import BacktestDocument

DIALECT = "https://json-schema.org/draft/2020-12/schema"


def render() -> str:
    schema = {
        "$schema": DIALECT,
        **BacktestDocument.model_json_schema(by_alias=True, mode="serialization"),
    }
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    # Bytes, so Windows does not turn the newlines into CRLF.
    sys.stdout.buffer.write(render().encode())
