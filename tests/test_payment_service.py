from decimal import Decimal
from uuid import UUID

import pytest
from conftest import CART, METHOD, post_payment, query_rows

from shop.payments.demo.adapters import ALICE
from shop.payments.service import start_payment


@pytest.mark.parametrize(
    "amount,currency",
    [
        (Decimal("0"), "USD"),
        (Decimal("-1"), "USD"),
        (Decimal("1.001"), "USD"),
        (Decimal("NaN"), "USD"),
        (Decimal("Infinity"), "USD"),
        (Decimal("10000000000"), "USD"),
        (70.0, "USD"),
        (Decimal("70"), "usd"),
    ],
)
def test_invalid_total_never_charges(app, amount, currency):
    app.config["TOTAL_SERVICE"] = lambda _: {"amount": amount, "currency": currency}
    assert post_payment(app).status_code == 502
    app.config["PAYMENT_PROVIDER"].assert_not_called()


def test_payment_module_works_without_flask_context(app):
    payment, created = start_payment(
        app.extensions["payment_repository"],
        user_id=ALICE,
        cart_id=UUID(CART),
        method_id=UUID(METHOD),
        key="direct-call",
        total_service=app.config["TOTAL_SERVICE"],
        provider=app.config["PAYMENT_PROVIDER"],
    )
    assert created and payment.status == "succeeded"
    assert payment.amount == Decimal("70.00")
    assert (
        query_rows(app, f"SELECT status FROM carts WHERE id='{CART}'")[0][0]
        == "checked_out"
    )
    # The HTTP entry point sees the same committed attempt.
    assert post_payment(app, key="direct-call").json["id"] == str(payment.id)
    assert app.config["PAYMENT_PROVIDER"].call_count == 1


@pytest.mark.parametrize(
    "quote", [None, (), (Decimal("70"),), (Decimal("70"), "USD", "extra"), "70 USD"]
)
def test_malformed_quote_is_dependency_error(app, quote):
    app.config["TOTAL_SERVICE"] = lambda _: quote
    response = post_payment(app)
    assert response.status_code == 502
    assert response.json["error"] == "Total service returned an invalid quote"
    app.config["PAYMENT_PROVIDER"].assert_not_called()


def test_provider_receives_original_parameters(app):
    response = post_payment(app)
    app.config["PAYMENT_PROVIDER"].assert_called_once_with(
        token="tok_test_alice_visa",
        amount=Decimal("70.00"),
        currency="USD",
        idempotency_key=response.json["id"],
    )


@pytest.mark.parametrize(
    "result",
    [
        None,
        (),
        ("succeeded",),
        {"status": "succeeded", "reference": None},
        {"status": "failed", "failure_code": "secret-token"},
        {"status": "pending"},
        {"status": "succeeded", "reference": "ref", "failure_code": "card_declined"},
        {"status": "failed", "reference": "ref", "failure_code": "card_declined"},
        {"status": "succeeded", "reference": ""},
        {"status": "succeeded", "reference": "ref", "extra": "secret-token"},
    ],
)
def test_invalid_provider_results_remain_pending_but_report_adapter_error(
    app, result, caplog
):
    app.config["PAYMENT_PROVIDER"].side_effect = lambda **_: result
    response = post_payment(app)
    assert response.status_code == 502
    assert query_rows(app, "SELECT status FROM payments")[0][0] == "pending"
    assert (
        "status=502 type=InvalidProviderResponse cause=ValidationError payment="
        in caplog.text
    )
    assert "secret-token" not in caplog.text + response.get_data(as_text=True)
    assert post_payment(app).status_code == 202
    assert app.config["PAYMENT_PROVIDER"].call_count == 1


def test_provider_bug_is_distinct_from_timeout_and_does_not_leak_payload(app, caplog):
    app.config["PAYMENT_PROVIDER"].side_effect = AttributeError("secret-token")
    response = post_payment(app)
    assert response.status_code == 502
    assert "AttributeError" in caplog.text
    assert "secret-token" not in caplog.text + response.get_data(as_text=True)
    assert query_rows(app, "SELECT status FROM payments")[0][0] == "pending"


@pytest.mark.parametrize(
    "quote",
    [
        {"amount": "70.00", "currency": "USD"},
        {"amount": Decimal("70.00"), "currency": "USD", "extra": "secret-token"},
    ],
)
def test_pydantic_quote_rejects_coercion_and_extra_fields(app, quote):
    app.config["TOTAL_SERVICE"] = lambda _: quote
    response = post_payment(app)
    assert response.status_code == 502
    assert "secret-token" not in response.get_data(as_text=True)
    app.config["PAYMENT_PROVIDER"].assert_not_called()


def test_pydantic_revalidates_constructed_quote(app):
    from shop.payments.schemas import Quote

    app.config["TOTAL_SERVICE"] = lambda _: Quote.model_construct(
        amount=Decimal("-1"), currency="USD"
    )
    assert post_payment(app).status_code == 502
    app.config["PAYMENT_PROVIDER"].assert_not_called()
