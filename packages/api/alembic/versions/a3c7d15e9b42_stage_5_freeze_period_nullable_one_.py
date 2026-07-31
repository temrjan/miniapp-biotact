"""stage 5 (freeze = stamp): period becomes nullable, at most one committed draw

Revision ID: a3c7d15e9b42
Revises: d4e91b2a7c65
Create Date: 2026-07-31

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3c7d15e9b42"
down_revision: str | Sequence[str] | None = "d4e91b2a7c65"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Two changes, both required by the freeze this step introduces.

    * ``lottery_draws.period`` — ``DROP NOT NULL``. The new freeze receives no period at all: the
      calendar binding was removed with the model, and a draw happens when the button is pressed.
      Something still had to go into a NOT NULL column, and ``UNIQUE(period)`` made the obvious
      candidate — the current month — refuse the SECOND draw of that month with an
      ``IntegrityError``, which is the cancelled model welded onto the new one. Writing a synthetic
      value instead would put a public column in the position of stating something untrue. So the
      column stops being required and starts holding NULL; ``UNIQUE`` stays and keeps allowing it,
      because PostgreSQL treats NULLs as distinct (verified against this very schema before the
      migration was written). The column itself, its validator and the dead ``open`` state go in the
      contract phase (step 6), after every read has moved to ``draw_number``.
      This is a catalog-only change: no table rewrite, no scan.

    * ``uq_lottery_draws_one_committed`` — a PARTIAL UNIQUE index making a second live draw
      impossible. The freeze does check for one before inserting, but a check is not a serializer:
      two administrators pressing the button microseconds apart both pass it, and the loser stamps a
      ticket issued in between, leaving TWO committed draws each with a published hash. Uniqueness
      on ``status`` among committed rows removes the state instead of guarding against it. The
      losing INSERT then fails with an ``IntegrityError``, which the freeze translates into the same
      409 diagnosis its pre-check gives ("close the current draw first").

    ⚠️ Applying this to a database that already holds two committed draws would fail. Production
    holds zero draws of any status; the release checklist re-runs that count immediately before
    merge, and a non-zero answer stops this migration rather than this migration discovering it.
    """
    op.alter_column(
        "lottery_draws", "period", existing_type=sa.String(length=7), nullable=True
    )
    op.create_index(
        "uq_lottery_draws_one_committed",
        "lottery_draws",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'committed'"),
    )


def downgrade() -> None:
    """Reverse order.

    ⚠️ Honest boundary: ``SET NOT NULL`` succeeds only while no draw of the new model exists —
    every draw the new freeze creates has ``period IS NULL``. After the first freeze this downgrade
    needs the values filled in by hand first (any unique 7-char label per draw), and that is a
    deliberate decision, not an oversight: the alternative is inventing calendar labels for draws
    that never had a calendar. Immediately after this PR the table is empty and the downgrade is
    unconditional.
    """
    op.drop_index("uq_lottery_draws_one_committed", table_name="lottery_draws")
    op.alter_column(
        "lottery_draws", "period", existing_type=sa.String(length=7), nullable=False
    )
