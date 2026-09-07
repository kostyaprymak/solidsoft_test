"""Payment orchestration, independent of Flask and HTTP response handling."""

import logging
import re
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from .contracts import PaymentProvider, ProviderResult, Quote, TotalService
from .models import Cart, CartItem, Payment, PaymentMethod

logger = logging.getLogger(__name__)


class PaymentError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def read_quote(total_service: TotalService, cart_id: UUID) -> Quote:
    try:
        value = total_service(cart_id)
    except (LookupError, TimeoutError, ConnectionError) as error:
        raise PaymentError("unavailable", "Total service unavailable") from error
    except Exception as error:
        logger.error("Total adapter failed (%s)", type(error).__name__)
        raise PaymentError(
            "invalid_quote", "Total service returned an invalid quote"
        ) from error
    if not isinstance(value, tuple) or len(value) != 2:
        raise PaymentError("invalid_quote", "Total service returned an invalid quote")
    amount, currency = value
    try:
        valid = (
            isinstance(amount, Decimal)
            and amount.is_finite()
            and 0 < amount < Decimal("10000000000")
            and amount == amount.quantize(Decimal("0.01"))
            and isinstance(currency, str)
            and re.fullmatch("[A-Z]{3}", currency)
        )
    except InvalidOperation:
        valid = False
    if not valid:
        raise PaymentError("invalid_quote", "Total service returned an invalid quote")
    return Quote(amount.quantize(Decimal("0.01")), currency)


def charge_attempt(
    payment: Payment, provider: PaymentProvider
) -> ProviderResult | None:
    if payment.provider_token is None:
        raise PaymentError(
            "conflict",
            "Legacy attempt requires provider reconciliation; original token unavailable",
        )
    try:
        value = provider(
            token=payment.provider_token,
            amount=payment.amount,
            currency=payment.currency,
            idempotency_key=str(payment.id),
        )
    except (TimeoutError, ConnectionError) as error:
        logger.warning(
            "Provider transport failure payment=%s type=%s",
            payment.id,
            type(error).__name__,
        )
        return None
    except Exception as error:
        # Keep the attempt pending but expose the adapter fault, never its payload.
        logger.error(
            "Provider adapter failure payment=%s type=%s",
            payment.id,
            type(error).__name__,
        )
        raise PaymentError(
            "provider_error", "Payment provider adapter failed; payment remains pending"
        ) from error
    if isinstance(value, tuple) and len(value) == 3:
        status, reference, failure = value
        if (
            status == "succeeded"
            and isinstance(reference, str)
            and reference
            and failure is None
            or status == "failed"
            and reference is None
            and failure == "card_declined"
        ):
            return ProviderResult(status, reference, failure)
    logger.error("Invalid provider response payment=%s", payment.id)
    raise PaymentError(
        "provider_error", "Invalid payment provider response; payment remains pending"
    )


def finalize_payment(
    sessions: sessionmaker[Session], payment: Payment, result: ProviderResult
) -> Payment:
    with sessions.begin() as session:
        cart = session.scalars(
            select(Cart).where(Cart.id == payment.cart_id).with_for_update()
        ).one()
        saved = session.get(Payment, payment.id)
        if saved.status != "pending":
            # Concurrent recovery/request finalization is idempotent.
            return saved
        saved.status, saved.provider_reference, saved.failure_code = result
        saved.updated_at = func.now()
        session.flush()
        if saved.status == "succeeded":
            cart.status = "checked_out"
            cart.updated_at = func.now()
    return saved


def reconcile_payment(
    sessions: sessionmaker[Session], payment_id: UUID, provider: PaymentProvider
) -> Payment:
    """Resume with the immutable original parameters and provider idempotency key."""
    with sessions.begin() as session:
        payment = session.get(Payment, payment_id)
        if payment is None:
            raise PaymentError("not_found", "Payment not found")
        if payment.status != "pending":
            return payment
    result = charge_attempt(payment, provider)
    return finalize_payment(sessions, payment, result) if result else payment


def start_payment(
    sessions: sessionmaker[Session],
    *,
    user_id: UUID,
    cart_id: UUID,
    method_id: UUID,
    key: str,
    total_service: TotalService,
    provider: PaymentProvider,
) -> tuple[Payment, bool]:
    """Return the payment and whether this call created it.

    Commit reservation before provider I/O. An unknown outcome remains pending;
    replay never calls the provider again. Sessions are local to each transaction.
    """
    with sessions.begin() as session:
        cart = session.scalar(
            select(Cart)
            .where(Cart.id == cart_id, Cart.user_id == user_id)
            .with_for_update()
        )
        if cart is None:
            raise PaymentError("not_found", "Cart not found")
        previous = session.scalar(
            select(Payment).where(
                Payment.cart_id == cart_id, Payment.idempotency_key == key
            )
        )
        if previous is not None:
            if previous.payment_method_id != method_id:
                raise PaymentError(
                    "conflict",
                    "Idempotency key already used with another payment method",
                )
            return previous, False
        if cart.status != "active":
            raise PaymentError("conflict", "Cart is not active")
        live = session.scalar(
            select(Payment.id).where(
                Payment.cart_id == cart_id,
                Payment.status.in_(("pending", "succeeded")),
            )
        )
        if live is not None:
            raise PaymentError(
                "conflict", "Cart already has a pending or successful payment"
            )
        method = session.scalar(
            select(PaymentMethod).where(
                PaymentMethod.id == method_id, PaymentMethod.user_id == user_id
            )
        )
        if method is None:
            raise PaymentError("not_found", "Payment method not found")
        item = session.scalar(
            select(CartItem.id).where(CartItem.cart_id == cart_id).limit(1)
        )
        if item is None:
            raise PaymentError("conflict", "Cart is empty")
        amount, currency = read_quote(total_service, cart_id)
        payment = Payment(
            cart_id=cart_id,
            payment_method_id=method_id,
            idempotency_key=key,
            amount=amount,
            currency=currency,
            provider_token=method.provider_token,
        )
        session.add(payment)
        session.flush()

    result = charge_attempt(payment, provider)
    return (finalize_payment(sessions, payment, result) if result else payment), True
