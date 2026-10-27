"""Derived knowledge and finite, body-free shared control state."""

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql.base import ischema_names
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import UserDefinedType

from src.database import Base


class Vector(UserDefinedType[list[float]]):
    cache_ok = True

    def __init__(self, dimensions: int = 1536) -> None:
        self.dimensions = dimensions

    def get_col_spec(self, **_kw: Any) -> str:
        return f"vector({self.dimensions})"


ischema_names["vector"] = Vector


class Release(Base):
    __tablename__ = "consultant_releases"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB)
    profile: Mapped[str] = mapped_column(String(64))
    approval: Mapped[str] = mapped_column(Text)
    prepared: Mapped[bool] = mapped_column(Boolean, default=False)


class Source(Base):
    __tablename__ = "consultant_sources"
    __table_args__ = (CheckConstraint("language IN ('ru','uz')"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"))
    language: Mapped[str] = mapped_column(String(2))
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB)
    withdrawn: Mapped[bool] = mapped_column(Boolean, default=False)


class Section(Base):
    __tablename__ = "consultant_sections"
    __table_args__ = (UniqueConstraint("source_id", "ordinal"),)
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("consultant_sources.id", ondelete="RESTRICT"))
    ordinal: Mapped[int] = mapped_column(Integer)
    body: Mapped[str] = mapped_column(Text)
    body_hash: Mapped[str] = mapped_column(String(64))
    heading: Mapped[str] = mapped_column(Text)
    caution: Mapped[bool] = mapped_column(Boolean)
    pdf_page: Mapped[int] = mapped_column(Integer)


class Embedding(Base):
    __tablename__ = "consultant_embeddings"
    section_id: Mapped[str] = mapped_column(
        ForeignKey("consultant_sections.id", ondelete="RESTRICT"), primary_key=True
    )
    profile: Mapped[str] = mapped_column(String(64), primary_key=True)
    vector: Mapped[list[float]] = mapped_column(Vector())


class Membership(Base):
    __tablename__ = "consultant_memberships"
    release_id: Mapped[str] = mapped_column(
        ForeignKey("consultant_releases.id", ondelete="RESTRICT"), primary_key=True
    )
    source_id: Mapped[str] = mapped_column(
        ForeignKey("consultant_sources.id", ondelete="RESTRICT"), primary_key=True
    )


class Control(Base):
    __tablename__ = "consultant_control"
    __table_args__ = (CheckConstraint("id = 1"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[str | None] = mapped_column(ForeignKey("consultant_releases.id"))
    lease_owner: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Run(Base):
    __tablename__ = "consultant_runs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tab: Mapped[str] = mapped_column(String(64))
    owner: Mapped[str] = mapped_column(String(36), unique=True)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False)
    done: Mapped[bool] = mapped_column(Boolean, default=False)


class Rate(Base):
    __tablename__ = "consultant_rates"
    id: Mapped[int] = mapped_column(primary_key=True)
    network: Mapped[str] = mapped_column(String(64), index=True)
    lane: Mapped[str] = mapped_column(String(10))
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Budget(Base):
    __tablename__ = "consultant_budgets"
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    received: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    embedding_tokens: Mapped[int] = mapped_column(Integer, default=0)


class Reservation(Base):
    __tablename__ = "consultant_reservations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner: Mapped[str] = mapped_column(String(36))
    day: Mapped[date] = mapped_column(ForeignKey("consultant_budgets.day"))
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    kind: Mapped[str] = mapped_column(String(10))
    received: Mapped[bool] = mapped_column(Boolean, default=False)
