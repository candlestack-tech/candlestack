"""No look-ahead (docs/preprocessing.md#no-look-ahead): replacing every candle after a row t with
other random candles changes no row and no sample up to t, for every block of the catalog and
for whole manifests, as the engine's ``test_data_after_a_candle_changes_nothing_up_to_it``
does for the loop. A block registered in the catalog is in these tests at once; the blocks
made up at the end, which do look ahead, show that the tests can fail."""

import itertools
import random
from collections.abc import Callable
from typing import Any

import polars as pl
import pytest
from pydantic import BaseModel

from candlestack.preprocessing import (
    CATALOG,
    Block,
    Manifest,
    build_input,
    checks,
    parse_manifest,
    pipeline,
)
from candlestack.preprocessing.blocks import ROW_ID, NoParams

# Enough candles for the longest warm-up of the catalog's defaults (350, the EMA of 50) and a
# window of 25 after it, of Renko triples too (test_the_candles_outlast_every_warm_up).
ROWS = 500
SEEDS = range(20)
STEP = 1800
# The parameters the blocks run with here. A block whose parameters have no defaults needs an
# entry, or its test fails.
PARAMS: dict[str, list[dict[str, Any]]] = {
    "window@1": [
        {"length": 1},
        {"length": 5},
        {"length": 25},
        {"length": 1, "last": "current"},
        {"length": 5, "last": "current"},
        {"length": 25, "last": "current"},
    ],
}
# Leading rows still in their warm-up, per feature of the random rows: the same in every frame,
# as a block's warm-up is.
WARM_UP = (3, 0, 1)

Make = Callable[[random.Random], Any]


def random_candles(rng: random.Random) -> pl.DataFrame:
    """A random walk of 30-minute candles that gaps between them, with a random volume, some
    marked the last of their session as stock candles are."""
    rows = []
    price = 100.0
    for row in range(ROWS):
        open_ = price * (1 + rng.gauss(0, 0.01))
        close = open_ * (1 + rng.gauss(0, 0.02))
        high = max(open_, close) * (1 + abs(rng.gauss(0, 0.01)))
        low = min(open_, close) * (1 - abs(rng.gauss(0, 0.01)))
        last = rng.random() < 1 / 13
        rows.append((row * STEP, open_, high, low, close, rng.uniform(0, 1e6), last))
        price = close
    schema = {
        "ts": pl.Int64,
        **dict.fromkeys(("open", "high", "low", "close", "volume"), pl.Float64),
        "session_last": pl.Boolean,
    }
    return pl.DataFrame(rows, schema=schema, orient="row")


def random_column(rng: random.Random) -> pl.Series:
    """A column of the rows, still in its warm-up for the first rows."""
    return pl.Series(
        "feature", [None] * WARM_UP[0] + [rng.gauss(0, 1) for _ in range(ROWS - WARM_UP[0])]
    )


def random_rows(rng: random.Random) -> pl.DataFrame:
    """Rows of the window: ``ts`` and features of different warm-ups."""
    return pl.DataFrame(
        {
            "ts": [row * STEP for row in range(ROWS)],
            **{
                f"f{index}": [None] * lead + [rng.gauss(0, 1) for _ in range(ROWS - lead)]
                for index, lead in enumerate(WARM_UP)
            },
        },
        schema_overrides={f"f{index}": pl.Float64 for index in range(len(WARM_UP))},
    )


def random_forming_rows(rng: random.Random) -> pl.DataFrame:
    """Rows of the window that span several candles: a ``row_id`` that goes on or moves to
    the next row at random."""
    ids = list(itertools.accumulate(int(rng.random() < 0.5) for _ in range(ROWS)))
    return random_rows(rng).insert_column(1, pl.Series(ROW_ID, ids, dtype=pl.Int64))


def rows_of(representation: str) -> Make:
    """The rows of a representation of random candles, which its features of the rows take."""
    block = CATALOG[representation]
    return lambda rng: block.run(random_candles(rng), block.params())


def up_to_rows(frame: pl.DataFrame, row: int) -> pl.DataFrame:
    return frame.filter(pl.col("ts") <= row * STEP)


def up_to_values(values: pl.Series, row: int) -> pl.Series:
    return values.head(row + 1)


def up_to_samples(made: Any, row: int) -> tuple[list[Any], list[int], int | None]:
    """The samples known by a row, and the rows dropped before them once there are any: with
    rows that span several candles, where the first sample falls before it is known depends
    on the later candles."""
    known = int((made.ts <= row * STEP).sum())
    dropped = made.dropped if known else None
    return made.samples[:known].tolist(), made.ts.head(known).to_list(), dropped


def same(a: Any, b: Any) -> bool:
    return a.equals(b) if isinstance(a, pl.DataFrame | pl.Series) else a == b


# What each step takes, made at random, and the part of its output known by a row.
STEPS: dict[str, tuple[Make, Callable[[Any, int], Any]]] = {
    "representation": (random_candles, up_to_rows),
    "feature": (random_candles, up_to_values),
    "normalization": (random_column, up_to_values),
    "window": (random_rows, up_to_samples),
}


def params_of(block: Block) -> list[BaseModel]:
    return [block.params.model_validate(params) for params in PARAMS.get(block.id, [{}])]


def looks_ahead(block: Block, params: BaseModel, rng: random.Random) -> bool:
    """Whether replacing what comes after a row changes the block's output up to it."""
    make, up_to = STEPS[block.step]
    if block.of_rows is not None:
        make = rows_of(block.of_rows)
    if getattr(params, "last", None) == "current":
        make = random_forming_rows
    data = make(rng)
    full = block.run(data, params)
    for row in rng.sample(range(len(data) - 1), 5):
        changed = pl.concat([data.head(row + 1), make(rng).slice(row + 1)])
        if not same(up_to(block.run(changed, params), row), up_to(full, row)):
            return True
    return False


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("block", list(CATALOG.values()), ids=list(CATALOG))
def test_data_after_a_row_changes_nothing_of_a_block_up_to_it(block: Block, seed: int) -> None:
    for params in params_of(block):
        assert not looks_ahead(block, params, random.Random(seed)), (block.id, params)


def manifest(
    representation: dict[str, Any], features: list[dict[str, Any]], window: dict[str, Any]
) -> Manifest:
    return parse_manifest(
        {
            "version": 1,
            "input": {
                "timeframe": "30m",
                "session": "regular",
                "representation": representation,
                "features": features,
                "window": window,
            },
            "signature": {
                "input": {"shape": [window["length"], len(features)], "dtype": "float32"},
                "output": {"shape": [1], "dtype": "float32", "meaning": "score"},
            },
        }
    )


def every_manifest() -> list[Manifest]:
    """Manifests of every block of the catalog: each representation, with every feature it
    can have under every normalization, in every window that fits its rows."""

    def steps(step: str) -> list[dict[str, Any]]:
        return [
            {"id": block.id, "params": params}
            for block in CATALOG.values()
            if block.step == step
            for params in PARAMS.get(block.id, [{}])
        ]

    def features(representation: str) -> list[dict[str, Any]]:
        fitting = [
            feature
            for feature in steps("feature")
            if CATALOG[feature["id"]].of_rows in (None, representation)
        ]
        return [
            {**feature, "name": f"{feature['id']} {norm['id']} {index}", "norm": norm}
            for index, (feature, norm) in enumerate(
                (feature, norm) for feature in fitting for norm in steps("normalization")
            )
        ]

    def windows(representation: str) -> list[dict[str, Any]]:
        last = "closed" if CATALOG[representation].candles_per_row is None else "current"
        return [window for window in PARAMS["window@1"] if window.get("last", "closed") == last]

    return [
        manifest(representation, features(representation["id"]), window)
        for representation in steps("representation")
        for window in windows(representation["id"])
    ]


MANIFESTS = every_manifest()


def test_every_manifest_makes_samples() -> None:
    """A manifest whose candles end before its first sample would pass without a check."""
    for manifest in MANIFESTS:
        assert len(build_input(random_candles(random.Random(0)), manifest).samples) > 0


def changes_up_to_a_row(manifest: Manifest, rng: random.Random) -> bool:
    """Whether replacing the candles after a row changes the rows or samples up to it."""
    candles = random_candles(rng)
    full = build_input(candles, manifest)
    for row in rng.sample(range(ROWS - 1), 5):
        changed = pl.concat([candles.head(row + 1), random_candles(rng).slice(row + 1)])
        result = build_input(changed, manifest)
        if not up_to_rows(result.rows, row).equals(up_to_rows(full.rows, row)):
            return True
        if up_to_samples(result, row) != up_to_samples(full, row):
            return True
    return False


def test_the_candles_outlast_every_warm_up() -> None:
    """A block still in its warm-up at the last candle gives nulls only, which look ahead at
    nothing, so the tests above would pass without checking it."""
    longest = max(
        block.warm_up(params) for block in CATALOG.values() for params in params_of(block)
    )

    assert longest + max(params["length"] for params in PARAMS["window@1"]) < ROWS


def test_the_manifests_cover_every_block() -> None:
    named = {
        block
        for manifest in MANIFESTS
        for block in (
            manifest.input.representation.id,
            *(feature.id for feature in manifest.input.features),
            *(feature.norm.id for feature in manifest.input.features),
        )
    }

    assert named | {"window@1"} == set(CATALOG)


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize(
    "manifest",
    MANIFESTS,
    ids=[f"{m.input.representation.id} {m.input.window.length}" for m in MANIFESTS],
)
def test_candles_after_a_row_change_nothing_of_the_input_up_to_it(
    manifest: Manifest, seed: int
) -> None:
    assert not changes_up_to_a_row(manifest, random.Random(seed))


def test_the_raw_candles_manifest_does_not_look_ahead(raw: dict[str, Any]) -> None:
    manifest = parse_manifest(raw)

    for seed in SEEDS:
        assert not changes_up_to_a_row(manifest, random.Random(seed))


# Blocks that do look ahead, made up to show that the tests above catch them.


def next_close(frame: pl.DataFrame, _params: NoParams) -> pl.Series:
    """The next candle's close; the last candle keeps its own, so no row has a hole."""
    return frame["close"].shift(-1).fill_null(frame["close"])


def next_candle(frame: pl.DataFrame, _params: NoParams) -> pl.DataFrame:
    """Each row with the values of the next candle under its own ts."""
    return frame.with_columns(pl.exclude("ts", "session_last").shift(-1).fill_null(0.0))


def centered(column: pl.Series, _params: NoParams) -> pl.Series:
    """Scaled by the mean of the whole series, later rows included."""
    return column - column.mean()


def window_of_the_next_row(rows: pl.DataFrame, params: BaseModel) -> Any:
    return CATALOG["window@1"].run(next_candle(rows, NoParams()), params)


LOOKING_AHEAD = [
    Block(id="next-close@1", step="feature", description="", run=next_close),
    Block(id="centered@1", step="normalization", description="", run=centered),
    Block(id="next-candle@1", step="representation", description="", run=next_candle),
]


@pytest.mark.parametrize("block", LOOKING_AHEAD, ids=[block.id for block in LOOKING_AHEAD])
def test_a_block_that_looks_ahead_is_caught(block: Block) -> None:
    assert looks_ahead(block, NoParams(), random.Random(0))


def current_window_of_the_next_row_id(rows: pl.DataFrame, params: BaseModel) -> Any:
    """A window that takes a row as finished when the row after the next one starts another:
    it reads one candle too far."""
    ahead = rows.with_columns(pl.col(ROW_ID).shift(-1).fill_null(pl.col(ROW_ID).max() + 1))
    return CATALOG["window@1"].run(ahead, params)


def test_a_current_window_that_reads_its_own_row_s_end_is_caught() -> None:
    block = Block(
        id="window@8", step="window", description="", run=current_window_of_the_next_row_id
    )
    params = CATALOG["window@1"].params.model_validate({"length": 5, "last": "current"})

    assert looks_ahead(block, params, random.Random(0))


def test_a_window_that_looks_ahead_is_caught() -> None:
    block = Block(id="window@9", step="window", description="", run=window_of_the_next_row)

    assert looks_ahead(
        block, CATALOG["window@1"].params.model_validate({"length": 5}), random.Random(0)
    )


def test_a_manifest_with_a_block_that_looks_ahead_is_caught(
    raw: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = {**CATALOG, "next-close@1": LOOKING_AHEAD[0]}
    monkeypatch.setattr(pipeline, "CATALOG", catalog)
    monkeypatch.setattr(checks, "CATALOG", catalog)
    raw["input"]["features"][3]["id"] = "next-close@1"

    assert changes_up_to_a_row(parse_manifest(raw), random.Random(0))
