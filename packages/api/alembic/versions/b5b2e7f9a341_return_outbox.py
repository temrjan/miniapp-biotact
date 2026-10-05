"""Add retained confirmations with the existing paid source-position sequence."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "b5b2e7f9a341"
down_revision = "b5a1d6e8f230"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "employee_referral_return_outbox",
        sa.Column(
            "id",
            sa.BigInteger(),
            primary_key=True,
            autoincrement=False,
            server_default=sa.text("nextval('employee_referral_paid_outbox_id_seq'::regclass)"),
        ),
        sa.Column("return_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column(
            "order_id",
            sa.BigInteger(),
            sa.ForeignKey("employee_referral_paid_outbox.order_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("order_version", sa.BigInteger(), nullable=False),
        sa.Column("confirmed_by", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("request_lines", JSONB(), nullable=False),
        sa.Column("body", sa.LargeBinary(), nullable=False),
        sa.Column("body_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("order_id", "order_version", name="uq_referral_return_version"),
        sa.CheckConstraint(
            "id > 0 AND order_id > 0 AND order_version >= 2 AND confirmed_by > 0",
            name="ck_referral_return_positive",
        ),
        sa.CheckConstraint("source = 'shop-admin-confirmation'", name="ck_referral_return_source"),
        sa.CheckConstraint(
            "octet_length(body) BETWEEN 1 AND 1048576 AND body_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_referral_return_body",
        ),
        sa.CheckConstraint(
            "substring(return_id::text,15,1) = '4' AND substring(return_id::text,20,1) IN ('8','9','a','b')",
            name="ck_referral_return_uuid",
        ),
    )
    op.execute("""CREATE FUNCTION employee_referral_return_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN
RAISE EXCEPTION 'immutable referral return history' USING ERRCODE = '23514';
END; $$""")
    op.execute("""CREATE TRIGGER referral_return_immutable BEFORE UPDATE OR DELETE
ON employee_referral_return_outbox FOR EACH ROW
EXECUTE FUNCTION employee_referral_return_immutable()""")


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
IF EXISTS (SELECT 1 FROM employee_referral_return_outbox) THEN
RAISE EXCEPTION 'Referral returned history prevents downgrade';
END IF; END $$""")
    op.drop_table("employee_referral_return_outbox")
    op.execute("DROP FUNCTION employee_referral_return_immutable()")
