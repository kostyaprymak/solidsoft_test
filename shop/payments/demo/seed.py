"""Idempotent local sample data."""

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from shop.models import Cart, CartItem, Product, User
from shop.payments.demo.adapters import ALICE, BOB, BOB_CART, DEMO_CART
from shop.payments.models import PaymentMethod

DEMO_QUOTES = {
    str(DEMO_CART): ["70.00", "USD"],
    str(BOB_CART): ["89.99", "USD"],
}


def seed_demo(session: Session) -> None:
    rows = {
        User: [
            {"id": ALICE, "email": "alice@example.com", "name": "Alice"},
            {"id": BOB, "email": "bob@example.com", "name": "Bob"},
        ],
        Product: [
            {
                "id": UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
                "name": "Blue Kettle",
                "price": Decimal("45.00"),
                "currency": "USD",
                "stock_quantity": 10,
            },
            {
                "id": UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
                "name": "Wool Blanket",
                "price": Decimal("89.99"),
                "currency": "USD",
                "stock_quantity": 5,
            },
            {
                "id": UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
                "name": "Ceramic Mug",
                "price": Decimal("12.50"),
                "currency": "USD",
                "stock_quantity": 100,
            },
        ],
        Cart: [
            {"id": DEMO_CART, "user_id": ALICE},
            {"id": BOB_CART, "user_id": BOB},
        ],
        CartItem: [
            {
                "id": UUID("d1111111-1111-1111-1111-111111111111"),
                "cart_id": DEMO_CART,
                "product_id": UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
                "quantity": 1,
                "unit_price": Decimal("45.00"),
            },
            {
                "id": UUID("d2222222-2222-2222-2222-222222222222"),
                "cart_id": DEMO_CART,
                "product_id": UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
                "quantity": 2,
                "unit_price": Decimal("12.50"),
            },
            {
                "id": UUID("d3333333-3333-3333-3333-333333333333"),
                "cart_id": BOB_CART,
                "product_id": UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
                "quantity": 1,
                "unit_price": Decimal("89.99"),
            },
        ],
        PaymentMethod: [
            {
                "id": UUID("11111111-2222-3333-4444-555555555555"),
                "user_id": ALICE,
                "provider_token": "tok_test_alice_visa",
                "last_four": "4242",
                "is_default": True,
            },
            {
                "id": UUID("22222222-2222-3333-4444-555555555555"),
                "user_id": BOB,
                "provider_token": "tok_test_bob_visa",
                "last_four": "4242",
                "is_default": True,
            },
        ],
    }
    for model, values in rows.items():
        existing = set(
            session.scalars(
                select(model.id).where(model.id.in_(row["id"] for row in values))
            )
        )
        missing = [row for row in values if row["id"] not in existing]
        if missing:
            session.execute(insert(model).values(missing).on_conflict_do_nothing())
