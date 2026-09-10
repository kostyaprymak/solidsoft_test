"""Local identity, total, and payment-provider substitutes."""

from decimal import Decimal
from hashlib import sha256
from hmac import compare_digest
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from shop.payments.models import MockCharge
from shop.payments.schemas import ProviderResult, Quote

ALICE = UUID("11111111-1111-1111-1111-111111111111")
BOB = UUID("22222222-2222-2222-2222-222222222222")
DEMO_CART = UUID("c1c1c1c1-c1c1-c1c1-c1c1-c1c1c1c1c1c1")
BOB_CART = UUID("c2c2c2c2-c2c2-c2c2-c2c2-c2c2c2c2c2c2")


class DemoIdentityResolver:
    def __init__(self, identities: dict[str, str]):
        self.identities = {
            token: UUID(user) for token, user in identities.items() if token
        }

    def __call__(self, authorization: str) -> UUID | None:
        for token, user in self.identities.items():
            if compare_digest(authorization.encode(), f"Bearer {token}".encode()):
                return user
        return None


class DemoTotalService:
    def __init__(self, quotes: dict[str, list[str]]):
        self.quotes = quotes

    def __call__(self, cart_id: UUID) -> Quote:
        amount, currency = self.quotes[str(cart_id)]
        return Quote(amount=Decimal(amount), currency=currency)


class MockProvider:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    def __call__(
        self, *, token: str, amount: Decimal, currency: str, idempotency_key: str
    ) -> ProviderResult:
        payment_id = UUID(idempotency_key)
        fingerprint = sha256(token.encode()).hexdigest()
        status = "failed" if token == "tok_test_decline" else "succeeded"
        with self.sessions.begin() as session:
            inserted = session.scalar(
                insert(MockCharge)
                .values(
                    id=payment_id,
                    token_fingerprint=fingerprint,
                    amount=amount,
                    currency=currency,
                    status=status,
                )
                .on_conflict_do_nothing(index_elements=["id"])
                .returning(MockCharge.id)
            )
            charge = session.scalars(
                select(MockCharge).where(MockCharge.id == payment_id)
            ).one()
            if (charge.amount, charge.currency, charge.token_fingerprint) != (
                amount,
                currency,
                fingerprint,
            ):
                raise ValueError("Provider idempotency parameters changed")
            result = ProviderResult(
                status=charge.status,
                reference=f"mock_{payment_id}"
                if charge.status == "succeeded"
                else None,
                failure_code="card_declined" if charge.status == "failed" else None,
            )
        if inserted and token == "tok_test_timeout":
            raise TimeoutError
        return result
