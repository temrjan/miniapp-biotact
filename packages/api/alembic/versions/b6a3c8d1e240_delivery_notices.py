"""Add retained K2 and notice jobs after confirmed return history."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "b6a3c8d1e240"
down_revision = "b5b2e7f9a341"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for lane, terminal in (("k2", "acknowledged"), ("notice", "sent")):
        table = f"employee_referral_{lane}_delivery"
        columns = [
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("source_kind", sa.String(6), nullable=False),
            sa.Column(
                "paid_id",
                sa.BigInteger(),
                sa.ForeignKey("employee_referral_paid_outbox.id", ondelete="RESTRICT"),
                unique=True,
            ),
            sa.Column(
                "return_id",
                sa.BigInteger(),
                sa.ForeignKey("employee_referral_return_outbox.id", ondelete="RESTRICT"),
                unique=True,
            ),
            sa.Column("state", sa.String(16), nullable=False),
            sa.Column("attempt_count", sa.BigInteger(), nullable=False),
            sa.Column("generation", sa.BigInteger(), nullable=False),
            sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("leased_until", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True)),
            sa.Column("error_category", sa.String(32)),
        ]
        checks = [
            sa.CheckConstraint(
                "(source_kind = 'paid' AND paid_id IS NOT NULL AND return_id IS NULL) OR (source_kind = 'return' AND paid_id IS NULL AND return_id IS NOT NULL)",
                name=f"ck_referral_{lane}_source",
            ),
            sa.CheckConstraint(
                "attempt_count BETWEEN 0 AND 5 AND generation >= 0",
                name=f"ck_referral_{lane}_attempt",
            ),
            sa.CheckConstraint(
                f"state IN ('pending','leased','blocked','{terminal}'"
                + (",'legacy_unknown')" if lane == "notice" else ")"),
                name=f"ck_referral_{lane}_state",
            ),
            sa.CheckConstraint(
                "(state = 'leased') = (leased_until IS NOT NULL)", name=f"ck_referral_{lane}_lease"
            ),
            sa.CheckConstraint(
                f"(state = '{terminal}') = (completed_at IS NOT NULL) AND "
                + (
                    "(state = 'sent') = (message_id IS NOT NULL) AND (message_id IS NULL OR message_id > 0)"
                    if lane == "notice"
                    else "(state = 'acknowledged') = (ack IS NOT NULL)"
                ),
                name=f"ck_referral_{lane}_receipt",
            ),
        ]
        if lane == "notice":
            columns.extend(
                [
                    sa.Column("payload", sa.LargeBinary()),
                    sa.Column("payload_sha256", sa.String(64)),
                    sa.Column("message_id", sa.BigInteger()),
                ]
            )
            checks.append(
                sa.CheckConstraint(
                    "(state = 'legacy_unknown' AND payload IS NULL AND payload_sha256 IS NULL) OR (state <> 'legacy_unknown' AND payload IS NOT NULL AND payload_sha256 IS NOT NULL AND octet_length(payload) BETWEEN 1 AND 1048576 AND payload_sha256 ~ '^[0-9a-f]{64}$')",
                    name="ck_referral_notice_payload",
                )
            )
        else:
            columns.append(sa.Column("ack", JSONB()))
        created = op.create_table(table, *columns, *checks)
        op.create_index(f"ix_referral_{lane}_due", table, ["state", "next_attempt_at"])
        immutable = "id,source_kind,paid_id,return_id,created_at"
        if lane == "notice":
            immutable += ",payload,payload_sha256"
        fields = immutable.split(",")
        old = ",".join("OLD." + f for f in fields)
        new = ",".join("NEW." + f for f in fields)
        op.execute(f"""CREATE FUNCTION referral_{lane}_delivery_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN
IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'immutable referral delivery identity' USING ERRCODE='23514'; END IF;
IF ROW({old}) IS DISTINCT FROM ROW({new}) OR (OLD.state='legacy_unknown' AND NEW.state <> OLD.state) THEN
RAISE EXCEPTION 'immutable referral delivery identity' USING ERRCODE='23514'; END IF;
RETURN NEW; END $$""")
        op.execute(
            f"CREATE TRIGGER referral_{lane}_delivery_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION referral_{lane}_delivery_immutable()"
        )
        for kind, source in (("paid", "paid"), ("return", "return")):
            state = "pending" if lane == "k2" else "legacy_unknown"
            source_table = sa.table(
                f"employee_referral_{source}_outbox", sa.column("id", sa.BigInteger())
            )
            op.execute(
                created.insert().from_select(
                    [
                        "source_kind",
                        f"{kind}_id",
                        "state",
                        "attempt_count",
                        "generation",
                        "next_attempt_at",
                        "created_at",
                    ],
                    sa.select(
                        sa.literal(kind),
                        source_table.c.id,
                        sa.literal(state),
                        sa.literal(0),
                        sa.literal(0),
                        sa.func.clock_timestamp(),
                        sa.func.clock_timestamp(),
                    ),
                )
            )


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
IF EXISTS(SELECT 1 FROM employee_referral_k2_delivery) OR EXISTS(SELECT 1 FROM employee_referral_notice_delivery) THEN
RAISE EXCEPTION 'Referral delivery history prevents downgrade'; END IF; END $$""")
    for lane in ("notice", "k2"):
        op.drop_table(f"employee_referral_{lane}_delivery")
        op.execute(f"DROP FUNCTION referral_{lane}_delivery_immutable()")
