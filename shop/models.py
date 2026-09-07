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
    UniqueConstraint,
    func,
    text,
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


class PaymentMethod(Record, Base):
    __tablename__ = "user_payment_methods"
    __table_args__ = (Index("idx_user_payment_methods_user_id", "user_id"),)

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    provider_token: Mapped[str]
    last_four: Mapped[str | None] = mapped_column(CHAR(4))
    is_default: Mapped[bool] = mapped_column(server_default="false")


class Payment(Record, Base):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("cart_id", "idempotency_key"),
        CheckConstraint(
            "length(idempotency_key) BETWEEN 1 AND 128",
            name="payments_idempotency_key_check",
        ),
        CheckConstraint(
            "amount > 0 AND amount < 10000000000", name="payments_amount_check"
        ),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="payments_currency_check"),
        CheckConstraint(
            "status IN ('pending','succeeded','failed')", name="payments_status_check"
        ),
        CheckConstraint(
            "(status = 'pending' AND provider_reference IS NULL AND failure_code IS NULL) OR "
            "(status = 'succeeded' AND provider_reference IS NOT NULL AND failure_code IS NULL) OR "
            "(status = 'failed' AND provider_reference IS NULL AND failure_code IS NOT NULL)",
            name="payments_check",
        ),
        Index(
            "one_live_payment_per_cart",
            "cart_id",
            unique=True,
            postgresql_where=text("status IN ('pending','succeeded')"),
        ),
    )

    cart_id: Mapped[UUID] = mapped_column(ForeignKey("carts.id"))
    payment_method_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_payment_methods.id")
    )
    idempotency_key: Mapped[str]
    provider_token: Mapped[str | None]
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str]
    status: Mapped[str] = mapped_column(server_default="pending")
    provider_reference: Mapped[str | None] = mapped_column(unique=True)
    failure_code: Mapped[str | None]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


class MockCharge(Record, Base):
    """External provider ledger simulated locally; deliberately no payment FK."""

    __tablename__ = "mock_charges"
    __table_args__ = (
        CheckConstraint(
            "status IN ('succeeded','failed')", name="mock_charges_status_check"
        ),
    )

    token_fingerprint: Mapped[str]
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str]
    status: Mapped[str]
