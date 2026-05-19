"""Embedding service for OpenAI + pgvector vector search."""

import json
import logging
from typing import Any

from fastapi import HTTPException, status
from openai import APIError, AsyncOpenAI, AuthenticationError, RateLimitError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Service for generating text embeddings and performing vector search via pgvector."""

    def __init__(self) -> None:
        api_key = settings.openai_api_key.get_secret_value()
        self.client = AsyncOpenAI(
            api_key=api_key if api_key else None,
            base_url=settings.openai_base_url or None,
        )
        self.model = settings.openai_embedding_model
        self.dimensions = 1536  # text-embedding-3-small default

    async def embed_query(self, text: str) -> list[float]:
        """Generate embedding for a single query text.

        Args:
            text: Text to embed.

        Returns:
            Vector embedding as list of floats.

        Raises:
            HTTPException: If OpenAI API key is invalid or request fails.
        """
        try:
            response = await self.client.embeddings.create(
                model=self.model,
                input=text,
            )
        except AuthenticationError as exc:
            logger.error("OpenAI authentication failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="AI service authentication failed. Check OPENAI_API_KEY.",
            ) from exc
        except RateLimitError as exc:
            logger.error("OpenAI rate limit exceeded: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="AI service rate limit exceeded. Please try again later.",
            ) from exc
        except APIError as exc:
            logger.error("OpenAI API error: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="AI service temporarily unavailable.",
            ) from exc
        return response.data[0].embedding

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts in a single batch call.

        Args:
            texts: List of texts to embed.

        Returns:
            List of vector embeddings.

        Raises:
            HTTPException: If OpenAI API key is invalid or request fails.
        """
        if not texts:
            return []

        try:
            response = await self.client.embeddings.create(
                model=self.model,
                input=texts,
            )
        except AuthenticationError as exc:
            logger.error("OpenAI authentication failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="AI service authentication failed. Check OPENAI_API_KEY.",
            ) from exc
        except RateLimitError as exc:
            logger.error("OpenAI rate limit exceeded: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="AI service rate limit exceeded. Please try again later.",
            ) from exc
        except APIError as exc:
            logger.error("OpenAI API error: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="AI service temporarily unavailable.",
            ) from exc
        return [item.embedding for item in response.data]

    async def search_products(
        self,
        db: AsyncSession,
        query_text: str,
        limit: int = 5,
        score_threshold: float = 0.7,
    ) -> list[dict[str, Any]]:
        """Perform semantic search over product embeddings using pgvector.

        Steps:
            1. Generate embedding for the query text via OpenAI.
            2. Execute cosine similarity search in PostgreSQL using pgvector.
            3. Join with products table to return enriched results.

        Args:
            db: Async database session.
            query_text: User's natural language query.
            limit: Maximum number of results.
            score_threshold: Minimum cosine similarity score (0.0 - 1.0).

        Returns:
            List of dictionaries with product info and similarity score.
        """
        # 1. Generate query embedding
        embedding = await self.embed_query(query_text)

        # 2. Vector search via pgvector
        #    <=>  = cosine distance  (1.0 = opposite, 0.0 = identical)
        #    similarity = 1 - distance
        sql = text(
            """
            SELECT
                pe.product_id,
                pe.content,
                1 - (pe.embedding <=> :embedding) AS score,
                p.name,
                p.slug,
                p.description,
                p.price,
                p.currency,
                p.image_url
            FROM product_embeddings pe
            JOIN products p ON pe.product_id = p.id
            WHERE p.is_active = true
              AND 1 - (pe.embedding <=> :embedding) >= :threshold
            ORDER BY pe.embedding <=> :embedding
            LIMIT :limit
            """
        )

        result = await db.execute(
            sql,
            {
                "embedding": embedding,
                "threshold": score_threshold,
                "limit": limit,
            },
        )

        rows = result.mappings().all()
        return [dict(row) for row in rows]

    async def upsert_product_embedding(
        self,
        db: AsyncSession,
        product_id: int,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Generate embedding for product content and upsert into pgvector.

        Deletes any existing embeddings for the product first, then inserts
        the new one. Commits the transaction.

        Args:
            db: Async database session.
            product_id: ID of the product to embed.
            content: Text content to generate embedding from.
            metadata: Optional JSON metadata to store alongside the embedding.
        """
        # Generate embedding
        embeddings = await self.embed_texts([content])
        embedding = embeddings[0]

        # Delete existing embeddings for this product
        delete_sql = text(
            "DELETE FROM product_embeddings WHERE product_id = :product_id"
        )
        await db.execute(delete_sql, {"product_id": product_id})

        # Insert new embedding
        insert_sql = text(
            """
            INSERT INTO product_embeddings (product_id, content, embedding, metadata)
            VALUES (:product_id, :content, :embedding, :metadata)
            """
        )
        await db.execute(
            insert_sql,
            {
                "product_id": product_id,
                "content": content,
                "embedding": embedding,
                "metadata": json.dumps(metadata or {}),
            },
        )
        await db.commit()
        logger.info("Upserted embedding for product_id=%s", product_id)
