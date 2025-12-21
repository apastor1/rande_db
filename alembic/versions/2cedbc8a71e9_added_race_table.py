"""added race table

Revision ID: 2cedbc8a71e9
Revises: f00dc88e1772
Create Date: 2025-12-21 11:30:17.139150

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2cedbc8a71e9'
down_revision: Union[str, Sequence[str], None] = 'f00dc88e1772'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
