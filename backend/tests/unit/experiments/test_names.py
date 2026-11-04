"""The names a backtest gets when it is created: the word lists and the pick of a free name.
Which names an account has is an integration test (tests/integration/experiments)."""

from collections.abc import Sequence

import pytest

from candlestack.experiments.names import ADJECTIVES, NAMES, NOUNS, generated_name
from candlestack.experiments.schemas import BACKTEST_NAME_MAX

# The words of the approved design, which the lists keep.
DESIGN_ADJECTIVES = (
    "Silent", "Bright", "Crimson", "Distant", "Frozen", "Golden", "Hidden", "Radiant", "Pale",
    "Swift", "Silver", "Lone", "Quiet", "Amber", "Velvet", "Calm", "Misty", "Icy", "Gentle",
    "Wandering",
)  # fmt: skip
DESIGN_NOUNS = (
    "Orion", "Vega", "Nova", "Lyra", "Pulsar", "Rigel", "Quasar", "Altair", "Andromeda", "Comet",
    "Nebula", "Polaris", "Sirius", "Meteor", "Deneb", "Antares", "Capella", "Betelgeuse",
    "Aurora", "Eclipse",
)  # fmt: skip


def first(free: Sequence[str]) -> str:
    return free[0]


@pytest.mark.parametrize("words", [ADJECTIVES, NOUNS], ids=["adjectives", "nouns"])
def test_128_short_title_case_words_each(words: tuple[str, ...]) -> None:
    assert len(words) == len(set(words)) == 128
    for word in words:
        assert word.isascii(), word
        assert word.isalpha(), word
        assert word == word.title(), word
        assert len(word) <= 10, word


def test_the_lists_keep_the_designs_words_and_share_none() -> None:
    assert set(DESIGN_ADJECTIVES) <= set(ADJECTIVES)
    assert set(DESIGN_NOUNS) <= set(NOUNS)
    assert not set(ADJECTIVES) & set(NOUNS)


def test_no_adjective_judges_a_result() -> None:
    judging = {"Lucky", "Unlucky", "Winning", "Losing", "Bullish", "Bearish", "Rich", "Poor"}

    assert not judging & set(ADJECTIVES)


def test_every_pair_is_a_name_of_its_own() -> None:
    assert len(NAMES) == len({name.lower() for name in NAMES}) == 128 * 128
    assert NAMES[0] == f"{ADJECTIVES[0]} {NOUNS[0]}"
    assert max(len(name) for name in NAMES) + len(" 99999") <= BACKTEST_NAME_MAX


def test_a_name_is_an_adjective_and_a_noun() -> None:
    adjective, noun = generated_name(set()).split(" ")

    assert adjective in ADJECTIVES
    assert noun in NOUNS


def test_the_pick_is_among_the_free_names() -> None:
    picked: list[Sequence[str]] = []

    def choose(free: Sequence[str]) -> str:
        picked.append(free)
        return free[-1]

    taken = {"silent orion", "amber achernar"}
    name = generated_name(taken, choose)

    assert len(picked[0]) == len(NAMES) - 2
    assert "Silent Orion" not in picked[0]
    assert name == NAMES[-1]


def test_a_name_taken_in_any_case_is_not_picked() -> None:
    # The account's names come lowercased; only "Silent Orion" is free.
    taken = {name.lower() for name in NAMES} - {"silent orion"}

    assert generated_name(taken, first) == "Silent Orion"


def test_once_every_pair_is_taken_a_number_follows() -> None:
    every = {name.lower() for name in NAMES}

    assert generated_name(every, first) == f"{NAMES[0]} 2"
    assert generated_name(every - {"silent orion"}, first) == "Silent Orion"
    with_two = every | {f"{name} 2" for name in every}
    assert generated_name(with_two - {"silent orion 2"}, first) == "Silent Orion 2"
    assert generated_name(with_two, first) == f"{NAMES[0]} 3"
