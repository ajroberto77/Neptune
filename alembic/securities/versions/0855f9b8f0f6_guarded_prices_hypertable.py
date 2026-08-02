"""guarded prices hypertable

Revision ID: 0855f9b8f0f6
Revises: 1f83c47309ef
Create Date: 2026-08-02 14:30:48.503974

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from neptune.db.timescale import timescaledb_available

# revision identifiers, used by Alembic.
revision: str = '0855f9b8f0f6'
down_revision: Union[str, Sequence[str], None] = '1f83c47309ef'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Convert `prices` to a TimescaleDB hypertable, guarded to a real Postgres with the
    extension installed. A no-op everywhere else (SQLite tests; plain Postgres without
    `CREATE EXTENSION timescaledb`) — `create_all` already yields a working relational
    `prices` table in both cases (see securities/models.py's module docstring).

    TimescaleDB requires the partitioning column (`ts`) in every unique/PK constraint, so
    `prices`' solo `id` primary key is widened to `(id, ts)` first. Nothing else FKs to
    `prices.id`, so this is safe; the existing `uq_price_sec_ts_source` constraint already
    includes `ts` and needs no change."""
    bind = op.get_bind()
    if not timescaledb_available(bind):
        return
    op.drop_constraint("prices_pkey", "prices", type_="primary")
    op.create_primary_key("prices_pkey", "prices", ["id", "ts"])
    op.execute(
        "SELECT create_hypertable('prices', 'ts', if_not_exists => TRUE, migrate_data => TRUE)"
    )


def downgrade() -> None:
    """Same guard. A hypertable behaves as an ordinary Postgres table for every query
    Neptune issues (no query changes needed either way), so there is no "un-hypertable"
    step beyond restoring the original `(id)`-only primary key."""
    bind = op.get_bind()
    if not timescaledb_available(bind):
        return
    op.drop_constraint("prices_pkey", "prices", type_="primary")
    op.create_primary_key("prices_pkey", "prices", ["id"])
