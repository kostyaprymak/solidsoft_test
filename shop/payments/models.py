from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from shop.models import Base, Record


class PaymentMethod(Record, Base):
    __tablename__ = "user_payment_methods"
    __table_args__ = (Index("idx_user_payment_methods_user_id", "user_id"),)

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    provider_token: Mapped[str]
    last_four: Mapped[str | None] = mapped_column(CHAR(4))
    is_default: Mapped[bool] = mapped_column(server_default="false")


class MockCharge(Record, Base):
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
