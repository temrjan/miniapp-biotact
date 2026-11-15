"""Strict, body-safe public and provider boundaries."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

Language = Literal["ru", "uz"]
PageView = Literal["home", "catalogue", "product", "account", "order", "invalid"]
ProductId = Annotated[int, Field(strict=True, gt=0, le=9007199254740991)]
Text = Annotated[str, Field(min_length=1)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PageContext(Strict):
    view: PageView
    category_id: ProductId | None = None


class Pair(Strict):
    # Earlier questions keep the former 2,000-character bound, so histories written before
    # the 1,000-character question cap stay valid.
    question: Annotated[str, Field(min_length=1, max_length=2000)]
    answer: Text


class Turn(Strict):
    request_id: UUID
    tab_id: UUID
    context_revision: Annotated[int, Field(ge=0, le=9007199254740991)]
    language: Language
    question: Annotated[str, Field(min_length=1, max_length=1000)]
    history: list[Pair]
    page_product_id: ProductId | None
    context_token: Annotated[str, Field(max_length=2048)] | None
    page_context: PageContext | None = None

    @model_validator(mode="after")
    def bounds(self) -> "Turn":
        if not self.question.strip() or self.question != self.question.strip():
            raise ValueError("invalid_input")
        if self.context_token is None and self.history:
            raise ValueError("context_invalid")
        return self


class Cancel(Strict):
    request_id: UUID
    tab_id: UUID


class Refresh(Strict):
    product_ids: Annotated[list[ProductId], Field(min_length=1, max_length=2)]
    language: Language

    @model_validator(mode="after")
    def unique(self) -> "Refresh":
        if len(set(self.product_ids)) != len(self.product_ids):
            raise ValueError("invalid_input")
        return self


class Proposal(Strict):
    status: Literal["answer", "clarification", "limited", "refused"]
    answer: Text
    source_ids: Annotated[list[str], Field(max_length=12)]
    product_ids: Annotated[list[ProductId], Field(max_length=2)]
    open_product_id: ProductId | None

    @model_validator(mode="after")
    def unique(self) -> "Proposal":
        if not self.answer.strip():
            raise ValueError("invalid_answer")
        if len(set(self.source_ids)) != len(self.source_ids) or len(set(self.product_ids)) != len(
            self.product_ids
        ):
            raise ValueError("invalid_answer")
        return self


class ContextArgs(Strict):
    product_ids: Annotated[list[ProductId], Field(max_length=2)]
    sections: Annotated[
        list[Literal["description", "composition", "use", "effects", "quality", "storage"]],
        Field(max_length=6),
    ]

    @model_validator(mode="after")
    def unique(self) -> "ContextArgs":
        if len(set(self.product_ids)) != len(self.product_ids) or len(set(self.sections)) != len(
            self.sections
        ):
            raise ValueError("invalid_answer")
        return self


class Fault(Exception):
    def __init__(self, code: str, status: int = 503) -> None:
        self.code = code
        self.status = status
        super().__init__(code)

    @property
    def retryable(self) -> bool:
        return self.status in {429, 502, 503, 504}
