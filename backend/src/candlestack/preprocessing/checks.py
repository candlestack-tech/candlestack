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

from candlestack.preprocessing.catalog import CATALOG, RENKO_TRIPLES, Step
from candlestack.preprocessing.manifest import RESERVED_NAMES, VERSION, Manifest

Check = Literal["schema", "catalog", "params", "names", "signature", "example", "model"]

# The output CandleStack takes: one score per sample.
SCORE_SHAPE = [1]

# Manifests written before the plain version number named it in this field.
OLD_VERSION_FIELD = "schema"


# More errors of the form than this are counted, not read: reading each into Python takes
# seconds and hundreds of MB for a document wrong throughout, such as one with a thousand
# unknown fields.
SCHEMA_ERRORS_MAX = 100


@dataclass(frozen=True, slots=True)
class ManifestProblem:
    """A failed check: which one (``check``) and what is wrong, one sentence (or two, when the
    second says what to do) for each thing (``sentences``)."""

    check: Check
    sentences: tuple[str, ...]

    @property
    def message(self) -> str:
        """The sentences as one text."""
        return " ".join(self.sentences)


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
        InvalidManifest: It is not valid JSON or not of the form of a manifest of version 1.
    """
    try:
        if isinstance(document, str | bytes):
            return Manifest.model_validate_json(document)
        return Manifest.model_validate(document)
    except ValidationError as error:
        if error.error_count() > SCHEMA_ERRORS_MAX:
            sentence = (
                f"The manifest has {error.error_count()} mistakes in its form, too many to list. "
                "Check that the file is a CandleStack manifest."
            )
            raise InvalidManifest(ManifestProblem("schema", (sentence,))) from None
        details = error.errors(include_url=False)
        sentences = []
        if any(_is_old_version_field(detail) for detail in details):
            # One sentence says how to update the manifest instead of two about its fields.
            sentences.append(
                f"The manifest names its version with the old field {OLD_VERSION_FIELD}. Replace "
                f'"{OLD_VERSION_FIELD}": "candlestack.manifest/1" with "version": {VERSION}.'
            )
            details = [
                detail
                for detail in details
                if not _is_old_version_field(detail)
                and (detail["loc"], detail["type"]) != (("version",), "missing")
            ]
        sentences += [_sentence(detail) for detail in details]
        raise InvalidManifest(ManifestProblem("schema", tuple(sentences))) from None


def _is_old_version_field(detail: ErrorDetails) -> bool:
    return (detail["loc"], detail["type"]) == ((OLD_VERSION_FIELD,), "extra_forbidden")


def check_manifest(manifest: Manifest) -> list[ManifestProblem]:
    """The problems of a manifest that has the right form: none when it can run."""
    checks: list[tuple[Check, list[str]]] = [
        ("catalog", _catalog(manifest)),
        ("params", _params(manifest)),
        ("names", _names(manifest)),
        ("signature", _signature(manifest)),
        ("example", _example(manifest)),
    ]
    return [ManifestProblem(check, tuple(found)) for check, found in checks if found]


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
    return [ManifestProblem("model", tuple(found))] if found else []


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
    session = manifest.input.session
    for step in _steps(manifest):
        block = CATALOG.get(step.id)
        if block is None:
            continue
        if block.step != step.step:
            found.append(
                f"{step.where} is {step.id}, a {block.step} block, where a {step.step} block goes."
            )
        if block.needs_session and session != "regular":
            found.append(
                f"{step.where} is {step.id}, which marks the last candle of each trading "
                f"session, but {session} candles have no sessions. Use it with the regular session."
            )
    found += _has_rules(manifest)
    rep = CATALOG.get(manifest.input.representation.id)
    if rep is not None and rep.step == "representation":
        found += _fits_representation(manifest, rep.id, forming=rep.candles_per_row is not None)
    return found


def _has_rules(manifest: Manifest) -> list[str]:
    """Each feature against its normalization, where that has a rule of its own per feature
    (``blesk-norm@1``)."""
    found = []
    for index, feature in enumerate(manifest.input.features):
        block, norm = CATALOG.get(feature.id), CATALOG.get(feature.norm.id)
        # A block that is unknown or in the wrong step is a problem of its own above.
        if block is None or block.step != "feature" or norm is None:
            continue
        rules = norm.normalizes if norm.step == "normalization" else None
        if rules is not None and feature.id not in rules:
            found.append(
                f"input.features[{index}].norm is {norm.id}, which has no rule for "
                f"{feature.id}, so it cannot scale that feature."
            )
    return found


def _fits_representation(manifest: Manifest, rep: str, *, forming: bool) -> list[str]:
    """The features of rows and the window rule against the rows the representation makes."""
    found = []
    for index, feature in enumerate(manifest.input.features):
        block = CATALOG.get(feature.id)
        if block is not None and block.of_rows is not None and block.of_rows != rep:
            found.append(
                f"input.features[{index}] is {feature.id}, a column of the rows of "
                f"{block.of_rows}, but the representation is {rep}."
            )
    last = manifest.input.window.last
    if last == "current" and not forming:
        found.append(
            f'The window option last "current" needs rows that span several candles, such as '
            f'{RENKO_TRIPLES}, but each row of {rep} is finished at its candle. Use "closed".'
        )
    if last == "closed" and forming:
        found.append(
            f'The rows of {rep} span several candles, so the window needs last "current": the '
            'last finished rows and the row still forming. "closed" is not supported with it.'
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
        f"The feature name {name} is taken by {RESERVED_NAMES[name]}. Rename the feature."
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
    if path == "version" and kind == "literal_error":
        return f"This manifest has version {detail['input']}. CandleStack reads version {VERSION}."
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
