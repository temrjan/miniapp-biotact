"""AI API router for embeddings and semantic search."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.embeddings import EmbeddingService
from src.ai.schemas import (
    ProductSearchRequest,
    ProductSearchResponse,
    ProductSearchResult,
)
from src.auth.dependencies import get_current_user, verify_admin_jwt
from src.database import get_db

router = APIRouter()

DbSession = Annotated[AsyncSession, Depends(get_db)]


class ProductEmbedRequest(BaseModel):
    """Request to generate and store embedding for a product."""

    content: str = Field(
        ...,
        min_length=1,
        max_length=10000,
        description="Text content to embed (e.g. product description + advantages)",
    )


@router.post(
    "/search-products",
    response_model=ProductSearchResponse,
    dependencies=[Depends(get_current_user)],
)
async def search_products(
    request: ProductSearchRequest,
    db: DbSession,
) -> ProductSearchResponse:
    """Semantic search for products using AI embeddings.

    Performs vector similarity search over product descriptions and content
    stored in pgvector. Returns products ranked by cosine similarity.

    - **query**: Natural language search query (e.g. "витамины для иммунитета")
    - **limit**: Max results (1-20, default 5)
    - **score_threshold**: Minimum similarity score (0.0-1.0, default 0.7)
    """
    service = EmbeddingService()
    rows = await service.search_products(
        db=db,
        query_text=request.query,
        limit=request.limit,
        score_threshold=request.score_threshold,
    )

    return ProductSearchResponse(
        results=[
            ProductSearchResult(
                product_id=row["product_id"],
                name=row["name"],
                slug=row["slug"],
                description=row["description"],
                price=row["price"],
                currency=row["currency"],
                image_url=row["image_url"],
                score=row["score"],
                content=row["content"],
            )
            for row in rows
        ],
        total=len(rows),
        query=request.query,
    )


@router.post(
    "/products/{product_id}/embed",
    dependencies=[Depends(verify_admin_jwt)],
)
async def embed_product(
    product_id: int,
    request: ProductEmbedRequest,
    db: DbSession,
) -> dict[str, Any]:
    """Generate and store embedding for a product (admin only).

    Deletes any existing embeddings for the product and inserts a new one.

    - **product_id**: Product ID to embed
    - **content**: Text content to generate embedding from
    """
    service = EmbeddingService()
    await service.upsert_product_embedding(
        db=db,
        product_id=product_id,
        content=request.content,
    )
    return {"status": "ok", "product_id": product_id}
