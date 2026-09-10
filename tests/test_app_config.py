from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from conftest import METHOD, post_payment, run_demo_cli

from shop.app import create_app
from shop.payments.demo.adapters import ALICE


def test_database_url_is_required_in_every_profile(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        create_app()
    monkeypatch.setenv("APP_CONFIG", "demo")
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        create_app()


def test_demo_profile_loads_fixture_data_and_adapters(app, monkeypatch):
    monkeypatch.setenv("APP_CONFIG", "demo")
    monkeypatch.setenv("DEMO_API_TOKEN", "demo-secret")
    monkeypatch.setenv(
        "DATABASE_URL", app.config["DATABASE_URL"].render_as_string(hide_password=False)
    )
    demo = create_app()
    repeated = create_app()
    try:
        assert demo.config["IDENTITY_RESOLVER"]("Bearer demo-secret") == ALICE
        from conftest import query_rows

        assert query_rows(demo, "SELECT count(*) FROM users")[0][0] >= 2
    finally:
        repeated.extensions["db"].dispose()
        demo.extensions["db"].dispose()


@pytest.mark.parametrize(
    "token,expected_status", [(None, "succeeded"), ("tok_test_timeout", "pending")]
)
def test_demo_restarts_after_payment(app, monkeypatch, token, expected_status):
    monkeypatch.setenv("APP_CONFIG", "demo")
    monkeypatch.setenv("DEMO_API_TOKEN", "test-secret")
    monkeypatch.setenv(
        "DATABASE_URL", app.config["DATABASE_URL"].render_as_string(hide_password=False)
    )
    demo = create_app()
    try:
        if token:
            from conftest import execute_sql

            execute_sql(
                demo, f"UPDATE user_payment_methods SET provider_token='{token}'"
            )
        response = post_payment(demo)
        assert response.json["status"] == expected_status
        restarted = run_demo_cli(demo, "routes")
        assert restarted.returncode == 0, restarted.stderr
    finally:
        demo.extensions["db"].dispose()


def test_injected_identity_and_quote_support_another_cart(app):
    from shop.models import Cart, CartItem

    new_cart = uuid4()
    with app.extensions["sessions"].begin() as session:
        session.add(Cart(id=new_cart, user_id=ALICE))
        session.flush()
        session.add(
            CartItem(
                cart_id=new_cart,
                product_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
                quantity=1,
                unit_price=Decimal("45"),
            )
        )
    app.config["IDENTITY_RESOLVER"] = lambda header: (
        ALICE if header == "Bearer upstream-token" else None
    )
    app.config["TOTAL_SERVICE"] = lambda cart_id: (
        {"amount": Decimal("45"), "currency": "USD"} if cart_id == new_cart else None
    )
    with app.test_client() as client:
        response = client.post(
            f"/carts/{new_cart}/payments",
            headers={
                "Authorization": "Bearer upstream-token",
                "Idempotency-Key": "custom",
            },
            json={"payment_method_id": METHOD},
        )
    assert response.status_code == 201 and response.json["amount"] == "45.00"
