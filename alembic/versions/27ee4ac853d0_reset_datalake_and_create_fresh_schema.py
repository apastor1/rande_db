"""reset datalake and create fresh schema

Revision ID: 27ee4ac853d0
Revises: 1b1e638608c7
Create Date: 2025-11-23 08:17:57.162085

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '27ee4ac853d0'
down_revision: Union[str, Sequence[str], None] = '1b1e638608c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
