"""What the server tells an agent about CandleStack: its instructions, sent when a client
connects, the ``docs`` resource with the links to the public docs, and where the get_docs tool
reads the docs (``library``)."""

WEBSITE = "https://candlestack.tech"
DOCS = f"{WEBSITE}/docs"
# The website's index of every docs page for agents (llmstxt.org); each page is also Markdown
# at /docs/<page>.md. Both are built with the site, so they match the published pages.
DOCS_INDEX = f"{WEBSITE}/llms.txt"
AGENTS_DOCS = f"{DOCS}/agents/"
MCP_DOCS = f"{DOCS}/mcp/"
TOOLS_DOCS = f"{MCP_DOCS}tools/"
PROBLEMS = "https://candlestack.tech/problems/"
API_REFERENCE = "https://app.candlestack.tech/api/v1/docs"
# The website's pages of the manifest and the result document (candlestack-tech/website#63, #64).
MANIFEST_DOCS = f"{DOCS}/manifest/"
RESULT_DOCS = f"{DOCS}/backtest-json/"
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

A tool answers JSON; its text leaves out the fields that are null. A failed tool call answers a \
problem as JSON: its type (https://candlestack.tech/problems/<slug>) names what went wrong and its \
detail says what to do; rate-limited also gives retry_after_s, the seconds to wait.

Answers about a backtest or a model carry its `url`, its page in the app; a comparison and a \
sweep of 2 to 4 backtests (a ranked sweep: its best 4) carry `url`, the app's Compare page with \
them side by side. Give the user these links whenever you show results, so they can see the \
charts and the details themselves.

Read the docs with get_docs (without a page it lists them) before you explain how the engine, \
a manifest, a metric or an error works, and answer from them: never invent a formula or a \
behaviour, and say so when CandleStack does not do something. Show metrics in a table. The \
server's prompts walk through common tasks. On the web: {AGENTS_DOCS} and {MCP_DOCS}; the \
resource {DOCS_URI} links them."""

DOCS_TEXT = f"""\
# CandleStack docs for agents

The tool get_docs lists the docs pages of candlestack.tech and answers each one as Markdown, with
the JSON Schemas below and the problem types (page api/errors).

- Agents: {AGENTS_DOCS} (what the MCP server and the API can do, which to pick)
- MCP server: {MCP_DOCS} (address, sign-in, scopes, what is web only and why)
- Tool reference: {TOOLS_DOCS} (every tool with an example call and answer)
- Manifest, how a model's input is described: {MANIFEST_DOCS} (JSON Schema: {MANIFEST_SCHEMA})
- Backtest result JSON: {RESULT_DOCS} (JSON Schema: {RESULT_SCHEMA})
- Problems, what each error type means: {PROBLEMS}
- HTTP API reference: {API_REFERENCE}
"""
