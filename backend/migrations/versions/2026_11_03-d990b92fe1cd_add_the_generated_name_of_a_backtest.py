"""Add the generated name of a backtest.

Every backtest stored before gets one, account by account, the oldest first (by number): the
first name in the account's own order that none of its backtests has, as its own name or one
given before it, in any case. The order is the pairs of the word lists sorted by a SHA-256 of
the account's ID and the name, so the names look drawn at random but every run of the migration
gives the same rows the same names.

The word lists are a copy of ``candlestack.experiments.names`` as it was when this was written,
so the migration names the rows the same way whatever the lists become (a migration imports
nothing of the app that may change).

Revision ID: d990b92fe1cd
Revises: e4b7a2c9d013
Create Date: 2026-11-03 12:00:00.000000
"""

import hashlib
import itertools
from collections.abc import Iterator, Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d990b92fe1cd"
down_revision: str | None = "e4b7a2c9d013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "uq_backtests_user_id_generated_name"

_ADJECTIVES = (
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

_NOUNS = (
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

_NAMES = tuple(f"{adjective} {noun}" for adjective in _ADJECTIVES for noun in _NOUNS)


def _names_of(user_id: str) -> Iterator[str]:
    """Every name in the account's own order: the pairs, then each with a number from 2 on
    (``Silent Orion 2``), as the app's generator goes on once every pair is taken."""
    order = sorted(_NAMES, key=lambda name: hashlib.sha256(f"{user_id} {name}".encode()).digest())
    for number in itertools.count(1):
        suffix = "" if number == 1 else f" {number}"
        for name in order:
            yield name + suffix


def upgrade() -> None:
    op.add_column("backtests", sa.Column("generated_name", sa.String(length=80), nullable=True))
    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT id, user_id, lower(name) FROM backtests ORDER BY user_id, number")
    ).all()
    named = []
    for user_id, group in itertools.groupby(rows, key=lambda row: row[1]):
        backtests = list(group)
        taken = {row[2] for row in backtests if row[2] is not None}
        free = (name for name in _names_of(user_id) if name.lower() not in taken)
        named += [{"id": row[0], "name": next(free)} for row in backtests]
    if named:
        connection.execute(
            sa.text("UPDATE backtests SET generated_name = :name WHERE id = :id"), named
        )
    op.alter_column("backtests", "generated_name", nullable=False)
    op.create_index(
        _INDEX,
        "backtests",
        ["user_id", sa.literal_column("lower(generated_name)")],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="backtests")
    op.drop_column("backtests", "generated_name")
