"""reconcile schema baseline (banners/settings/products/categories)

Приводит цепочку миграций в соответствие с моделями/прод. Пред-существующий дрейф:
целые таблицы ``banners``/``settings`` и колонки ``products``/``categories`` создавались
на проде ВНЕ цепочки миграций. Все операции ИДЕМПОТЕНТНЫ: на проде (где объекты есть)
— no-op; на чистой БД (parity / dev) — догоняют модели.

downgrade — НАМЕРЕННО no-op: baseline-reconcile не реверсит то, что предшествует
цепочке на проде, иначе симметричный DROP уничтожил бы живые данные.

Revision ID: f1e2d3c4b5a6
Revises: e1f2a3b4c5d6
Create Date: 2026-06-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f1e2d3c4b5a6"
down_revision: Union[str, Sequence[str], None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    tables = set(insp.get_table_names())

    # --- banners (есть на проде, нет в цепочке) ---
    if "banners" not in tables:
        op.create_table(
            "banners",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("title", sa.String(length=200), nullable=False),
            sa.Column("title_uz", sa.String(length=200), nullable=True),
            sa.Column("subtitle", sa.String(length=500), nullable=True),
            sa.Column("subtitle_uz", sa.String(length=500), nullable=True),
            sa.Column("image_url", sa.String(length=500), nullable=True),
            sa.Column("gradient", sa.String(length=200), nullable=True),
            sa.Column("link_url", sa.String(length=500), nullable=True),
            sa.Column("sort_order", sa.Integer(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
        )

    # --- settings (есть на проде, нет в цепочке) ---
    if "settings" not in tables:
        op.create_table(
            "settings",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("key", sa.String(length=100), nullable=False),
            sa.Column("value", sa.Text(), nullable=False),
            sa.Column("description", sa.String(length=500), nullable=True),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("key"),
        )

    # --- products: новые колонки ---
    prod_cols = {c["name"] for c in insp.get_columns("products")}
    if "advantages" not in prod_cols:
        op.add_column(
            "products",
            sa.Column(
                "advantages",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
                comment="Array of product benefits/advantages",
            ),
        )
    if "video_url" not in prod_cols:
        op.add_column("products", sa.Column("video_url", sa.String(length=500), nullable=True))

    # images TEXT->JSONB ТОЛЬКО если ещё не jsonb. На проде уже jsonb => настоящий no-op
    # (без table rewrite). На чистой parity-БД (text) — конвертит. (Reviewer MINOR.)
    op.execute(
        """
        DO $$
        BEGIN
          IF (SELECT data_type FROM information_schema.columns
              WHERE table_name = 'products' AND column_name = 'images') <> 'jsonb' THEN
            ALTER TABLE products ALTER COLUMN images TYPE jsonb USING images::jsonb;
          END IF;
        END $$;
        """
    )
    op.execute("COMMENT ON COLUMN products.images IS 'Array of image URLs for product gallery'")

    # --- categories.is_purchasable (A': NOT NULL DEFAULT true) ---
    cat_cols = {c["name"] for c in insp.get_columns("categories")}
    if "is_purchasable" not in cat_cols:
        op.add_column(
            "categories",
            sa.Column("is_purchasable", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        )

    # --- GIN-индексы products (после того как images/advantages стали jsonb) ---
    prod_idx = {ix["name"] for ix in insp.get_indexes("products")}
    if "idx_products_images_gin" not in prod_idx:
        op.create_index("idx_products_images_gin", "products", ["images"], postgresql_using="gin")
    if "idx_products_advantages_gin" not in prod_idx:
        op.create_index(
            "idx_products_advantages_gin", "products", ["advantages"], postgresql_using="gin"
        )


def downgrade() -> None:
    # baseline-reconcile: объекты предшествуют цепочке на проде; downgrade НАМЕРЕННО
    # no-op, чтобы НЕ уничтожить живые данные (banners/settings/products/categories).
    pass
