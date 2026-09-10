"""ORM mappings for the existing schema; SQL migrations own database constraints."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map = {str: Text, datetime: DateTime(timezone=True)}


class Record:
    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=func.gen_random_uuid()
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class User(Record, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(unique=True)
    name: Mapped[str]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Product(Record, Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("price >= 0", name="products_price_check"),
        CheckConstraint("stock_quantity >= 0", name="products_stock_quantity_check"),
    )

    name: Mapped[str]
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(CHAR(3), server_default="USD")
    stock_quantity: Mapped[int] = mapped_column(server_default="0")
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Cart(Record, Base):
    __tablename__ = "carts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','checked_out','abandoned')", name="carts_status_check"
        ),
        Index("idx_carts_user_id", "user_id"),
    )

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(server_default="active")
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


class CartItem(Record, Base):
    __tablename__ = "cart_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="cart_items_quantity_check"),
        Index("idx_cart_items_cart_id", "cart_id"),
    )

    cart_id: Mapped[UUID] = mapped_column(ForeignKey("carts.id", ondelete="CASCADE"))
    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"))
    quantity: Mapped[int]
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
