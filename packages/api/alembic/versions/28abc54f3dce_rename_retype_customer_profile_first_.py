"""rename+retype customer_profile.first_activated_sku to first_activated_product_id (bigint)

Revision ID: 28abc54f3dce
Revises: e881ee3fc9a5
Create Date: 2026-06-30 14:17:11.052648

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '28abc54f3dce'
down_revision: Union[str, Sequence[str], None] = 'e881ee3fc9a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    F4 (Stage 1 legacy fix): ``customer_profile.first_activated_sku`` (String(32) holding a
    stringified product_id) becomes ``first_activated_product_id`` (BigInteger). Retype BEFORE
    rename so the ``USING`` cast references the still-existing column name. The column is empty
    on prod (loyalty is dark → ``activate_code`` never ran), so the cast is a no-op in practice.
    """
    op.alter_column(
        "customer_profile",
        "first_activated_sku",
        type_=sa.BigInteger(),
        existing_type=sa.String(length=32),
        existing_nullable=True,
        postgresql_using="first_activated_sku::bigint",
    )
    op.alter_column(
        "customer_profile",
        "first_activated_sku",
        new_column_name="first_activated_product_id",
        existing_type=sa.BigInteger(),
        existing_nullable=True,
    )


def downgrade() -> None:
    """Downgrade schema (mirror: rename back, then retype to VARCHAR(32))."""
    op.alter_column(
        "customer_profile",
        "first_activated_product_id",
        new_column_name="first_activated_sku",
        existing_type=sa.BigInteger(),
        existing_nullable=True,
    )
    op.alter_column(
        "customer_profile",
        "first_activated_sku",
        type_=sa.String(length=32),
        existing_type=sa.BigInteger(),
        existing_nullable=True,
        postgresql_using="first_activated_sku::varchar(32)",
    )
