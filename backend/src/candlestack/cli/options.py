"""The argparse types the options of ``run`` and ``run-all`` share."""

import argparse
import math
from collections.abc import Callable


def _non_negative(text: str) -> float:
    """A finite number of 0 or more (an argparse type)."""
    value = _number(text)
    if value < 0:
        raise argparse.ArgumentTypeError(f"must be 0 or more, got {text}")
    return value


def _fraction(text: str) -> float:
    """A number between 0 and 1, both excluded (an argparse type)."""
    value = _number(text)
    if not 0 < value < 1:
        raise argparse.ArgumentTypeError(f"must be between 0 and 1 (0.05 is 5%), got {text}")
    return value


def _share(text: str) -> float:
    """A number above 0, at most 1 (an argparse type)."""
    value = _number(text)
    if not 0 < value <= 1:
        raise argparse.ArgumentTypeError(f"must be above 0 and at most 1 (0.5 is 50%), got {text}")
    return value


def _number(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {text!r}") from None
    if not math.isfinite(value):
        raise argparse.ArgumentTypeError(f"not a finite number: {text!r}")
    return value


def _at_least(minimum: int) -> Callable[[str], int]:
    """An argparse type: a whole number of ``minimum`` or more."""

    def parse(text: str) -> int:
        try:
            value = int(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"not a whole number: {text!r}") from None
        if value < minimum:
            raise argparse.ArgumentTypeError(f"must be {minimum} or more, got {text}")
        return value

    return parse


def _list[T](item: Callable[[str], T]) -> Callable[[str], tuple[T, ...]]:
    """An argparse type: comma-separated values, each of the type ``item``, none repeated."""

    def parse(text: str) -> tuple[T, ...]:
        values = tuple(item(part.strip()) for part in text.split(","))
        if len(set(values)) < len(values):
            raise argparse.ArgumentTypeError(f"a value repeats: {text!r}")
        return values

    return parse
