"""fix_orderstatus_enum_lowercase

Revision ID: e1f2a3b4c5d6
Revises: 08f2ab35492b
Create Date: 2026-04-20 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, Sequence[str], None] = '08f2ab35492b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Rename orderstatus enum values from UPPERCASE to lowercase."""
    op.execute("ALTER TYPE orderstatus RENAME VALUE 'PENDING' TO 'pending'")
    op.execute("ALTER TYPE orderstatus RENAME VALUE 'CONFIRMED' TO 'confirmed'")
    op.execute("ALTER TYPE orderstatus RENAME VALUE 'PROCESSING' TO 'processing'")
    op.execute("ALTER TYPE orderstatus RENAME VALUE 'SHIPPED' TO 'shipped'")
    op.execute("ALTER TYPE orderstatus RENAME VALUE 'DELIVERED' TO 'delivered'")
    op.execute("ALTER TYPE orderstatus RENAME VALUE 'CANCELLED' TO 'cancelled'")


def downgrade() -> None:
    """Revert orderstatus enum values back to UPPERCASE."""
    op.execute("ALTER TYPE orderstatus RENAME VALUE 'pending' TO 'PENDING'")
    op.execute("ALTER TYPE orderstatus RENAME VALUE 'confirmed' TO 'CONFIRMED'")
    op.execute("ALTER TYPE orderstatus RENAME VALUE 'processing' TO 'PROCESSING'")
    op.execute("ALTER TYPE orderstatus RENAME VALUE 'shipped' TO 'SHIPPED'")
    op.execute("ALTER TYPE orderstatus RENAME VALUE 'delivered' TO 'DELIVERED'")
    op.execute("ALTER TYPE orderstatus RENAME VALUE 'cancelled' TO 'CANCELLED'")
