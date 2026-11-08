"""What the server tells an agent about CandleStack: its instructions, sent when a client
connects, and the ``docs`` resource with the links to the public docs."""

DOCS = "https://candlestack.tech/docs"
AGENTS_DOCS = f"{DOCS}/agents/"
MCP_DOCS = f"{DOCS}/mcp/"
TOOLS_DOCS = f"{MCP_DOCS}tools/"
PROBLEMS = "https://candlestack.tech/problems/"
API_REFERENCE = "https://app.candlestack.tech/api/v1/docs"
# The manifest and the result document are described in the repository until the website has
# their pages (candlestack-tech/website#63, #64): its pages then replace these two.
_REPOSITORY_DOCS = "https://github.com/candlestack-tech/candlestack/blob/main/docs"
MANIFEST_DOCS = f"{_REPOSITORY_DOCS}/preprocessing.md#the-manifest"
RESULT_DOCS = f"{_REPOSITORY_DOCS}/engine.md#the-result-document"
# The JSON Schemas committed in the repository, raw, so an agent can read them as they are.
_SCHEMAS = "https://raw.githubusercontent.com/candlestack-tech/candlestack/main/docs"
MANIFEST_SCHEMA = f"{_SCHEMAS}/manifest.schema.json"
RESULT_SCHEMA = f"{_SCHEMAS}/backtest.schema.json"

DOCS_URI = "candlestack://docs"

INSTRUCTIONS = f"""\
CandleStack is a lab for experiments on financial time series: a user uploads a trading model \
(Keras or ONNX), describes its input in a manifest and backtests it on market data, crypto from \
Binance and US stocks from Alpaca, then compares the results and publishes the good ones.

You act as the user who signed you in, with an API key or by allowing you on CandleStack (OAuth). \
The scopes of that sign-in decide which tools work: read (everything that only reads), \
backtests, models and publish. Call get_me first to see who you act for and what you may do. \
A tool that changes data says so in its annotations.

A failed tool call answers a problem as JSON: its type (https://candlestack.tech/problems/<slug>) \
names what went wrong and its detail says what to do.

Docs for agents: {AGENTS_DOCS} and for this server: {MCP_DOCS}. The resource {DOCS_URI} lists \
every docs page and reference."""

DOCS_TEXT = f"""\
# CandleStack docs for agents

- Agents: {AGENTS_DOCS} (what the MCP server and the API can do, which to pick)
- MCP server: {MCP_DOCS} (address, sign-in, scopes, what is web only and why)
- Tool reference: {TOOLS_DOCS} (every tool with an example call and answer)
- Manifest, how a model's input is described: {MANIFEST_DOCS} (JSON Schema: {MANIFEST_SCHEMA})
- Backtest result JSON: {RESULT_DOCS} (JSON Schema: {RESULT_SCHEMA})
- Problems, what each error type means: {PROBLEMS}
- HTTP API reference: {API_REFERENCE}
"""
