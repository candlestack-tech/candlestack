"""Search the backtests by trigrams: the search of the list matched every backtest of the user
against the query, so it took longer the more backtests an account had. A trigram index (the
extension pg_trgm, part of Postgres) of the text the search can find in a backtest's own row
(``experiments.models.SEARCH_TEXT``) lets Postgres look up the rows that may match instead.

The text is a SQL function, so the index and the queries name it alike. Postgres inlines it
(one immutable expression) in both, and every cast in it is explicit: ``'#' || number`` alone
would be stable, which an index cannot use.

Revision ID: 4b7e2c9a1f63
Revises: d2e6b4a8f913
Create Date: 2026-11-09 11:01:09.091705
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4b7e2c9a1f63"
down_revision: str | None = "d2e6b4a8f913"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "ix_backtests_search"
_FUNCTION = """
CREATE FUNCTION backtest_search_text(
    number integer, name varchar, generated_name varchar, model_name varchar, instruments jsonb
) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE
RETURN '#' || number::text || ' ' || coalesce(name, '') || ' ' || generated_name || ' '
    || model_name || ' ' || jsonb_path_query_array(instruments, '$[*].symbol')::text
"""
_SEARCH_TEXT = sa.literal_column(
    "backtest_search_text(number, name, generated_name, model_name, instruments)"
).label("search")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(_FUNCTION)
    op.create_index(
        _INDEX,
        "backtests",
        [_SEARCH_TEXT],
        postgresql_using="gin",
        postgresql_ops={"search": "gin_trgm_ops"},
        postgresql_with={"fastupdate": "off"},
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="backtests")
    op.execute("DROP FUNCTION backtest_search_text")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
