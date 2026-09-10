from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from conftest import (
    CART,
    METHOD,
    URL,
    charge_count,
    execute_sql,
    post_payment,
    query_rows,
)


def test_success_and_replay(app):
    first = post_payment(app)
    assert first.status_code == 201
    assert first.json["amount"] == "70.00"
    assert first.json["status"] == "succeeded"
    replay = post_payment(app)
    assert replay.status_code == 200 and replay.json == first.json
    assert post_payment(app, key="new").status_code == 409
    assert (
        query_rows(app, f"SELECT status FROM carts WHERE id='{CART}'")[0][0]
        == "checked_out"
    )
    assert query_rows(app, "SELECT count(*) FROM payments")[0][0] == 1
    app.config["PAYMENT_PROVIDER"].assert_called_once()
    assert "tok_" not in first.get_data(as_text=True)


def test_decline_can_start_new_attempt(app):
    execute_sql(
        app, "UPDATE user_payment_methods SET provider_token='tok_test_decline'"
    )
    assert post_payment(app).status_code == 402
    assert post_payment(app).status_code == 402
    assert app.config["PAYMENT_PROVIDER"].call_count == 1
    assert (
        query_rows(app, f"SELECT status FROM carts WHERE id='{CART}'")[0][0] == "active"
    )
    execute_sql(
        app, "UPDATE user_payment_methods SET provider_token='tok_test_alice_visa'"
    )
    assert post_payment(app, key="retry").status_code == 201
    assert query_rows(app, "SELECT count(*) FROM payments")[0][0] == 2


def test_unknown_outcome_blocks_recharge(app):
    execute_sql(
        app, "UPDATE user_payment_methods SET provider_token='tok_test_timeout'"
    )
    first = post_payment(app)
    assert first.status_code == 202 and first.json["status"] == "pending"
    assert post_payment(app).json == first.json
    assert post_payment(app, key="different").status_code == 409
    assert app.config["PAYMENT_PROVIDER"].call_count == 1
    assert (
        query_rows(app, f"SELECT status FROM carts WHERE id='{CART}'")[0][0] == "active"
    )


@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_requests_charge_once(app, same_key):
    entered, release = Event(), Event()

    def slow_provider(**kwargs):
        entered.set()
        assert release.wait(5)
        return app.config["PAYMENT_PROVIDER"]._mock_wraps(**kwargs)

    app.config["PAYMENT_PROVIDER"].side_effect = slow_provider
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(post_payment, app)
        try:
            assert entered.wait(5)
            second = pool.submit(
                post_payment, app, "attempt-1" if same_key else "other"
            ).result(5)
            assert second.status_code == (202 if same_key else 409)
        finally:
            release.set()
        assert first.result(5).status_code == 201
    assert app.config["PAYMENT_PROVIDER"].call_count == 1


@pytest.mark.parametrize(
    "sql,code",
    [
        ("UPDATE carts SET user_id='22222222-2222-2222-2222-222222222222'", 404),
        (
            "UPDATE user_payment_methods SET user_id='22222222-2222-2222-2222-222222222222'",
            404,
        ),
        ("UPDATE carts SET status='abandoned'", 409),
        ("DELETE FROM cart_items", 409),
    ],
)
def test_rejects_ineligible_cart_or_method(app, sql, code):
    execute_sql(app, sql)
    assert post_payment(app).status_code == code
    app.config["PAYMENT_PROVIDER"].assert_not_called()
    assert query_rows(app, "SELECT count(*) FROM payments")[0][0] == 0


def test_validation_and_authentication(app):
    with app.test_client() as client:
        response = client.post(URL)
        assert response.status_code == 401
        assert response.json == {"error": "Valid bearer token required"}
        headers = {"Authorization": "Bearer test-secret", "Idempotency-Key": "k"}
        response = client.post(URL, headers=headers, json={})
        assert response.status_code == 400
        assert response.json == {
            "error": "Provide only a valid payment_method_id UUID in a JSON object"
        }
        for body in [
            None,
            [],
            {"payment_method_id": 123},
            {"payment_method_id": METHOD, "amount": 1},
        ]:
            assert client.post(URL, headers=headers, json=body).status_code in (
                400,
                415,
            )
        assert (
            client.post(
                URL, headers=headers, data="{", content_type="application/json"
            ).status_code
            == 400
        )
        assert client.get(URL).status_code == 405
        assert (
            client.post("/carts/not-a-uuid/payments", headers=headers).status_code
            == 404
        )
    response = post_payment(app, key="")
    assert response.status_code == 400
    assert response.json["error"] == (
        "Idempotency-Key must contain 1-128 letters, digits, ., _, : or -"
    )
    assert post_payment(app, key="x" * 129).status_code == 400
    assert post_payment(app, method=str(uuid4())).status_code == 404
    app.config["PAYMENT_PROVIDER"].assert_not_called()


def test_key_cannot_change_method(app):
    assert post_payment(app).status_code == 201
    assert post_payment(app, method=str(uuid4())).status_code == 409
    assert app.config["PAYMENT_PROVIDER"].call_count == 1


def test_two_authenticated_users_own_their_carts_and_methods(app):
    from shop.payments.demo.adapters import BOB_CART

    bob_method = "22222222-2222-3333-4444-555555555555"
    headers = {"Authorization": "Bearer bob-secret", "Idempotency-Key": "bob-attempt"}
    with app.test_client() as client:
        assert (
            client.post(
                URL, headers=headers, json={"payment_method_id": METHOD}
            ).status_code
            == 404
        )
        bob_url = f"/carts/{BOB_CART}/payments"
        assert (
            client.post(
                bob_url, headers=headers, json={"payment_method_id": METHOD}
            ).status_code
            == 404
        )
        response = client.post(
            bob_url, headers=headers, json={"payment_method_id": bob_method}
        )
        assert response.status_code == 201 and response.json["amount"] == "89.99"
    assert post_payment(app, method=bob_method).status_code == 404
    assert post_payment(app).status_code == 201
    assert charge_count(app) == 2
