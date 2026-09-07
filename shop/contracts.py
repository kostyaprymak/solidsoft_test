"""Contracts for the integrations supplied by the surrounding shop."""

from collections.abc import Callable
from decimal import Decimal
from typing import NamedTuple, Protocol
from uuid import UUID


class Quote(NamedTuple):
    amount: Decimal
    currency: str


class ProviderResult(NamedTuple):
    status: str
    reference: str | None
    failure_code: str | None


class PaymentProvider(Protocol):
    """Repeated calls with identical parameters/key must return the same outcome.

    A real adapter must bound network timeouts and honor its provider's key-retention
    window. It must reconcile expired keys, never blindly submit a new charge.
    """

    def __call__(
        self, *, token: str, amount: Decimal, currency: str, idempotency_key: str
    ) -> ProviderResult: ...


TotalService = Callable[[UUID], Quote]
IdentityResolver = Callable[[str], UUID | None]
