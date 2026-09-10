from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True)
class Payment:
    id: UUID
    cart_id: UUID
    payment_method_id: UUID
    idempotency_key: str
    provider_token: str | None
    amount: Decimal
    currency: str
    status: str
    provider_reference: str | None
    failure_code: str | None
