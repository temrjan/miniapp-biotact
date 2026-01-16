"""Global dependencies for the application."""

from typing import Annotated

from fastapi import Depends, Query
from pydantic import BaseModel


class PaginationParams(BaseModel):
    """Pagination parameters."""
    page: int = 1
    size: int = 20

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size


def get_pagination(
    page: int = Query(1, ge=1, description="Page number"),
    size: int = Query(20, ge=1, le=100, description="Items per page"),
) -> PaginationParams:
    """Get pagination parameters."""
    return PaginationParams(page=page, size=size)


# Type alias
Pagination = Annotated[PaginationParams, Depends(get_pagination)]
