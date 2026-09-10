from collections.abc import Callable
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from shop.payments.entities import Payment
from shop.payments.schemas import ProviderResult, Quote


class IdentityResolver(Protocol):
    def __call__(self, authorization: str) -> UUID | None: ...


class TotalService(Protocol):
    def __call__(self, cart_id: UUID) -> Quote: ...


class PaymentProvider(Protocol):
    def __call__(
        self, *, token: str, amount: Decimal, currency: str, idempotency_key: str
    ) -> ProviderResult: ...


class PaymentRepository(Protocol):
    def reserve_payment_with_locked_quote(
        self,
        *,
        user_id: UUID,
        cart_id: UUID,
        method_id: UUID,
        key: str,
        quote_reader: Callable[[], Quote],
    ) -> tuple[Payment, bool]: ...

    def get_payment(self, payment_id: UUID) -> Payment | None: ...

    def finalize_payment(
        self, *, payment_id: UUID, result: ProviderResult
    ) -> Payment: ...
