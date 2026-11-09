"""The server's prompts: plans for the tasks users ask most, which a client offers as commands
(prompts/list) and sends to its model as the user's message (prompts/get).

Each names the tools to call in their order and the docs pages to read with get_docs, so the
agent answers from the docs instead of guessing. The pages named are those the website lists;
a unit test keeps them to the pages known to exist.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from pydantic import Field

# What every prompt ends with: the server's instructions say the same, for the agent that
# skipped them.
RULES = (
    "Before you explain how the engine, a manifest, a metric or an error works, read the page "
    "with get_docs and answer from it: never invent a formula or a behaviour, and say so when "
    "CandleStack does not do something. Show the metrics in a table and give me the `url` of "
    "each backtest, comparison or model you show."
)

Optional = Annotated[str | None, Field(description="Leave it out to choose with the user")]
BacktestArg = Annotated[str, Field(description="The backtest's ID (bt_...), name or number")]


def _plan(task: str, *steps: str) -> str:
    numbered = "\n".join(f"{i}. {step}" for i, step in enumerate(steps, 1))
    return f"{task}\n\n{numbered}\n\n{RULES}"


def _named(what: str, value: str | None) -> str:
    return f" {what} {value}" if value else ""


def first_backtest(
    model: Annotated[str | None, Field(description="The model's name or ID, if it has one")] = None,
    instrument: Annotated[
        str | None, Field(description="What to test it on, such as BTC or Apple")
    ] = None,
) -> str:
    """Run a first backtest from scratch: upload a model if needed, give it a manifest, pick an
    instrument and a period, run it and read the results."""
    return _plan(
        f"Help me run a backtest{_named('of the model', model)}{_named('on', instrument)} in "
        "CandleStack, from scratch.",
        "get_me: check who I am and that the scopes include backtests (models, to upload one).",
        "list_models. A model that is not `ready` needs a manifest: read get_docs page manifest, "
        "build it with preprocessing_catalog, check it with validate_manifest, then set_manifest. "
        "No model yet: create_model_upload, I send the file, get_model_upload until `done`.",
        "search_instruments for the instrument, then get_instrument for the period it has "
        "candles. A stock fits a model of US trading hours, a crypto pair one of 24/7.",
        "get_backtest_defaults: use them for every setting I do not name.",
        "validate_backtest, then start_backtest with the same arguments.",
        "wait_for_backtest until `done`, then get_backtest.",
        "Show the 12 statistics against buy and hold. Read get_docs page metrics before you say "
        "what a statistic means, and get_docs page engine before you say how it traded.",
    )


def analyze_backtest(backtest: BacktestArg) -> str:
    """Analyze one backtest: its results, its trades and what they say."""
    return _plan(
        f"Analyze my backtest {backtest}.",
        "Find it: get_backtest with its ID, or list_backtests with `query` for a name or number.",
        "get_backtest: setup and statistics. Read get_docs page metrics for what each statistic "
        "means and get_docs page engine for what each setting does.",
        "If it finished, get_backtest with trades_per_page 50 for the trades, and equity_points "
        "for the curve when the shape matters.",
        "Answer: the statistics in a table against the benchmark, what the trades show (win "
        "rate, size of wins and losses, exits by stop loss) and the backtest's `url`.",
    )


def compare_my_backtests(
    backtests: Annotated[
        str | None, Field(description="The backtests' IDs or names, comma separated")
    ] = None,
) -> str:
    """Compare backtests side by side and say which is best and why."""
    return _plan(
        f"Compare my backtests{f': {backtests}' if backtests else ''}.",
        "Without IDs, find them: list_backtests (filters and sort) or get_dashboard; ask me "
        "which when it is not clear.",
        "compare_backtests with their IDs and setup_mode diff, to see what differs.",
        "Read get_docs page metrics and get_docs page sweeps (section Comparing backtests) "
        "before you weigh one statistic against another.",
        "Answer: a table of the statistics with the best of each marked, what differs in the "
        "setups, which one is best for what, and the comparison's and each backtest's `url`.",
    )


def find_best_model(
    instrument: Optional = None,
    metric: Annotated[
        str | None, Field(description="What best means, such as Sharpe ratio or drawdown")
    ] = None,
) -> str:
    """Find the user's best model, by their backtests or by new ones on the same data."""
    return _plan(
        f"Find my best model{_named('on', instrument)}{_named('by', metric)}.",
        "list_models: the models, how many backtests each has and its best Sharpe ratio.",
        "list_backtests sorted by the metric (filtered by instrument when given) for the best "
        "finished backtest of each model.",
        "Backtests on different instruments or periods do not compare fairly. If they differ, "
        "offer a sweep of the models on one instrument and period (validate_sweep, run_sweep, "
        "wait_for_sweep), and ask before you start it.",
        "compare_backtests on the best backtest of each model.",
        "Answer: a table of the models with their statistics, the best one and why, and the "
        "`url` of each backtest and model.",
    )


def explain_poor_backtest(backtest: BacktestArg) -> str:
    """Explain why a backtest did poorly or failed, from its results and the docs."""
    return _plan(
        f"Explain why my backtest {backtest} did poorly.",
        "get_backtest. A failed one: its error is a problem; read get_docs page api/errors for "
        "what it means and what to do.",
        "A finished one: compare it with buy and hold, and read its trades (trades_per_page 50): "
        "many small losses, stops hit, fees and slippage, few trades, a long drawdown.",
        "Read get_docs page engine (sections Decision steps and Execution settings) and "
        "get_docs page metrics, so you explain how it traded from the docs, not from memory.",
        "get_model and get_manifest: does the model's input fit the instrument and timeframe?",
        "Answer: the statistics in a table, the causes with the evidence for each, what to "
        "change next (one setting at a time), and the backtest's `url`.",
    )


def set_up_sweep(
    model: Optional = None,
    instrument: Optional = None,
) -> str:
    """Set up a sweep: one setup run over several models, instruments, periods or rules."""
    return _plan(
        f"Set up a sweep{_named('of the model', model)}{_named('on', instrument)}.",
        "Ask me what to vary: models, instruments, periods or decision rules (2 to 20 backtests "
        "in all) and which statistic ranks them.",
        "Build `base` as for start_backtest: list_models, search_instruments, get_instrument "
        "for the period, get_backtest_defaults for the rest. Read get_docs page sweeps, and "
        "get_docs page mcp/tools (section run_sweep) for the shape of `vary`.",
        "validate_sweep and show me the count and the setups; start run_sweep only when I agree.",
        "wait_for_sweep until `done`, then get_sweep for the ranking and compare_backtests for "
        "the best ones.",
        "Answer: the ranking in a table, what it shows and what it cannot, and the `url`s.",
    )


@dataclass(frozen=True, slots=True)
class PromptSpec:
    fn: Callable[..., str]
    name: str
    title: str


PROMPTS: tuple[PromptSpec, ...] = (
    PromptSpec(first_backtest, "first_backtest", "Run a first backtest"),
    PromptSpec(analyze_backtest, "analyze_backtest", "Analyze a backtest"),
    PromptSpec(compare_my_backtests, "compare_my_backtests", "Compare my backtests"),
    PromptSpec(find_best_model, "find_best_model", "Find my best model"),
    PromptSpec(explain_poor_backtest, "explain_poor_backtest", "Why a backtest did poorly"),
    PromptSpec(set_up_sweep, "set_up_sweep", "Set up a sweep"),
)
