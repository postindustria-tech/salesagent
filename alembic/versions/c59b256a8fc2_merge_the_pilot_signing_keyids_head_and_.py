"""merge the pilot signing keyids head and the creative assignment head

Revision ID: c59b256a8fc2
Revises: 5c7d91ad65fb, 502abb701275
Create Date: 2026-11-24 12:48:04.807650

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c59b256a8fc2'
down_revision: Union[str, Sequence[str], None] = ('5c7d91ad65fb', '502abb701275')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
