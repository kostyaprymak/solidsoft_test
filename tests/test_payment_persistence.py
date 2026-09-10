from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier, Event

import pytest
from conftest import CART, charge_count, execute_sql, post_payment, query_rows
from sqlalchemy.exc import IntegrityError


def test_database_enforces_one_live_payment(app):
    assert post_payment(app).status_code == 201
    with pytest.raises(IntegrityError):
        execute_sql(
            app,
            """INSERT INTO payments (cart_id,payment_method_id,idempotency_key,amount,currency)
                    SELECT cart_id,payment_method_id,'other',amount,currency FROM payments""",
        )


def test_finalization_failure_preserves_pending_attempt(app):
    execute_sql(
        app,
        """CREATE FUNCTION reject_checkout() RETURNS trigger LANGUAGE plpgsql AS $$
                 BEGIN RAISE EXCEPTION 'simulated database failure'; END; $$""",
    )
    execute_sql(
        app,
        "CREATE TRIGGER fail_checkout BEFORE UPDATE ON carts FOR EACH ROW EXECUTE FUNCTION reject_checkout()",
    )
    assert post_payment(app).status_code == 500
    assert query_rows(app, "SELECT status FROM payments")[0][0] == "pending"
    assert (
        query_rows(app, f"SELECT status FROM carts WHERE id='{CART}'")[0][0] == "active"
    )
    assert post_payment(app).status_code == 202
    assert app.config["PAYMENT_PROVIDER"].call_count == 1


@pytest.mark.parametrize(
    "sql",
    [
        f"UPDATE cart_items SET quantity=quantity+1 WHERE cart_id='{CART}'",
        f"DELETE FROM cart_items WHERE cart_id='{CART}'",
        f"INSERT INTO cart_items(cart_id,product_id,quantity,unit_price) VALUES ('{CART}','aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',1,45)",
        f"UPDATE carts SET status='abandoned' WHERE id='{CART}'",
        f"UPDATE carts SET user_id='22222222-2222-2222-2222-222222222222' WHERE id='{CART}'",
        f"UPDATE cart_items SET cart_id='c2c2c2c2-c2c2-c2c2-c2c2-c2c2c2c2c2c2' WHERE cart_id='{CART}'",
        f"UPDATE cart_items SET cart_id='{CART}' WHERE cart_id='c2c2c2c2-c2c2-c2c2-c2c2-c2c2c2c2c2c2'",
    ],
)
def test_database_freezes_cart_while_payment_pending(app, sql):
    execute_sql(
        app, "UPDATE user_payment_methods SET provider_token='tok_test_timeout'"
    )
    assert post_payment(app).status_code == 202
    with pytest.raises(IntegrityError):
        execute_sql(app, sql)
    assert (
        query_rows(app, f"SELECT sum(quantity) FROM cart_items WHERE cart_id='{CART}'")[
            0
        ][0]
        == 3
    )


def test_decline_allows_cart_edits_but_paid_cart_is_frozen(app):
    execute_sql(
        app, "UPDATE user_payment_methods SET provider_token='tok_test_decline'"
    )
    assert post_payment(app).status_code == 402
    execute_sql(
        app, f"UPDATE cart_items SET quantity=quantity+1 WHERE cart_id='{CART}'"
    )
    # The total is supplied by the existing service for the new cart contents.
    app.config["TOTAL_SERVICE"] = lambda _: {
        "amount": Decimal("127.50"),
        "currency": "USD",
    }
    execute_sql(
        app, "UPDATE user_payment_methods SET provider_token='tok_test_alice_visa'"
    )
    assert post_payment(app, key="new").status_code == 201
    with pytest.raises(IntegrityError):
        execute_sql(app, f"DELETE FROM cart_items WHERE cart_id='{CART}'")


def test_simultaneous_payment_creation_is_serialized(app):
    ready = Barrier(4)

    def request_payment():
        ready.wait(5)
        return post_payment(app)

    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: request_payment(), range(4)))
    assert sum(response.status_code == 201 for response in responses) == 1
    assert all(response.status_code in (200, 201, 202) for response in responses)
    assert len({response.json["id"] for response in responses}) == 1
    assert app.config["PAYMENT_PROVIDER"].call_count == 1
    assert charge_count(app) == 1


def test_cart_writer_waiting_on_quote_cannot_change_reserved_cart(app):
    quoting, release, writer_started = Event(), Event(), Event()

    def slow_quote(_):
        quoting.set()
        assert release.wait(5)
        return {"amount": Decimal("70.00"), "currency": "USD"}

    def change_item():
        writer_started.set()
        execute_sql(app, f"UPDATE cart_items SET quantity=10 WHERE cart_id='{CART}'")

    app.config["TOTAL_SERVICE"] = slow_quote
    with ThreadPoolExecutor(max_workers=2) as pool:
        payment = pool.submit(post_payment, app)
        try:
            assert quoting.wait(5)
            writer = pool.submit(change_item)
            assert writer_started.wait(5)
        finally:
            release.set()
        assert payment.result(5).status_code == 201
        with pytest.raises(IntegrityError):
            writer.result(5)
    assert (
        query_rows(app, f"SELECT sum(quantity) FROM cart_items WHERE cart_id='{CART}'")[
            0
        ][0]
        == 3
    )
