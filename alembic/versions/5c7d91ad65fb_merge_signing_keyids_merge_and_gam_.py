"""merge signing keyids merge and gam refresh token heads

Revision ID: 5c7d91ad65fb
Revises: 2f2601309dd9, 9fdf18a56063
Create Date: 2026-11-19 05:33:00.036501

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "5c7d91ad65fb"
down_revision: str | Sequence[str] | None = ("2f2601309dd9", "9fdf18a56063")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
