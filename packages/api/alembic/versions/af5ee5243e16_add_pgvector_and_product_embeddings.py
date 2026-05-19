"""Add pgvector extension and product_embeddings table

Revision ID: af5ee5243e16
Revises: 08f2ab35492b
Create Date: 2026-05-08 13:45:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'af5ee5243e16'
down_revision: Union[str, Sequence[str], None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create pgvector extension and product_embeddings table."""
    # Enable pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Create product_embeddings table
    op.execute("""
        CREATE TABLE product_embeddings (
            id SERIAL PRIMARY KEY,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            content TEXT NOT NULL,
            embedding vector(1536),
            metadata JSONB DEFAULT '{}',
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)

    # Create indexes
    op.execute("CREATE INDEX idx_product_embeddings_product_id ON product_embeddings(product_id)")
    op.execute("CREATE INDEX idx_product_embeddings_vector ON product_embeddings USING ivfflat (embedding vector_cosine_ops)")


def downgrade() -> None:
    """Drop product_embeddings table and pgvector extension."""
    op.execute("DROP TABLE IF EXISTS product_embeddings")
    op.execute("DROP EXTENSION IF EXISTS vector")
