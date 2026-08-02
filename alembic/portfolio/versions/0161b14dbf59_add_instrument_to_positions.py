"""add instrument to positions

Revision ID: 0161b14dbf59
Revises: 0d7b71c1aece
Create Date: 2026-08-02 07:38:29.085064

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0161b14dbf59'
down_revision: Union[str, Sequence[str], None] = '0d7b71c1aece'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # native_enum=False on the ORM column (PositionORM.instrument) -- stored as a plain
    # VARCHAR, not a Postgres native enum/TYPE, matching TransactionORM's convention rather
    # than this table's own native-enum side/short_type columns. See db/models.py.
    op.add_column(
        'positions',
        sa.Column('instrument', sa.String(), nullable=False, server_default='CASH'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('positions', 'instrument')
