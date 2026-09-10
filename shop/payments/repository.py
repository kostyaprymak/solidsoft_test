from collections.abc import Callable
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from shop.models import Cart, CartItem
from shop.payments.entities import Payment as PaymentEntity
from shop.payments.errors import PaymentConflict, PaymentNotFound
from shop.payments.models import Payment, PaymentMethod
from shop.payments.schemas import ProviderResult, Quote


def to_entity(payment: Payment) -> PaymentEntity:
    return PaymentEntity(
        id=payment.id,
        cart_id=payment.cart_id,
        payment_method_id=payment.payment_method_id,
        idempotency_key=payment.idempotency_key,
        provider_token=payment.provider_token,
        amount=payment.amount,
        currency=payment.currency,
        status=payment.status,
        provider_reference=payment.provider_reference,
        failure_code=payment.failure_code,
    )


class PaymentRepository:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    def reserve_payment_with_locked_quote(
        self,
        *,
        user_id: UUID,
        cart_id: UUID,
        method_id: UUID,
        key: str,
        quote_reader: Callable[[], Quote],
    ) -> tuple[PaymentEntity, bool]:
        with self.sessions.begin() as session:
            cart = session.scalar(
                select(Cart)
                .where(Cart.id == cart_id, Cart.user_id == user_id)
                .with_for_update()
            )
            if cart is None:
                raise PaymentNotFound("Cart not found")
            previous = session.scalar(
                select(Payment).where(
                    Payment.cart_id == cart_id, Payment.idempotency_key == key
                )
            )
            if previous is not None:
                if previous.payment_method_id != method_id:
                    raise PaymentConflict(
                        "Idempotency key already used with another payment method"
                    )
                return to_entity(previous), False
            if cart.status != "active":
                raise PaymentConflict("Cart is not active")
            live = session.scalar(
                select(Payment.id).where(
                    Payment.cart_id == cart_id,
                    Payment.status.in_(("pending", "succeeded")),
                )
            )
            if live is not None:
                raise PaymentConflict(
                    "Cart already has a pending or successful payment"
                )
            method = session.scalar(
                select(PaymentMethod).where(
                    PaymentMethod.id == method_id, PaymentMethod.user_id == user_id
                )
            )
            if method is None:
                raise PaymentNotFound("Payment method not found")
            item = session.scalar(
                select(CartItem.id).where(CartItem.cart_id == cart_id).limit(1)
            )
            if item is None:
                raise PaymentConflict("Cart is empty")
            quote = quote_reader()
            payment = Payment(
                cart_id=cart_id,
                payment_method_id=method_id,
                idempotency_key=key,
                amount=quote.amount,
                currency=quote.currency,
                provider_token=method.provider_token,
            )
            session.add(payment)
            session.flush()
        return to_entity(payment), True

    def get_payment(self, payment_id: UUID) -> PaymentEntity | None:
        with self.sessions.begin() as session:
            payment = session.get(Payment, payment_id)
            return to_entity(payment) if payment else None

    def finalize_payment(
        self, *, payment_id: UUID, result: ProviderResult
    ) -> PaymentEntity:
        with self.sessions.begin() as session:
            payment = session.get(Payment, payment_id)
            cart = session.scalars(
                select(Cart).where(Cart.id == payment.cart_id).with_for_update()
            ).one()
            payment = session.get(Payment, payment_id, populate_existing=True)
            if payment.status != "pending":
                return to_entity(payment)
            payment.status = result.status
            payment.provider_reference = result.reference
            payment.failure_code = result.failure_code
            payment.updated_at = func.now()
            session.flush()
            if payment.status == "succeeded":
                cart.status = "checked_out"
                cart.updated_at = func.now()
        return to_entity(payment)
