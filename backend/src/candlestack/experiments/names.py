"""The name a backtest gets when it is created: an adjective and a word from space, as
"Silent Orion". 128 adjectives by 128 nouns make 16,384 names an account can have; once it has
all of them, a number follows, as "Silent Orion 2".

The words are short and easy to read and say. No adjective judges a result (no Lucky, Winning
or Bullish: a name says nothing of how the backtest did) and none could offend; the nouns are
stars, constellations, planets, moons, comets and words such as Nebula or Orbit. The migration
that named the backtests stored before has its own copy of the lists, as they were then.
"""

import secrets
from collections.abc import Callable, Sequence, Set

ADJECTIVES = (
    "Amber", "Ancient", "Azure", "Blazing", "Bold", "Brave", "Breezy", "Bright",
    "Bronze", "Calm", "Clear", "Cloudy", "Cobalt", "Copper", "Coral", "Cosmic",
    "Crimson", "Crystal", "Curious", "Dancing", "Distant", "Dreamy", "Drifting", "Early",
    "Eastern", "Electric", "Emerald", "Endless", "Faint", "Fiery", "Fleet", "Floating",
    "Frosty", "Frozen", "Gentle", "Gleaming", "Glowing", "Golden", "Hazy", "Hidden",
    "Humble", "Humming", "Hushed", "Icy", "Indigo", "Ivory", "Jade", "Keen",
    "Kind", "Lilac", "Lively", "Lone", "Lucid", "Luminous", "Lunar", "Magnetic",
    "Marble", "Mellow", "Midnight", "Misty", "Mystic", "Nimble", "Noble", "Northern",
    "Olive", "Opal", "Orbiting", "Pale", "Patient", "Pearl", "Placid", "Polar",
    "Quick", "Quiet", "Radiant", "Rapid", "Rare", "Restless", "Roaming", "Rolling",
    "Rosy", "Royal", "Rustic", "Sandy", "Sapphire", "Scarlet", "Serene", "Shimmering",
    "Shining", "Shy", "Silent", "Silky", "Silver", "Sleek", "Sleepless", "Snowy",
    "Soft", "Solar", "Solemn", "Southern", "Spinning", "Spiral", "Starry", "Still",
    "Stormy", "Subtle", "Sunny", "Swift", "Teal", "Tender", "Tidal", "Tiny",
    "Tranquil", "Twilight", "Twin", "Vast", "Velvet", "Violet", "Vivid", "Wandering",
    "Warm", "Wavy", "Western", "Whispering", "Wintry", "Wise", "Woven", "Young",
)  # fmt: skip

NOUNS = (
    "Achernar", "Acrux", "Albireo", "Alcor", "Alcyone", "Aldebaran", "Algol", "Alnilam",
    "Altair", "Andromeda", "Antares", "Apogee", "Aquila", "Arcturus", "Ariel", "Aries",
    "Asteroid", "Atlas", "Auriga", "Aurora", "Bellatrix", "Betelgeuse", "Callisto", "Canopus",
    "Capella", "Carina", "Cassiopeia", "Castor", "Centaur", "Cepheus", "Ceres", "Charon",
    "Cluster", "Comet", "Corona", "Corvus", "Cosmos", "Cygnus", "Deneb", "Dione",
    "Draco", "Dubhe", "Eclipse", "Electra", "Elnath", "Enceladus", "Equinox", "Eris",
    "Europa", "Fomalhaut", "Galaxy", "Ganymede", "Gemini", "Hadar", "Halley", "Halo",
    "Hamal", "Helios", "Hercules", "Horizon", "Hydra", "Hyperion", "Io", "Jupiter",
    "Kepler", "Kuiper", "Leo", "Libra", "Luna", "Lynx", "Lyra", "Maia",
    "Mars", "Mercury", "Merope", "Meteor", "Mimas", "Mintaka", "Mira", "Miranda",
    "Mizar", "Nebula", "Neptune", "Nova", "Oberon", "Orbit", "Orion", "Parsec",
    "Pegasus", "Perigee", "Perseus", "Phoenix", "Photon", "Pleiades", "Pluto", "Polaris",
    "Pollux", "Procyon", "Proxima", "Pulsar", "Quasar", "Regulus", "Rhea", "Rigel",
    "Saturn", "Scorpius", "Shaula", "Sirius", "Solstice", "Spica", "Stardust", "Starlight",
    "Sun", "Supernova", "Taurus", "Tethys", "Thuban", "Titan", "Titania", "Triton",
    "Umbriel", "Vega", "Vela", "Venus", "Vesta", "Virgo", "Vortex", "Zenith",
)  # fmt: skip

NAMES = tuple(f"{adjective} {noun}" for adjective in ADJECTIVES for noun in NOUNS)
_LOWERED = tuple(name.lower() for name in NAMES)
_RANDOM = secrets.SystemRandom()


def candidates(count: int) -> list[str]:
    """``count`` different names of ``NAMES`` at random: what a new backtest tries first, as an
    account with even 2,000 names has the ten of them all with a chance of about 1 in 10^9."""
    return _RANDOM.sample(NAMES, count)


def generated_name(taken: Set[str], choose: Callable[[Sequence[str]], str] = secrets.choice) -> str:
    """A name of ``NAMES`` that none of `taken` (the account's names, lowercased) is, picked by
    `choose` (at random); once every one is taken, the first number from 2 that frees one, as
    "Silent Orion 2"."""
    number = 1
    while True:
        suffix = "" if number == 1 else f" {number}"
        free = [
            name + suffix
            for name, lowered in zip(NAMES, _LOWERED, strict=True)
            if lowered + suffix not in taken
        ]
        if free:
            return choose(free)
        number += 1
