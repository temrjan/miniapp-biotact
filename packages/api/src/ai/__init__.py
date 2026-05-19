"""AI module for embeddings and vector search."""

from src.ai.embeddings import EmbeddingService
from src.ai.router import router
from src.ai.schemas import ProductSearchRequest, ProductSearchResponse, ProductSearchResult

__all__ = [
    "EmbeddingService",
    "ProductSearchRequest",
    "ProductSearchResponse",
    "ProductSearchResult",
    "router",
]
