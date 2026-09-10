"""Payment orchestration, independent of Flask and persistence."""

import logging
from uuid import UUID

from pydantic import ValidationError

from shop.payments.contracts import PaymentProvider, PaymentRepository, TotalService
from shop.payments.entities import Payment
from shop.payments.errors import (
    DependencyUnavailable,
    InvalidProviderResponse,
    InvalidQuote,
    PaymentConflict,
    PaymentNotFound,
    ProviderFailure,
    TotalServiceFailure,
)
from shop.payments.schemas import ProviderResult, Quote

logger = logging.getLogger(__name__)


def read_quote(total_service: TotalService, cart_id: UUID) -> Quote:
    try:
        return Quote.model_validate(total_service(cart_id))
    except (LookupError, TimeoutError, ConnectionError) as error:
        raise DependencyUnavailable("Total service unavailable") from error
    except ValidationError as error:
        raise InvalidQuote("Total service returned an invalid quote") from error
    except Exception as error:
        raise TotalServiceFailure("Total service adapter failed") from error


def charge_attempt(
    payment: Payment, provider: PaymentProvider
) -> ProviderResult | None:
    if payment.provider_token is None:
        raise PaymentConflict(
            "Legacy attempt requires provider reconciliation; original token unavailable"
        )
    try:
        value = provider(
            token=payment.provider_token,
            amount=payment.amount,
            currency=payment.currency,
            idempotency_key=str(payment.id),
        )
        return ProviderResult.model_validate(value)
    except ValidationError as error:
        raise InvalidProviderResponse(
            "Invalid payment provider response; payment remains pending",
            payment_id=payment.id,
        ) from error
    except (TimeoutError, ConnectionError) as error:
        logger.warning(
            "Provider transport failure payment=%s type=%s",
            payment.id,
            type(error).__name__,
        )
        return None
    except Exception as error:
        raise ProviderFailure(
            "Payment provider adapter failed; payment remains pending",
            payment_id=payment.id,
        ) from error


def reconcile_payment(
    repository: PaymentRepository,
    payment_id: UUID,
    provider: PaymentProvider,
) -> Payment:
    payment = repository.get_payment(payment_id)
    if payment is None:
        raise PaymentNotFound("Payment not found")
    if payment.status != "pending":
        return payment
    result = charge_attempt(payment, provider)
    return (
        repository.finalize_payment(payment_id=payment.id, result=result)
        if result
        else payment
    )


def start_payment(
    repository: PaymentRepository,
    *,
    user_id: UUID,
    cart_id: UUID,
    method_id: UUID,
    key: str,
    total_service: TotalService,
    provider: PaymentProvider,
) -> tuple[Payment, bool]:
    payment, created = repository.reserve_payment_with_locked_quote(
        user_id=user_id,
        cart_id=cart_id,
        method_id=method_id,
        key=key,
        quote_reader=lambda: read_quote(total_service, cart_id),
    )
    if not created:
        return payment, False
    result = charge_attempt(payment, provider)
    if result:
        payment = repository.finalize_payment(payment_id=payment.id, result=result)
    return payment, True
