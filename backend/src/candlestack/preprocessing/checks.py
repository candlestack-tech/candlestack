"""The checks of a manifest that read the files only (docs/preprocessing.md, checks 2 and 3).

``parse_manifest`` checks the form (the JSON Schema), ``check_manifest`` what the form cannot
say: that the catalog has every block, their parameters, the names, the signature and the
example; ``check_with_model`` compares the manifest with what the model file says. Each check
that fails gives one ``ManifestProblem`` in plain sentences, for the upload page to show next
to it; a check that passes gives none.
"""

from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ValidationError
from pydantic_core import ErrorDetails

from candlestack.preprocessing.catalog import CATALOG, Step
from candlestack.preprocessing.manifest import RESERVED_NAMES, Manifest

Check = Literal["schema", "catalog", "params", "names", "signature", "example", "model"]

# The output CandleStack takes: one score per sample.
SCORE_SHAPE = [1]


@dataclass(frozen=True, slots=True)
class ManifestProblem:
    """A failed check: which one (``check``) and what is wrong, in sentences (``message``)."""

    check: Check
    message: str


class InvalidManifest(ValueError):
    """The document is not a manifest; ``problem`` says why (the ``schema`` check)."""

    def __init__(self, problem: ManifestProblem) -> None:
        super().__init__(problem.message)
        self.problem = problem


@dataclass(frozen=True, slots=True)
class _Step:
    where: str
    id: str
    step: Step
    params: dict[str, Any]


def parse_manifest(document: str | bytes | Mapping[str, Any]) -> Manifest:
    """The manifest in ``document``: JSON text, or the JSON object already read.

    Raises:
        InvalidManifest: It is not valid JSON or not of the schema ``candlestack.manifest/1``.
    """
    try:
        if isinstance(document, str | bytes):
            return Manifest.model_validate_json(document)
        return Manifest.model_validate(document)
    except ValidationError as error:
        sentences = [_sentence(detail) for detail in error.errors(include_url=False)]
        raise InvalidManifest(ManifestProblem("schema", " ".join(sentences))) from None


def check_manifest(manifest: Manifest) -> list[ManifestProblem]:
    """The problems of a manifest that has the right form: none when it can run."""
    checks: list[tuple[Check, list[str]]] = [
        ("catalog", _catalog(manifest)),
        ("params", _params(manifest)),
        ("names", _names(manifest)),
        ("signature", _signature(manifest)),
        ("example", _example(manifest)),
    ]
    return [ManifestProblem(check, " ".join(found)) for check, found in checks if found]


def check_with_model(
    manifest: Manifest, input_shape: Sequence[int] | None, output_shape: Sequence[int] | None
) -> list[ManifestProblem]:
    """The manifest against the model file: its input and output shapes without the batch axis,
    as the upload's check reads them (None where the file does not say)."""
    signature = manifest.signature
    compared = (
        _compared(
            "input",
            signature.input.shape,
            input_shape,
            "The manifest gives {ours}, the model takes {theirs}.",
        ),
        _compared(
            "output",
            signature.output.shape,
            output_shape,
            "The manifest says the model gives {ours}, the model gives {theirs}.",
        ),
    )
    found = [sentence for sentence in compared if sentence is not None]
    return [ManifestProblem("model", " ".join(found))] if found else []


def _compared(
    name: str, ours: list[int], theirs: Sequence[int] | None, mismatch: str
) -> str | None:
    if theirs is None:
        return f"The model file does not say the shape of its {name}, so it cannot be compared."
    if list(theirs) != ours:
        return mismatch.format(ours=ours, theirs=list(theirs))
    return None


def unknown_blocks(manifest: Manifest) -> list[str]:
    """The blocks the manifest names that the catalog does not have, once each, in order."""
    named = (step.id for step in _steps(manifest))
    return list(dict.fromkeys(block for block in named if block not in CATALOG))


def _steps(manifest: Manifest) -> Iterator[_Step]:
    """Each step of the input: where it is, its block, the step it must be, its parameters."""
    spec = manifest.input
    rep = spec.representation
    yield _Step("input.representation", rep.id, "representation", rep.params)
    for index, feature in enumerate(spec.features):
        where = f"input.features[{index}]"
        yield _Step(where, feature.id, "feature", feature.params)
        yield _Step(f"{where}.norm", feature.norm.id, "normalization", feature.norm.params)


def _catalog(manifest: Manifest) -> list[str]:
    found = []
    if unknown := unknown_blocks(manifest):
        kind = "block" if len(unknown) == 1 else "blocks"
        found.append(f"The catalog has no {kind} {_listed(unknown)} yet, so it cannot run.")
    for step in _steps(manifest):
        block = CATALOG.get(step.id)
        if block is not None and block.step != step.step:
            found.append(
                f"{step.where} is {step.id}, a {block.step} block, where a {step.step} block goes."
            )
    if manifest.input.window.last == "current":
        found.append(
            'The window option last "current" is not supported yet. It needs rows that span '
            "several candles, such as Renko bricks."
        )
    return found


def _params(manifest: Manifest) -> list[str]:
    """The parameters of each known block in its right step, against its model."""
    found = []
    for step in _steps(manifest):
        block = CATALOG.get(step.id)
        if block is None or block.step != step.step:
            continue
        try:
            block.params.model_validate(step.params)
        except ValidationError as error:
            found += [
                _sentence(detail, where=f"{step.where}.params", block=step.id)
                for detail in error.errors(include_url=False)
            ]
    return found


def _names(manifest: Manifest) -> list[str]:
    counts = Counter(feature.name for feature in manifest.input.features)
    found = [
        f"The feature name {name} is used {count} times. Each feature needs a name of its own."
        for name, count in counts.items()
        if count > 1
    ]
    found += [
        f"The feature name {name} is taken by the time of each row. Rename the feature."
        for name in counts
        if name in RESERVED_NAMES
    ]
    return found


def _signature(manifest: Manifest) -> list[str]:
    found = []
    length, features = manifest.input.window.length, len(manifest.input.features)
    expected = [length, features]
    if manifest.signature.input.shape != expected:
        found.append(
            f"signature.input is {manifest.signature.input.shape}, but a window of {length} "
            f"{_plural(length, 'row')} of {features} {_plural(features, 'feature')} gives "
            f"{expected}."
        )
    if manifest.signature.output.shape != SCORE_SHAPE:
        found.append(
            f"signature.output is {manifest.signature.output.shape}, but CandleStack takes "
            f"models that give one score per sample, {SCORE_SHAPE}."
        )
    return found


def _example(manifest: Manifest) -> list[str]:
    example = manifest.example
    if example is None:
        return []
    found = []
    samples = len(example.input)
    for name, values, shape in (
        ("input", example.input, manifest.signature.input.shape),
        ("output", example.output, manifest.signature.output.shape),
    ):
        expected = [samples, *shape]
        got = _shape(values)
        if got != expected:
            what = f"the shape {got}" if got is not None else "rows of different lengths"
            found.append(
                f"example.{name} has {what}, but {samples} {_plural(samples, 'sample')} of "
                f"signature.{name} make {expected}."
            )
    return found


def _shape(values: list[Any]) -> list[int] | None:
    """The shape of nested lists of numbers; None when their lengths differ."""
    if not values or not isinstance(values[0], list):
        return [len(values)]
    inner = [_shape(value) for value in values]
    if any(shape != inner[0] for shape in inner) or inner[0] is None:
        return None
    return [len(values), *inner[0]]


def _sentence(detail: ErrorDetails, *, where: str = "", block: str | None = None) -> str:
    """One validation error in plain words."""
    path, kind = _path(where, detail["loc"]), detail["type"]
    if kind == "json_invalid":
        return f"The manifest is not valid JSON: {detail.get('ctx', {}).get('error', '')}."
    if not path:
        return "The manifest must be a JSON object."
    if kind == "missing":
        return f"{path} is missing." if block is None else f"{path} is missing ({block} needs it)."
    if kind == "extra_forbidden":
        return (
            f"{path} is not a field of the manifest."
            if block is None
            else f"{path} is not a parameter of {block}."
        )
    return f"{path}: {detail['msg']}." if block is None else f"{path}: {detail['msg']} ({block})."


def _path(where: str, location: tuple[int | str, ...]) -> str:
    """`input.features[2].name` from Pydantic's location of an error, after ``where``."""
    path = where
    for part in location:
        path += f"[{part}]" if isinstance(part, int) else f".{part}" if path else part
    return path


def _listed(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def _plural(count: int, word: str) -> str:
    return word if count == 1 else f"{word}s"
