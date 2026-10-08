"""Allow historical minute and newly issued five-minute confirmations."""

from alembic import op

revision = "c4f8a921d730"
down_revision = "b6d4e8f2a301"
branch_labels = None
depends_on = None

TABLE = "employee_referral_campaigns"
CONSTRAINT = "ck_employee_referral_campaigns_lease"


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT, TABLE, type_="check")
    op.create_check_constraint(
        CONSTRAINT,
        TABLE,
        "valid_until - issued_at IN (interval '60 seconds', interval '300 seconds')",
    )


def downgrade() -> None:
    op.execute("LOCK TABLE employee_referral_campaigns IN ACCESS EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN
IF EXISTS (SELECT 1 FROM employee_referral_campaigns
           WHERE valid_until <> issued_at + interval '60 seconds') THEN
RAISE EXCEPTION 'Five-minute confirmations remain; downgrade refused';
END IF; END $$""")
    op.drop_constraint(CONSTRAINT, TABLE, type_="check")
    op.create_check_constraint(
        CONSTRAINT,
        TABLE,
        "valid_until = issued_at + interval '60 seconds'",
    )
