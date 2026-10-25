"""Prints the JSON Schema of the manifest as stable JSON:
``python -m candlestack.preprocessing.schema``.

The committed ``docs/manifest.schema.json`` must match this output (a unit test checks). From
``backend/``:

    uv run python -m candlestack.preprocessing.schema > ../docs/manifest.schema.json
"""

import json
import sys

from candlestack.preprocessing.manifest import Manifest

DIALECT = "https://json-schema.org/draft/2020-12/schema"


def render() -> str:
    schema = {"$schema": DIALECT, **Manifest.model_json_schema(by_alias=True)}
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    # Bytes, so Windows does not turn the newlines into CRLF.
    sys.stdout.buffer.write(render().encode())
