"""Product SQLAlchemy models."""

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base

if TYPE_CHECKING:
    from src.categories.models import Category


class Product(Base):
    """Product model."""

    __tablename__ = "products"
    # GIN-индексы существуют на проде (созданы вне цепочки) — объявлены здесь, чтобы
    # модель == прод и автоген против прода их не дропал (см. reconcile-миграцию).
    __table_args__ = (
        CheckConstraint(
            "price > 0 AND (NOT is_sale OR (sale_price IS NOT NULL AND "
            "sale_price > 0 AND sale_price < price))",
            name="products_sale_ck",
        ),
        Index("idx_products_images_gin", "images", postgresql_using="gin"),
        Index("idx_products_advantages_gin", "advantages", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    name_uz: Mapped[str | None] = mapped_column(String(200))
    # ⚠️ Не только адрес: окончание slug'а (`_caps` / `_stik`) читается как ФОРМА ВЫПУСКА и
    # печатается в уведомлении отделу продаж — см. `RELEASE_FORM_BY_SLUG_SUFFIX` в
    # `notifications/service.py`. Переименование slug'а меняет то, что увидит человек, готовящий
    # отгрузку.
    slug: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    description_uz: Mapped[str | None] = mapped_column(Text)

    # Pricing
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    old_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    is_sale: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    sale_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="UZS")

    # Stock
    stock_quantity: Mapped[int] = mapped_column(Integer, default=0)
    is_in_stock: Mapped[bool] = mapped_column(Boolean, default=True)

    # Media
    image_url: Mapped[str | None] = mapped_column(String(500))
    # JSONB stores as list in Python
    images: Mapped[list[str] | None] = mapped_column(
        JSONB, default=list, comment="Array of image URLs for product gallery"
    )

    # Category
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"),
        index=True,
    )

    # OFD / Fiscal data (Multicard integration)
    # Сумма для Multicard: price * 100 (тийины)
    mxik: Mapped[str | None] = mapped_column(String(17))  # ИКПУ из tasnif.soliq.uz (17 символов)
    package_code: Mapped[str | None] = mapped_column(String(7))  # Код упаковки (7 символов)
    vat: Mapped[int | None] = mapped_column(Integer)  # НДС в процентах (0, 12, 15)

    # Additional info - JSONB array of advantages
    advantages: Mapped[list[str] | None] = mapped_column(
        JSONB, default=list, comment="Array of product benefits/advantages"
    )
    video_url: Mapped[str | None] = mapped_column(String(500))  # YouTube video URL

    # Custom information sections
    additional_info_title: Mapped[str | None] = mapped_column(
        String(200)
    )  # Title for additional info section
    additional_info_title_uz: Mapped[str | None] = mapped_column(String(200))
    additional_info: Mapped[str | None] = mapped_column(Text)  # Additional info text
    additional_info_uz: Mapped[str | None] = mapped_column(Text)
    disclaimer: Mapped[str | None] = mapped_column(
        Text
    )  # Disclaimer text (e.g., "БАД. Не является лекарственным средством.")
    disclaimer_uz: Mapped[str | None] = mapped_column(Text)

    # Metadata
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationships
    category: Mapped[Optional["Category"]] = relationship(back_populates="products")

    def __repr__(self) -> str:
        return f"<Product(id={self.id}, name={self.name})>"
