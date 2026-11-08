"""MCP: the remote MCP server at ``/mcp``, through which Claude, ChatGPT, Cursor and any MCP
client use CandleStack. Its tools call the same services as the API's routes, never the API
over HTTP, and sign in with the same API keys and scopes.

Other modules import only from here (``from candlestack.mcp import mount_mcp``), never from the
submodules. ``candlestack.main`` mounts the server (``mount_mcp``) and runs it in the app's
lifespan (``serving_mcp``). Every tool follows the convention of ``convention`` and every
endpoint of the API has its entry in the parity table (``parity``).
``python -m candlestack.mcp.reference`` prints the tool reference (``docs/mcp-tools.json``).
"""

from candlestack.mcp.http import MCP_PATH, METADATA_PATH, mount_mcp, serving_mcp

__all__ = ["MCP_PATH", "METADATA_PATH", "mount_mcp", "serving_mcp"]
