"""Configurable local substitutes; no live card processing or total calculation."""

from decimal import Decimal
from hashlib import sha256
from hmac import compare_digest
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from .contracts import ProviderResult, Quote
from .models import MockCharge

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
        return Quote(Decimal(amount), currency)


class MockProvider:
    """Durable provider simulator with an independent transaction per charge.

    This table stands in for the external provider's storage. It intentionally has
    no foreign key to payments, and its commit survives payment finalization errors.
    """

    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    def __call__(
        self, *, token: str, amount: Decimal, currency: str, idempotency_key: str
    ) -> ProviderResult:
        fingerprint = sha256(token.encode()).hexdigest()
        status = "failed" if token == "tok_test_decline" else "succeeded"
        with self.sessions.begin() as session:
            inserted = session.scalar(
                insert(MockCharge)
                .values(
                    id=UUID(idempotency_key),
                    token_fingerprint=fingerprint,
                    amount=amount,
                    currency=currency,
                    status=status,
                )
                .on_conflict_do_nothing(index_elements=["id"])
                .returning(MockCharge.id)
            )
            charge = session.scalars(
                select(MockCharge).where(MockCharge.id == UUID(idempotency_key))
            ).one()
            if (charge.amount, charge.currency, charge.token_fingerprint) != (
                amount,
                currency,
                fingerprint,
            ):
                raise ValueError("Provider idempotency parameters changed")
            result = ProviderResult(
                charge.status,
                f"mock_{charge.id}" if charge.status == "succeeded" else None,
                "card_declined" if charge.status == "failed" else None,
            )
        if inserted and token == "tok_test_timeout":
            # The charge committed, but the first response was lost in transit.
            raise TimeoutError
        return result
