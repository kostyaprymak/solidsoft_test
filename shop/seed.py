"""Explicit local sample data; never run as part of schema migration."""

from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from .adapters import ALICE, BOB, BOB_CART, DEMO_CART
from .models import Cart, CartItem, PaymentMethod, Product, User


def seed_demo(session: Session) -> None:
    session.add_all(
        [
            User(id=ALICE, email="alice@example.com", name="Alice"),
            User(
                id=UUID("22222222-2222-2222-2222-222222222222"),
                email="bob@example.com",
                name="Bob",
            ),
        ]
    )
    session.flush()
    products = [
        Product(
            id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            name="Blue Kettle",
            price=Decimal("45.00"),
            currency="USD",
            stock_quantity=10,
        ),
        Product(
            id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
            name="Wool Blanket",
            price=Decimal("89.99"),
            currency="USD",
            stock_quantity=5,
        ),
        Product(
            id=UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
            name="Ceramic Mug",
            price=Decimal("12.50"),
            currency="USD",
            stock_quantity=100,
        ),
    ]
    session.add_all(products)
    session.add(Cart(id=DEMO_CART, user_id=ALICE))
    session.add(Cart(id=BOB_CART, user_id=BOB))
    session.flush()
    session.add_all(
        [
            CartItem(
                cart_id=BOB_CART,
                product_id=products[1].id,
                quantity=1,
                unit_price=Decimal("89.99"),
            ),
            PaymentMethod(
                id=UUID("22222222-2222-3333-4444-555555555555"),
                user_id=BOB,
                provider_token="tok_test_bob_visa",
                last_four="4242",
                is_default=True,
            ),
            CartItem(
                cart_id=DEMO_CART,
                product_id=products[0].id,
                quantity=1,
                unit_price=Decimal("45.00"),
            ),
            CartItem(
                cart_id=DEMO_CART,
                product_id=products[2].id,
                quantity=2,
                unit_price=Decimal("12.50"),
            ),
            PaymentMethod(
                id=UUID("11111111-2222-3333-4444-555555555555"),
                user_id=ALICE,
                provider_token="tok_test_alice_visa",
                last_four="4242",
                is_default=True,
            ),
        ]
    )
