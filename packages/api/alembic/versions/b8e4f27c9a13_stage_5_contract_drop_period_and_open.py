"""stage 5 (contract): drop period, retire the open state

Revision ID: b8e4f27c9a13
Revises: a3c7d15e9b42
Create Date: 2026-07-31

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8e4f27c9a13"
down_revision: str | Sequence[str] | None = "a3c7d15e9b42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """The last of the calendar model leaves the schema.

    * ``lottery_draws.period`` — dropped, and its ``UNIQUE`` goes with it. Every read moved to
      ``draw_number`` in step 3, and every draw the button creates has held NULL here since. It
      survived this long on purpose: dropping it while 60 live read sites still named it is the
      shape of incident #30.

    * ``status`` — the ``'open'`` server default is removed. ``open`` was never written by any code
      path, and the only value a bare INSERT could reasonably take instead is ``committed`` — which
      would let a thoughtless row become a LIVE draw, the one operation guarded by a typed
      confirmation and a partial unique index. An INSERT that does not name the status is a mistake,
      and from here it fails like one.

    ``draw_number`` is NOT touched: the plan scheduled ``SET NOT NULL`` for this phase, but step 1
    already shipped the column as NOT NULL together with its identity (ratified at its own Gate-1).
    An operation that changes nothing has no place in a revision history — the plan simply predates
    the decision.
    """
    op.drop_column("lottery_draws", "period")
    op.alter_column(
        "lottery_draws", "status", existing_type=sa.String(length=16), server_default=None
    )


def downgrade() -> None:
    """Reverse order — with two honest limits.

    ``period`` comes back EMPTY: the values are gone, and they were NULL on every row this model
    ever created. Nothing reads the column after step 3, so an emergency rollback of the code works
    with it empty.

    ⚠️ Rolling back FURTHER than this revision needs the column filled in by hand first: the
    revision below (``a3c7d15e9b42``) puts ``NOT NULL`` back on ``period``, which fails while every
    row holds NULL. On an empty database — CI runs ``downgrade base`` — the chain passes.
    """
    op.alter_column(
        "lottery_draws",
        "status",
        existing_type=sa.String(length=16),
        server_default=sa.text("'open'::character varying"),
    )
    op.add_column("lottery_draws", sa.Column("period", sa.String(length=7), nullable=True))
    op.create_unique_constraint("lottery_draws_period_key", "lottery_draws", ["period"])
