"""HTTP API of the experiments module: the signed-in user's backtests under
``/api/v1/backtests`` (the list, starting one, cancelling a queued one, its result document and
its download, deleting them), the backtests every account published under
``/api/v1/experiments``, the comparison of backtests under ``/api/v1/compare``, sweeps of many
backtests under ``/api/v1/sweeps`` and the Dashboard under ``/api/v1/dashboard``.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response

from candlestack.accounts import (
    API_KEY_SCHEME,
    BacktestsUserDep,
    CallerDep,
    CurrentUserDep,
    ReaderDep,
    Scope,
    heavy,
)
from candlestack.core import NO_STORE, ProblemError, problem_doc, require_same_origin
from candlestack.data import data_problems
from candlestack.experiments.compare import (
    COMPARE_MAX,
    COMPARE_MIN,
    IDS_MAX_LENGTH,
    CompareOut,
    compare,
    parse_ids,
)
from candlestack.experiments.deletes import DELETE_MAX, BacktestsDeleted, BacktestsDeleteIn
from candlestack.experiments.dependencies import (
    BacktestRunsDep,
    BacktestsServiceDep,
    DeletesDep,
    SweepsDep,
)
from candlestack.experiments.document import BacktestCheck, BacktestDocument, BacktestIn
from candlestack.experiments.errors import (
    BacktestLimitReached,
    BacktestNotFinished,
    BacktestNotFound,
    BacktestNotQueued,
    BacktestsUnavailable,
    CountMismatch,
    InstrumentNotForModel,
    InvalidBacktest,
    ModelNeedsSetup,
    ModelNotFound,
    NameTaken,
    NoDocument,
    NotComparable,
    QueueFull,
    SetupFailed,
    SweepNotFound,
    SweepQueueFull,
    SweepTooLarge,
)
from candlestack.experiments.schemas import (
    EXPERIMENTS_PAGE_SIZE,
    MODEL_NAME_MAX,
    PAGE_SIZE,
    QUERY_MAX,
    BacktestOut,
    BacktestShow,
    BacktestSort,
    BacktestsPage,
    BacktestUpdate,
    DashboardOut,
    ExperimentSort,
    ExperimentsPage,
    InstrumentMarket,
    Representation,
    SetupTimeframe,
    SortOrder,
)
from candlestack.experiments.service import ExperimentsQuery, ListQuery
from candlestack.experiments.store import QUEUE_MAX
from candlestack.experiments.sweeps import (
    SWEEP_MAX,
    SWEEP_MIN,
    SweepIn,
    SweepMetric,
    SweepOut,
    SweepPlan,
)

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
                "Search: part of the name, of the number (`#12` or `12`), of an instrument's "
                "symbol or of the model's name, in any case"
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


@contextmanager
def update_problems() -> Iterator[None]:
    """Raises the errors of starring, renaming, publishing or unpublishing a backtest
    (``BacktestsService.update``) as problem details: for the route and the MCP tools."""
    try:
        yield
    except BacktestNotFound:
        raise ProblemError(
            404, "not-found", "Not Found", "You have no backtest with this ID."
        ) from None
    except NameTaken as taken:
        raise ProblemError(
            409,
            "name-taken",
            "Name taken",
            f"You already have a backtest called {taken.name} (#{taken.number}). Pick another "
            "name.",
            headers=NO_STORE,  # it names a backtest of the user
        ) from None
    except NoDocument:
        raise ProblemError(
            409,
            "no-document",
            "No result document",
            "This backtest is test data from before backtests ran in the app, so it cannot be "
            "published.",
        ) from None
    except BacktestNotFinished:
        raise ProblemError(
            409,
            "backtest-not-finished",
            "Backtest not finished",
            "Only a finished backtest can be published.",
        ) from None


@router.patch(
    "/api/v1/backtests/{backtest_id}",
    responses={
        **_NOT_SIGNED_IN,
        **problem_doc(
            403, "The request does not come from the app's own pages (`cross-origin-request`)."
        ),
        **problem_doc(404, "The user has no backtest with this ID (`not-found`)."),
        **problem_doc(
            409,
            "Publishing a backtest that is queued, running or failed (`backtest-not-finished`), "
            "or test data without a result document (`no-document`); a name another backtest of "
            "the account has, ignoring case, as its name or generated name (`name-taken`, the "
            "detail names that backtest and its number).",
        ),
    },
    dependencies=[Depends(require_same_origin)],
    # An API key needs `publish` to publish or unpublish, `backtests` for the rest.
    openapi_extra={
        "security": [
            {API_KEY_SCHEME: [Scope.backtests.value]},
            {API_KEY_SCHEME: [Scope.publish.value]},
        ]
    },
)
async def update_backtest(
    backtest_id: BacktestId,
    body: BacktestUpdate,
    caller: CallerDep,
    service: BacktestsServiceDep,
    response: Response,
) -> BacktestOut:
    """Stars, unstars, renames, publishes or unpublishes the user's backtest; a field left out
    stays as it is. A name of null goes back to the generated name. Unpublishing takes it out
    of Experiments and out of other people's Compare. With an API key, publishing and unpublishing
    need the scope `publish`, the rest `backtests`."""
    needed = {
        Scope.publish if field == "public" else Scope.backtests for field in body.model_fields_set
    }
    caller.require(*(scope for scope in Scope if scope in needed))
    response.headers.update(NO_STORE)
    with update_problems():
        return await service.update(caller.user.id, backtest_id, body)


def experiments_query(  # noqa: PLR0913 (one argument per query parameter)
    *,
    q: Annotated[
        str,
        Query(
            max_length=QUERY_MAX,
            description=(
                "Search: part of the backtest's name, of an instrument's symbol, of the model's "
                "name or of the author's name, in any case"
            ),
            examples=["nvda"],
        ),
    ] = "",
    mine: Annotated[
        bool, Query(description="Only the backtests the signed-in user published")
    ] = False,
    author: Annotated[
        str | None,
        Query(
            max_length=32,
            description="Only the backtests of this account (`author.id`)",
            examples=["usr_7f3k2m9qx4"],
        ),
    ] = None,
    market: Annotated[InstrumentMarket | None, Query(description="Only this market")] = None,
    symbol: Annotated[
        str | None,
        Query(max_length=32, description="Only this instrument's symbol, in any case"),
    ] = None,
    representation: Annotated[
        Representation | None, Query(description="Only models that read this")
    ] = None,
    timeframe: Annotated[SetupTimeframe | None, Query(description="Only this timeframe")] = None,
    sort: Annotated[ExperimentSort, Query(description="What to sort by")] = ExperimentSort.newest,
    order: Annotated[SortOrder, Query(description="Direction of the sort")] = SortOrder.desc,
    page: Annotated[
        int,
        Query(
            ge=1,
            le=PAGE_MAX,
            description=f"The page, from 1, of {EXPERIMENTS_PAGE_SIZE} published backtests",
        ),
    ] = 1,
) -> ExperimentsQuery:
    return ExperimentsQuery(
        query=q,
        mine=mine,
        author=author,
        market=market,
        symbol=symbol,
        representation=representation,
        timeframe=timeframe,
        sort=sort,
        order=order,
        page=page,
    )


@router.get("/api/v1/experiments", responses=_NOT_SIGNED_IN)
async def list_experiments(
    query: Annotated[ExperimentsQuery, Depends(experiments_query)],
    user: CurrentUserDep,
    service: BacktestsServiceDep,
    response: Response,
) -> ExperimentsPage:
    """A page of Experiments: the backtests that accounts of the beta published, which every
    signed-in account can see, that match the search and the filters, with the counts and what
    the filters offer. Others see a published backtest's setup, results, charts, trades and what
    its model reads, never the model file. A page past the last one is empty."""
    response.headers.update(NO_STORE)
    return await service.experiments(user.id, query)


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
_NOT_FOUND = problem_doc(
    404,
    "Neither the user's backtest nor a published one has this ID (`not-found`): a backtest "
    "its author unpublished is not found.",
)


def _problem(error: Exception) -> ProblemError | None:  # noqa: C901, PLR0911, PLR0912 (one per error)
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
        case QueueFull():
            return ProblemError(
                409,
                "queue-full",
                "Queue full",
                f"You have {QUEUE_MAX} backtests waiting already. Start this one when one of "
                "them has started, or cancel one.",
                extensions={"limit": QUEUE_MAX},
            )
        case BacktestLimitReached():
            return _limit_problem(error)
        case CountMismatch():
            return ProblemError(
                409,
                "confirmation-mismatch",
                "Confirmation mismatch",
                f"{error.count} backtests match, which is not the count confirmed, so nothing was "
                "deleted. Check what you are deleting with the user, then confirm that count.",
                extensions={"count": error.count},
            )
        case BacktestNotQueued():
            detail = (
                "The backtest is running already, so it can no longer be cancelled."
                if error.status == "running"
                else "The backtest has ended, so there is nothing to cancel."
            )
            return ProblemError(409, "backtest-not-queued", "Backtest not queued", detail)
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
        case NotComparable(found=True):
            return ProblemError(
                409,
                "no-document",
                "No result document",
                f"{error.backtest_id} is test data from before backtests ran in the app, so it "
                "has no results to compare.",
                extensions={"id": error.backtest_id},
            )
        case NotComparable():
            return ProblemError(
                404,
                "not-found",
                "Not Found",
                f"Neither you nor a published backtest has the ID {error.backtest_id}.",
                extensions={"id": error.backtest_id},
            )
        case BacktestNotFinished():
            detail = {
                "queued": "The backtest is waiting in the queue. Download it once it has finished.",
                "running": "The backtest is still running. Download it once it has finished.",
            }.get(error.status, "The backtest failed, so it has no results to download.")
            return ProblemError(409, "backtest-not-finished", "Backtest not finished", detail)
    return None


def _limit_problem(error: BacktestLimitReached) -> ProblemError:
    """``backtest-limit``: the account keeps ``limit`` backtests at most."""
    have = f"You have {error.count:,} backtests, and an account keeps at most {error.limit:,}."
    if error.adding == 1:
        detail = f"{have} Delete old backtests you no longer need, then start this one."
    else:
        over = error.count + error.adding - error.limit
        detail = (
            f"The sweep has {error.adding} backtests. {have} Delete at least {over:,} old "
            "backtests you no longer need, or vary fewer values."
        )
    extensions = {"limit": error.limit, "count": error.count}
    if error.adding != 1:
        extensions["adding"] = error.adding
    return ProblemError(
        409, "backtest-limit", "Backtest limit reached", detail, extensions=extensions
    )


@contextmanager
def backtest_problems() -> Iterator[None]:
    """Raises the errors of starting or cancelling a backtest, of its document, of a comparison
    and of the market data they load as problem details, for the routes and the MCP tools."""
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
            "The model has no manifest (`model-needs-setup`), the user has "
            f"{QUEUE_MAX} backtests waiting already (`queue-full`, with `limit`), or as many "
            "backtests as an account keeps (`backtest-limit`, with `limit` and `count`).",
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
@heavy
async def start_backtest(
    body: BacktestIn, user: BacktestsUserDep, runs: BacktestRunsDep, response: Response
) -> Response:
    """Starts a backtest of one of the user's models on one instrument and period and returns
    its document with its number: `running`, or `queued` with its `queue_position` while
    another backtest of the user runs (one runs at a time per user, up to 20 more wait, and
    the oldest starts when the running one ends). Checked first: the model is the user's and
    has a manifest, the instrument fits the model, the period with the candles the model needs
    before it fits the data and the candle limit, the user's queue has room, and the worker
    that runs the models can be reached. The run goes on after the response; the document
    (`GET /api/v1/backtests/{backtest_id}`) has the results once it finished."""
    with backtest_problems():
        backtest, content = await runs.start(user.id, body)
    return _json(content, response, 201, {"Location": f"/api/v1/backtests/{backtest.id}"})


@router.post(
    "/api/v1/backtests/check",
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
            "The model has no manifest (`model-needs-setup`), the user has "
            f"{QUEUE_MAX} backtests waiting already (`queue-full`, with `limit`), or as many "
            "backtests as an account keeps (`backtest-limit`, with `limit` and `count`).",
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
async def check_backtest(
    body: BacktestIn, user: ReaderDep, runs: BacktestRunsDep, response: Response
) -> BacktestCheck:
    """Checks a new backtest as `POST /api/v1/backtests` does, with the same body and the same
    problems, and starts nothing: what would run (the model, the instrument as the catalog has
    it, the model's timeframe and session, the warm-up) and whether it would wait in the
    user's queue. An API key needs only the scope `read`."""
    response.headers.update(NO_STORE)
    with backtest_problems():
        return await runs.check(user.id, body)


@router.post(
    "/api/v1/backtests/{backtest_id}/cancel",
    response_model=BacktestDocument,
    responses={
        **_NOT_SIGNED_IN,
        **problem_doc(
            403, "The request does not come from the app's own pages (`cross-origin-request`)."
        ),
        **problem_doc(404, "The user has no backtest with this ID (`not-found`)."),
        **problem_doc(
            409,
            "The backtest is running or has ended, so it is not queued (`backtest-not-queued`).",
        ),
    },
    dependencies=[Depends(require_same_origin)],
)
async def cancel_backtest(
    backtest_id: BacktestId, user: BacktestsUserDep, runs: BacktestRunsDep, response: Response
) -> Response:
    """Cancels the user's queued backtest, so it never runs, and returns its document: `failed`,
    with an `error` that says it was cancelled. A running backtest cannot be cancelled."""
    with backtest_problems():
        content = await runs.cancel(user.id, backtest_id)
    return _json(content, response)


@router.delete(
    "/api/v1/backtests/{backtest_id}",
    status_code=204,
    responses={
        **_NOT_SIGNED_IN,
        **problem_doc(
            403, "The request does not come from the app's own pages (`cross-origin-request`)."
        ),
        **problem_doc(404, "The user has no backtest with this ID (`not-found`)."),
    },
    dependencies=[Depends(require_same_origin)],
)
async def delete_backtest(
    backtest_id: BacktestId, user: BacktestsUserDep, deletes: DeletesDep
) -> None:
    """Deletes the user's backtest for good, with everything its run stored. A queued one
    leaves the queue, a running one is stopped first. A published one leaves Experiments, and
    its page answers 404 to everyone. Its sweep no longer lists it, and a sweep without any
    backtest left is deleted too."""
    deleted = await deletes.delete(user.id, ids=[backtest_id])
    if not deleted.count:
        with backtest_problems():
            raise BacktestNotFound


@router.post(
    "/api/v1/backtests/delete",
    responses={
        **_NOT_SIGNED_IN,
        **problem_doc(
            403, "The request does not come from the app's own pages (`cross-origin-request`)."
        ),
        **problem_doc(404, "The user has no sweep with `sweep_id` (`not-found`)."),
        **problem_doc(
            422,
            f"Neither or both of `ids` and `sweep_id`, or more than {DELETE_MAX} IDs "
            "(`validation`).",
        ),
    },
    dependencies=[Depends(require_same_origin)],
)
async def delete_backtests(
    body: BacktestsDeleteIn, user: BacktestsUserDep, deletes: DeletesDep, response: Response
) -> BacktestsDeleted:
    """Deletes the user's backtests for good, as `DELETE /api/v1/backtests/{backtest_id}` does
    one: up to 100 by their `ids`, or every backtest of a sweep with `sweep_id`, which deletes
    the sweep too. IDs the user has no backtest with are listed in `not_found` and change
    nothing, so sending the same request again is safe."""
    response.headers.update(NO_STORE)
    with sweep_problems():
        return await deletes.delete(user.id, ids=body.ids or (), sweep_id=body.sweep_id)


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
@heavy
async def get_backtest(
    backtest_id: BacktestId, user: CurrentUserDep, runs: BacktestRunsDep, response: Response
) -> Response:
    """The result document of the user's backtest, whatever its status (`results` is null until
    it finished), or of another account's while it is published (`backtest.author`, read only,
    `model.id` null), in the light view: a series of more than 10,000 rows has one point per
    UTC day, and only the newest 10,000 trades are in it (`trades.complete` false)."""
    with backtest_problems():
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
            "The backtest is queued, running or failed (`backtest-not-finished`), or is test data "
            "without a result document (`no-document`).",
        ),
        **_SOURCE_ERRORS,
    },
)
@heavy
async def download_backtest(
    backtest_id: BacktestId, user: CurrentUserDep, runs: BacktestRunsDep, response: Response
) -> Response:
    """The result document of a finished backtest, the user's or a published one of another
    account, with every row of its series and every trade, as a file to save."""
    with backtest_problems():
        number, content = await runs.document(user.id, backtest_id, light=False)
    disposition = f'attachment; filename="candlestack-backtest-{number}.json"'
    return _json(content, response, headers={"Content-Disposition": disposition})


@router.get(
    "/api/v1/compare",
    responses={
        **_NOT_SIGNED_IN,
        **problem_doc(
            404,
            "Neither the user's backtest nor a published one has one of the IDs (`not-found`; "
            "`id` names it).",
        ),
        **problem_doc(
            409,
            "One of them is test data without a result document (`no-document`; `id` names it).",
        ),
        **problem_doc(
            422,
            f"More than {COMPARE_MAX} IDs, an empty one or one twice (`validation`).",
        ),
    },
)
@heavy
async def compare_backtests(
    ids: Annotated[
        str,
        Query(
            max_length=IDS_MAX_LENGTH,
            description=(
                f"{COMPARE_MIN} to {COMPARE_MAX} backtest IDs separated by commas: the user's own "
                "and published ones of other accounts"
            ),
            examples=["bt_7f3k2m9qx4,bt_t9n8v7d6a5"],
        ),
    ],
    user: CurrentUserDep,
    runs: BacktestRunsDep,
    response: Response,
) -> CompareOut:
    """Backtests side by side, as the Compare page shows them: each statistic of each with the
    best and the worst marked, the setups and which of their fields differ, and the equity and
    the drawdown of each and the return of holding its instrument on one time axis of at most
    200 times. A backtest that is running or failed is listed with its status and without
    values; one alone is listed with nothing marked, as the page shows a single backtest. Access
    is that of `GET /api/v1/backtests/{backtest_id}`. It reads only what each
    backtest stored when it ran, so it loads no candles from a data source."""
    response.headers.update(NO_STORE)
    with backtest_problems():
        return await compare(runs, user.id, parse_ids(ids))


# --- Sweeps -----------------------------------------------------------------------------------

SweepId = Annotated[str, Path(description="Public ID of the sweep", examples=["sw_7f3k2m9qx4"])]


def _sweep_problem(error: Exception) -> ProblemError | None:
    match error:
        case SweepTooLarge():
            return ProblemError(
                422,
                "validation",
                "Invalid request",
                f"The lists make {error.count} backtests, and a sweep runs at most {SWEEP_MAX}. "
                "Vary fewer values.",
                extensions={"count": error.count, "limit": SWEEP_MAX},
            )
        case SweepQueueFull():
            return ProblemError(
                409,
                "queue-full",
                "Queue full",
                f"The sweep has {error.count} backtests and your queue has room for "
                f"{error.room} (up to {QUEUE_MAX} wait). Start it when more of them have "
                "started, cancel some, or vary fewer values.",
                extensions={"limit": QUEUE_MAX, "count": error.count, "room": error.room},
            )
        case SweepNotFound():
            return ProblemError(404, "not-found", "Not Found", "You have no sweep with this ID.")
    return None


def _setup_problem(failed: SetupFailed) -> ProblemError:
    """The problem of a setup's error, named with the setup (`setup`, from 1)."""
    try:
        with backtest_problems():
            raise failed.error
    except ProblemError as problem:
        problem.detail = f"Setup {failed.index + 1} of the sweep: {problem.detail}"
        problem.extensions["setup"] = failed.index + 1
        return problem


@contextmanager
def sweep_problems() -> Iterator[None]:
    """Raises the errors of a sweep, of its setups and of the market data as problem details."""
    with backtest_problems():
        try:
            yield
        except SetupFailed as failed:
            raise _setup_problem(failed) from failed.error
        except Exception as error:
            problem = _sweep_problem(error)
            if problem is None:
                raise
            raise problem from error


@router.post(
    "/api/v1/sweeps",
    status_code=201,
    response_model=SweepOut,
    responses={
        200: {"model": SweepPlan, "description": "`validate_only`: what the sweep would start"},
        **_NOT_SIGNED_IN,
        **problem_doc(
            403, "The request does not come from the app's own pages (`cross-origin-request`)."
        ),
        **problem_doc(
            404,
            "The user has no model with an ID (`not-found`), or an instrument is not in the "
            "catalog (`instrument-not-found`); `setup` names the setup.",
        ),
        **problem_doc(
            409,
            "A model has no manifest (`model-needs-setup`, with `setup`), the user's queue has "
            "room for fewer backtests than the sweep has (`queue-full`, with `limit`, `count` "
            "and `room`), or the account cannot keep that many more backtests (`backtest-limit`, "
            "with `limit`, `count` and `adding`).",
        ),
        **problem_doc(
            422,
            f"An invalid body or fewer than {SWEEP_MIN} or more than {SWEEP_MAX} backtests "
            "(`validation`, the latter with `count` and `limit`), or a setup that fails a check "
            "of `POST /api/v1/backtests` (`instrument-not-for-model`, `period-out-of-range`, "
            "`warm-up-out-of-range`, `too-many-candles`, `validation`; `setup` names it, from 1).",
        ),
        **_SOURCE_ERRORS,
    },
    dependencies=[Depends(require_same_origin)],
)
@heavy
async def start_sweep(
    body: SweepIn, user: BacktestsUserDep, sweeps: SweepsDep, response: Response
) -> SweepOut | Response:
    """Starts a backtest for every combination of the values in `vary` over the `base` setup
    (the body of `POST /api/v1/backtests`): models, instruments, periods and decision rules,
    2 to 20 backtests, in that order. Every setup is checked as `POST /api/v1/backtests`
    checks one, and the user's queue must have room for all of them (up to 20 wait); then they
    all go to the end of the queue, or none does. With `validate_only` nothing starts: the
    answer is 200 with the count and each setup. The sweep's backtests are ordinary backtests
    with its `sweep_id`. `GET /api/v1/sweeps/{sweep_id}` follows them and ranks them once all
    ended."""
    response.headers.update(NO_STORE)
    with sweep_problems():
        if body.validate_only:
            plan = await sweeps.plan(user.id, body)
            return _json(plan.model_dump_json().encode(), response)
        sweep = await sweeps.start(user.id, body)
    response.headers["Location"] = f"/api/v1/sweeps/{sweep.id}"
    return sweep


@router.get(
    "/api/v1/sweeps/{sweep_id}",
    responses={**_NOT_SIGNED_IN, **problem_doc(404, "The user has no sweep with this ID.")},
)
async def get_sweep(
    sweep_id: SweepId,
    user: CurrentUserDep,
    sweeps: SweepsDep,
    response: Response,
    metric: Annotated[
        SweepMetric | None,
        Query(description="What the ranking orders by, instead of the sweep's own `metric`"),
    ] = None,
) -> SweepOut:
    """The user's sweep: how many of its backtests are queued, running, finished and failed,
    each backtest's ID, status and setup, and once none is queued or running (`done`) the
    ranking by the metric, with the marking rules of `GET /api/v1/compare`."""
    response.headers.update(NO_STORE)
    with sweep_problems():
        return await sweeps.sweep(user.id, sweep_id, metric)
