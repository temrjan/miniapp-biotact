"""Pydantic schemas for AI endpoints."""

from decimal import Decimal

from pydantic import BaseModel, Field


class ProductSearchRequest(BaseModel):
    """Request schema for semantic product search."""

    query: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Search query text (e.g., 'витамины для иммунитета')",
    )
    limit: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Maximum number of results to return",
    )
    score_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Minimum cosine similarity score (0.0 - 1.0)",
    )


class ProductSearchResult(BaseModel):
    """Single product search result with similarity score."""

    product_id: int = Field(..., description="Product ID")
    name: str = Field(..., description="Product name")
    slug: str = Field(..., description="Product slug")
    description: str | None = Field(None, description="Product description")
    price: Decimal = Field(..., description="Product price")
    currency: str = Field(..., description="Currency code (e.g., UZS)")
    image_url: str | None = Field(None, description="Primary product image URL")
    score: float = Field(
        ...,
        description="Cosine similarity score between query and product embedding",
    )
    content: str = Field(
        ...,
        description="Matched embedding content chunk",
    )


class ProductSearchResponse(BaseModel):
    """Response schema for semantic product search."""

    results: list[ProductSearchResult] = Field(
        default_factory=list,
        description="List of matching products ranked by relevance",
    )
    total: int = Field(..., description="Total number of results returned")
    query: str = Field(..., description="Original search query")
