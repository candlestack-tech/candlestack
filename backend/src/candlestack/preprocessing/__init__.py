"""Preprocessing: the model's input from candles and the model's manifest.

The manifest (``candlestack.manifest/1``) names a block of the catalog for every step of the
input: the representation (which rows), the features (the columns, in order), their
normalization and the window (rows to samples). ``build_input`` runs them on candles;
``parse_manifest``, ``check_manifest`` and ``check_with_model`` are the checks of a manifest on
upload (docs/preprocessing.md).

It imports no other module and no server library, like the backtest modules: the worker runs
it without network. Other modules import only from here, never from the submodules.
"""

from candlestack.preprocessing.catalog import (
    CATALOG,
    Block,
    BlockOut,
    CatalogOut,
    ParamOut,
    Step,
    describe_catalog,
)
from candlestack.preprocessing.checks import (
    Check,
    InvalidManifest,
    ManifestProblem,
    check_manifest,
    check_with_model,
    parse_manifest,
    unknown_blocks,
)
from candlestack.preprocessing.manifest import SCHEMA, Manifest
from candlestack.preprocessing.pipeline import ModelInput, build_input, warm_up

__all__ = [
    "CATALOG",
    "SCHEMA",
    "Block",
    "BlockOut",
    "CatalogOut",
    "Check",
    "InvalidManifest",
    "Manifest",
    "ManifestProblem",
    "ModelInput",
    "ParamOut",
    "Step",
    "build_input",
    "check_manifest",
    "check_with_model",
    "describe_catalog",
    "parse_manifest",
    "unknown_blocks",
    "warm_up",
]
