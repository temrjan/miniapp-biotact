"""Add immutable paid count outbox after retained order capture."""

import sqlalchemy as sa

from alembic import op

revision = "b5a1d6e8f230"
down_revision = "9b47c6e3d821"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "employee_referral_paid_outbox",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("order_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("event_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("body", sa.LargeBinary(), nullable=False),
        sa.Column("body_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id > 0 AND order_id > 0", name="ck_referral_paid_positive"),
        sa.CheckConstraint(
            "octet_length(body) BETWEEN 1 AND 1048576 AND body_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_referral_paid_body",
        ),
        sa.CheckConstraint(
            "substring(event_id::text,15,1) = '4' AND substring(event_id::text,20,1) IN ('8','9','a','b')",
            name="ck_referral_paid_uuid",
        ),
    )
    op.execute("""CREATE FUNCTION employee_referral_paid_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN
RAISE EXCEPTION 'immutable referral paid history' USING ERRCODE = '23514';
END; $$""")
    op.execute("""CREATE TRIGGER referral_paid_immutable BEFORE UPDATE OR DELETE
ON employee_referral_paid_outbox FOR EACH ROW
EXECUTE FUNCTION employee_referral_paid_immutable()""")


def downgrade() -> None:
    op.drop_table("employee_referral_paid_outbox")
    op.execute("DROP FUNCTION employee_referral_paid_immutable()")
