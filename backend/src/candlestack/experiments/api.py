"""HTTP API of the experiments module: the signed-in user's backtests under
``/api/v1/backtests`` (the list, starting one, its result document and its download) and the
Dashboard under ``/api/v1/dashboard``.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response

from candlestack.accounts import CurrentUserDep
from candlestack.core import NO_STORE, ProblemError, problem_doc, require_same_origin
from candlestack.data import data_problems
from candlestack.experiments.dependencies import BacktestRunsDep, BacktestsServiceDep
from candlestack.experiments.document import BacktestDocument, BacktestIn
from candlestack.experiments.errors import (
    BacktestNotFinished,
    BacktestNotFound,
    BacktestRunning,
    BacktestsUnavailable,
    InstrumentNotForModel,
    InvalidBacktest,
    ModelNeedsSetup,
    ModelNotFound,
    NoDocument,
)
from candlestack.experiments.schemas import (
    MODEL_NAME_MAX,
    PAGE_SIZE,
    QUERY_MAX,
    BacktestOut,
    BacktestShow,
    BacktestSort,
    BacktestsPage,
    BacktestUpdate,
    DashboardOut,
    SortOrder,
)
from candlestack.experiments.service import ListQuery

router = APIRouter(tags=["backtests"])

# A page number far past any user's backtests; the limit keeps the offset a small integer.
PAGE_MAX = 100_000

BacktestId = Annotated[
    str, Path(description="Public ID of the backtest", examples=["bt_7f3k2m9qx4"])
]


_NOT_SIGNED_IN = problem_doc(401, "Not signed in (`not-signed-in`).")


def list_query(
    *,
    q: Annotated[
        str,
        Query(
            max_length=QUERY_MAX,
            description=(
                "Search: part of the number (`#12` or `12`), of an instrument's symbol or of the "
                "model's name, in any case"
            ),
            examples=["aapl"],
        ),
    ] = "",
    show: Annotated[
        BacktestShow, Query(description="All, the starred or the failed backtests")
    ] = BacktestShow.all,
    model: Annotated[
        str | None,
        Query(
            max_length=MODEL_NAME_MAX,
            description="Only the backtests of the model with this name, in any case",
            examples=["blesk-novol-8.keras"],
        ),
    ] = None,
    sort: Annotated[BacktestSort, Query(description="What to sort by")] = BacktestSort.default,
    order: Annotated[SortOrder, Query(description="Direction of the sort")] = SortOrder.desc,
    page: Annotated[
        int, Query(ge=1, le=PAGE_MAX, description=f"The page, from 1, of {PAGE_SIZE} backtests")
    ] = 1,
) -> ListQuery:
    return ListQuery(query=q, show=show, model=model, sort=sort, order=order, page=page)


@router.get("/api/v1/backtests", responses=_NOT_SIGNED_IN)
async def list_backtests(
    query: Annotated[ListQuery, Depends(list_query)],
    user: CurrentUserDep,
    service: BacktestsServiceDep,
    response: Response,
) -> BacktestsPage:
    """A page of the user's backtests that match the search and the filters, and what the
    filter panel shows: the counts of all, starred and failed backtests and every model the
    backtests used. A page past the last one is empty."""
    response.headers.update(NO_STORE)
    return await service.backtests(user.id, query)


@router.patch(
    "/api/v1/backtests/{backtest_id}",
    responses={
        **_NOT_SIGNED_IN,
        **problem_doc(
            403, "The request does not come from the app's own pages (`cross-origin-request`)."
        ),
        **problem_doc(404, "The user has no backtest with this ID (`not-found`)."),
    },
    dependencies=[Depends(require_same_origin)],
)
async def update_backtest(
    backtest_id: BacktestId,
    body: BacktestUpdate,
    user: CurrentUserDep,
    service: BacktestsServiceDep,
    response: Response,
) -> BacktestOut:
    """Stars, unstars or renames the backtest; a field left out stays as it is."""
    response.headers.update(NO_STORE)
    try:
        return await service.update(user.id, backtest_id, body)
    except BacktestNotFound:
        raise ProblemError(
            404, "not-found", "Not Found", "You have no backtest with this ID."
        ) from None


@router.get("/api/v1/dashboard", responses=_NOT_SIGNED_IN)
async def dashboard(
    user: CurrentUserDep, service: BacktestsServiceDep, response: Response
) -> DashboardOut:
    """Everything the Dashboard shows: the counts of the user's backtests, the newest ones,
    the best finished ones by Sharpe ratio, return, drawdown and win rate, and the best
    starred ones."""
    response.headers.update(NO_STORE)
    return await service.dashboard(user.id)


# --- Running a backtest and its result document -------------------------------------------

_JSON = "application/json"
_SOURCE_ERRORS = {
    **problem_doc(
        502, "A data source sent candles that failed validation (`source-data-invalid`)."
    ),
    **problem_doc(
        503,
        "A data source cannot be reached (`source-unavailable`), or for a new backtest the "
        "worker that runs the models (`backtests-unavailable`).",
    ),
}
_NOT_FOUND = problem_doc(404, "The user has no backtest with this ID (`not-found`).")


def _problem(error: Exception) -> ProblemError | None:  # noqa: C901, PLR0911 (one per error)
    match error:
        case ModelNotFound() | BacktestNotFound():
            what = "model" if isinstance(error, ModelNotFound) else "backtest"
            return ProblemError(404, "not-found", "Not Found", f"You have no {what} with this ID.")
        case ModelNeedsSetup():
            return ProblemError(
                409,
                "model-needs-setup",
                "Model needs a manifest",
                "The model has no manifest yet, so CandleStack cannot build its input. Add one "
                "on the Models page, then start the backtest.",
            )
        case InstrumentNotForModel():
            market = "stocks" if error.session == "regular" else "crypto pairs"
            hours = "US trading hours" if error.session == "regular" else "around the clock"
            return ProblemError(
                422,
                "instrument-not-for-model",
                "Instrument does not fit the model",
                f"The model reads candles of {hours}, so it runs on {market}. Choose one of them.",
                extensions={"session": error.session, "market": error.market},
            )
        case InvalidBacktest():
            return ProblemError(422, "validation", "Invalid request", error.detail)
        case BacktestRunning():
            return ProblemError(
                409,
                "backtest-running",
                "A backtest is running",
                "Another backtest of yours is running. Start this one when it has finished.",
            )
        case BacktestsUnavailable():
            return ProblemError(
                503,
                "backtests-unavailable",
                "Backtests unavailable",
                "Backtests cannot run right now: the worker that runs the models cannot be "
                "reached. Try again later.",
            )
        case NoDocument():
            return ProblemError(
                409,
                "no-document",
                "No result document",
                "This backtest is test data from before backtests ran in the app, so it has no "
                "result document.",
            )
        case BacktestNotFinished():
            detail = (
                "The backtest is still running. Download it once it has finished."
                if error.status == "running"
                else "The backtest failed, so it has no results to download."
            )
            return ProblemError(409, "backtest-not-finished", "Backtest not finished", detail)
    return None


@contextmanager
def _problems() -> Iterator[None]:
    """Raises the errors of starting a backtest, of its document and of the market data they
    load as problem details."""
    with data_problems():
        try:
            yield
        except Exception as error:
            problem = _problem(error)
            if problem is None:
                raise
            raise problem from error


def _json(
    content: bytes,
    response: Response,
    status: int = 200,
    headers: dict[str, str] | None = None,
) -> Response:
    """The JSON written already as the response, with the session's cookie ``current_user`` set
    on ``response`` when the session slid on (FastAPI drops it from a returned response)."""
    sent = Response(content, status, headers={**NO_STORE, **(headers or {})}, media_type=_JSON)
    for cookie in response.headers.getlist("set-cookie"):
        sent.headers.append("set-cookie", cookie)
    return sent


@router.post(
    "/api/v1/backtests",
    status_code=201,
    response_model=BacktestDocument,
    responses={
        **_NOT_SIGNED_IN,
        **problem_doc(
            403, "The request does not come from the app's own pages (`cross-origin-request`)."
        ),
        **problem_doc(
            404,
            "The user has no model with this ID (`not-found`), or the instrument is not in the "
            "catalog (`instrument-not-found`).",
        ),
        **problem_doc(
            409,
            "The model has no manifest (`model-needs-setup`), or another backtest of the user "
            "is running (`backtest-running`).",
        ),
        **problem_doc(
            422,
            "An invalid setup (`validation`), a model of another market "
            "(`instrument-not-for-model`), a period outside the data (`period-out-of-range`), "
            "without the candles the model needs before it (`warm-up-out-of-range`), or with "
            "more candles than allowed (`too-many-candles`).",
        ),
        **_SOURCE_ERRORS,
    },
    dependencies=[Depends(require_same_origin)],
)
async def start_backtest(
    body: BacktestIn, user: CurrentUserDep, runs: BacktestRunsDep, response: Response
) -> Response:
    """Starts a backtest of one of the user's models on one instrument and period and returns
    its document, `running`, with its number. Checked first: the model is the user's and has a
    manifest, the instrument fits the model, the period with the candles the model needs before
    it fits the data and the candle limit, the user has no other backtest running, and the
    worker that runs the models can be reached. The run goes on after the response; the
    document (`GET /api/v1/backtests/{backtest_id}`) has the results once it finished."""
    with _problems():
        backtest, content = await runs.start(user.id, body)
    return _json(content, response, 201, {"Location": f"/api/v1/backtests/{backtest.id}"})


@router.get(
    "/api/v1/backtests/{backtest_id}",
    response_model=BacktestDocument,
    responses={
        **_NOT_SIGNED_IN,
        **_NOT_FOUND,
        **problem_doc(
            409, "The backtest is test data that has no result document (`no-document`)."
        ),
        **_SOURCE_ERRORS,
    },
)
async def get_backtest(
    backtest_id: BacktestId, user: CurrentUserDep, runs: BacktestRunsDep, response: Response
) -> Response:
    """The result document of the backtest, whatever its status (`results` is null until it
    finished), in the light view: a series of more than 10,000 rows has one point per UTC day,
    and only the newest 10,000 trades are in it (`trades.complete` false)."""
    with _problems():
        _, content = await runs.document(user.id, backtest_id, light=True)
    return _json(content, response)


@router.get(
    "/api/v1/backtests/{backtest_id}/download",
    response_model=BacktestDocument,
    responses={
        **_NOT_SIGNED_IN,
        **_NOT_FOUND,
        **problem_doc(
            409,
            "The backtest is running or failed (`backtest-not-finished`), or is test data "
            "without a result document (`no-document`).",
        ),
        **_SOURCE_ERRORS,
    },
)
async def download_backtest(
    backtest_id: BacktestId, user: CurrentUserDep, runs: BacktestRunsDep, response: Response
) -> Response:
    """The result document of a finished backtest with every row of its series and every
    trade, as a file to save."""
    with _problems():
        number, content = await runs.document(user.id, backtest_id, light=False)
    disposition = f'attachment; filename="candlestack-backtest-{number}.json"'
    return _json(content, response, headers={"Content-Disposition": disposition})
