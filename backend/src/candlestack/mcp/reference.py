"""Prints the tool reference as stable JSON: ``python -m candlestack.mcp.reference``.

The website builds its tool reference (``candlestack.tech/docs/mcp/tools/``) from the committed
``docs/mcp-tools.json``, which must match this output (a unit test checks). From ``backend/``:

    uv run python -m candlestack.mcp.reference > ../docs/mcp-tools.json

For each tool: what ``tools/list`` sends (name, title, description, annotations), its input and
output schemas as Pydantic writes them, with every description and default (``tools/list``
sends them shorter, ``schema``), the details its description leaves out, the scopes an API key
needs, an example call and answer, and the endpoints of the API it does the work of; then the
server's instructions, resources and prompts, and the endpoints that stay web only, with the
reason.
"""

import asyncio
import json
import sys
from typing import Any

from mcp.server.mcpserver import MCPServer

from candlestack.mcp.parity import endpoints_of, web_only
from candlestack.mcp.server import build_server

# The reference describes the server, not one release of it.
SNAPSHOT_VERSION = "snapshot"


async def _reference() -> dict[str, Any]:
    server = build_server(SNAPSHOT_VERSION)
    tools = []
    # The schemas whole, for people: a model reads the short ones (``server.list_tools``).
    for tool in await MCPServer.list_tools(server):
        spec = server.specs[tool.name]
        tools.append(
            {
                **tool.model_dump(mode="json", by_alias=True, exclude_none=True),
                **({"details": spec.details} if spec.details else {}),
                "scopes": [scope.value for scope in spec.scopes],
                "example": {
                    "arguments": spec.example.arguments,
                    "result": spec.example.result.model_dump(mode="json"),
                },
                "endpoints": endpoints_of(tool.name),
            }
        )
    resources = [
        resource.model_dump(mode="json", by_alias=True, exclude_none=True)
        for resource in await server.list_resources()
    ]
    prompts = [
        prompt.model_dump(mode="json", by_alias=True, exclude_none=True)
        for prompt in await server.list_prompts()
    ]
    return {
        "server": {"name": server.name, "title": server.title, "instructions": server.instructions},
        "resources": resources,
        "prompts": prompts,
        "tools": tools,
        "webOnly": [
            {"endpoint": endpoint, "reason": reason} for endpoint, reason in web_only().items()
        ],
    }


def render() -> str:
    return json.dumps(asyncio.run(_reference()), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    # Bytes, so Windows does not turn the newlines into CRLF.
    sys.stdout.buffer.write(render().encode())
