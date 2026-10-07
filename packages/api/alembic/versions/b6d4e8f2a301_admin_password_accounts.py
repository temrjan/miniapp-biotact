"""Add named administrator credentials without importing the compat password."""

import sqlalchemy as sa

from alembic import op

revision = "b6d4e8f2a301"
down_revision = "b6a3c8d1e240"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_password_accounts",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("login", sa.String(64), nullable=False),
        sa.Column("password_hash", sa.String(60), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("login"),
        sa.CheckConstraint(
            "login ~ '^[a-z][a-z0-9_.-]{2,63}$' AND login <> 'biotact'",
            name="ck_admin_password_login",
        ),
        sa.CheckConstraint(
            r"password_hash ~ '^\$2b\$12\$[./A-Za-z0-9]{53}$'",
            name="ck_admin_password_hash",
        ),
    )


def downgrade() -> None:
    op.execute("LOCK TABLE admin_password_accounts IN ACCESS EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN
IF EXISTS (SELECT 1 FROM admin_password_accounts) THEN
RAISE EXCEPTION 'admin_password_accounts is not empty; downgrade refused';
END IF; END $$""")
    op.drop_table("admin_password_accounts")
