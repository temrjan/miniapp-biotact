"""AI service layer for product embeddings and semantic search."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.embeddings import EmbeddingService


async def search_products(
    db: AsyncSession,
    query_text: str,
    limit: int = 5,
    score_threshold: float = 0.7,
) -> list[dict[str, Any]]:
    """Perform semantic search over product embeddings.

    Args:
        db: Async database session.
        query_text: User's natural language query.
        limit: Maximum number of results.
        score_threshold: Minimum cosine similarity score (0.0 - 1.0).

    Returns:
        List of dictionaries with product info and similarity score.
    """
    service = EmbeddingService()
    return await service.search_products(
        db=db,
        query_text=query_text,
        limit=limit,
        score_threshold=score_threshold,
    )


async def upsert_product_embedding(
    db: AsyncSession,
    product_id: int,
    content: str,
) -> None:
    """Generate and store embedding for a product.

    Deletes any existing embeddings for the product first.

    Args:
        db: Async database session.
        product_id: ID of the product to embed.
        content: Text content to generate embedding from.
    """
    service = EmbeddingService()
    await service.upsert_product_embedding(
        db=db,
        product_id=product_id,
        content=content,
    )
