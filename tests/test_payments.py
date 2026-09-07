import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from threading import Event
from unittest.mock import Mock
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from shop import create_app
from shop.adapters import MockProvider
from shop.seed import seed_demo

CART = "c1c1c1c1-c1c1-c1c1-c1c1-c1c1c1c1c1c1"
METHOD = "11111111-2222-3333-4444-555555555555"
URL = f"/carts/{CART}/payments"


@pytest.fixture
def app():
    url = os.getenv(
        "TEST_DATABASE_URL", "postgresql+psycopg://shop:shop@localhost:55432/shop"
    )
    admin = create_engine(url)
    schema = "test_" + uuid4().hex
    with admin.begin() as conn:
        conn.exec_driver_sql(f"CREATE SCHEMA {schema}")
    engine = create_engine(
        url, connect_args={"options": f"-csearch_path={schema},public"}
    )
    with engine.begin() as conn:
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.attributes["connection"] = conn
        command.upgrade(config, "head")
        with Session(bind=conn) as session:
            seed_demo(session)
            session.flush()
    from sqlalchemy.engine import make_url

    test_url = make_url(url).update_query_dict(
        {"options": f"-csearch_path={schema},public"}
    )
    application = create_app(
        {
            "TESTING": True,
            "DATABASE_URL": test_url,
            "DEMO_API_TOKEN": "test-secret",
            "DEMO_BOB_API_TOKEN": "bob-secret",
            "DEMO_IDENTITIES": {},
            "PAYMENT_PROVIDER": Mock(
                wraps=MockProvider(sessionmaker(engine, expire_on_commit=False))
            ),
        }
    )
    yield application
    application.extensions["db"].dispose()
    engine.dispose()
    with admin.begin() as conn:
        conn.exec_driver_sql(f"DROP SCHEMA {schema} CASCADE")
    admin.dispose()


def post(app, key="attempt-1", method=METHOD, **kwargs):
    with app.test_client() as client:
        return client.post(
            URL,
            headers={"Authorization": "Bearer test-secret", "Idempotency-Key": key},
            json={"payment_method_id": method},
            **kwargs,
        )


def execute(app, sql):
    with app.extensions["db"].begin() as conn:
        return (
            conn.execute(text(sql)).all()
            if sql.startswith("SELECT")
            else conn.execute(text(sql))
        )


def test_success_and_replay(app):
    first = post(app)
    assert first.status_code == 201
    assert first.json["amount"] == "70.00"
    assert first.json["status"] == "succeeded"
    replay = post(app)
    assert replay.status_code == 200 and replay.json == first.json
    assert post(app, key="new").status_code == 409
    assert (
        execute(app, f"SELECT status FROM carts WHERE id='{CART}'")[0][0]
        == "checked_out"
    )
    assert execute(app, "SELECT count(*) FROM payments")[0][0] == 1
    app.config["PAYMENT_PROVIDER"].assert_called_once()
    assert "tok_" not in first.get_data(as_text=True)


def test_decline_can_start_new_attempt(app):
    execute(app, "UPDATE user_payment_methods SET provider_token='tok_test_decline'")
    assert post(app).status_code == 402
    assert post(app).status_code == 402
    assert app.config["PAYMENT_PROVIDER"].call_count == 1
    assert execute(app, f"SELECT status FROM carts WHERE id='{CART}'")[0][0] == "active"
    execute(app, "UPDATE user_payment_methods SET provider_token='tok_test_alice_visa'")
    assert post(app, key="retry").status_code == 201
    assert execute(app, "SELECT count(*) FROM payments")[0][0] == 2


def test_unknown_outcome_blocks_recharge(app):
    execute(app, "UPDATE user_payment_methods SET provider_token='tok_test_timeout'")
    first = post(app)
    assert first.status_code == 202 and first.json["status"] == "pending"
    assert post(app).json == first.json
    assert post(app, key="different").status_code == 409
    assert app.config["PAYMENT_PROVIDER"].call_count == 1
    assert execute(app, f"SELECT status FROM carts WHERE id='{CART}'")[0][0] == "active"


@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_requests_charge_once(app, same_key):
    entered, release = Event(), Event()

    def slow_provider(**kwargs):
        entered.set()
        assert release.wait(5)
        return app.config["PAYMENT_PROVIDER"]._mock_wraps(**kwargs)

    app.config["PAYMENT_PROVIDER"].side_effect = slow_provider
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(post, app)
        try:
            assert entered.wait(5)
            second = pool.submit(
                post, app, "attempt-1" if same_key else "other"
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
    execute(app, sql)
    assert post(app).status_code == code
    app.config["PAYMENT_PROVIDER"].assert_not_called()
    assert execute(app, "SELECT count(*) FROM payments")[0][0] == 0


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
    app.config["TOTAL_SERVICE"] = lambda _: (amount, currency)
    assert post(app).status_code == 502
    app.config["PAYMENT_PROVIDER"].assert_not_called()


def test_validation_and_authentication(app):
    with app.test_client() as client:
        assert client.post(URL).status_code == 401
        headers = {"Authorization": "Bearer test-secret", "Idempotency-Key": "k"}
        for body in [
            None,
            [],
            {},
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
    assert post(app, key="").status_code == 400
    assert post(app, key="x" * 129).status_code == 400
    assert post(app, method=str(uuid4())).status_code == 404
    app.config["PAYMENT_PROVIDER"].assert_not_called()


def test_key_cannot_change_method(app):
    assert post(app).status_code == 201
    assert post(app, method=str(uuid4())).status_code == 409
    assert app.config["PAYMENT_PROVIDER"].call_count == 1


def test_database_enforces_one_live_payment(app):
    assert post(app).status_code == 201
    with pytest.raises(IntegrityError):
        execute(
            app,
            """INSERT INTO payments (cart_id,payment_method_id,idempotency_key,amount,currency)
                    SELECT cart_id,payment_method_id,'other',amount,currency FROM payments""",
        )


def test_finalization_failure_preserves_pending_attempt(app):
    execute(
        app,
        """CREATE FUNCTION reject_checkout() RETURNS trigger LANGUAGE plpgsql AS $$
                 BEGIN RAISE EXCEPTION 'simulated database failure'; END; $$""",
    )
    execute(
        app,
        "CREATE TRIGGER fail_checkout BEFORE UPDATE ON carts FOR EACH ROW EXECUTE FUNCTION reject_checkout()",
    )
    assert post(app).status_code == 503
    assert execute(app, "SELECT status FROM payments")[0][0] == "pending"
    assert execute(app, f"SELECT status FROM carts WHERE id='{CART}'")[0][0] == "active"
    assert post(app).status_code == 202
    assert app.config["PAYMENT_PROVIDER"].call_count == 1


def test_payment_module_works_without_flask_context(app):
    from uuid import UUID

    from shop.adapters import ALICE
    from shop.payments import start_payment

    payment, created = start_payment(
        app.extensions["sessions"],
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
        execute(app, f"SELECT status FROM carts WHERE id='{CART}'")[0][0]
        == "checked_out"
    )
    # The HTTP entry point sees the same committed attempt.
    assert post(app, key="direct-call").json["id"] == str(payment.id)
    assert app.config["PAYMENT_PROVIDER"].call_count == 1


def test_migration_round_trip_and_metadata(app):
    from sqlalchemy import inspect

    with app.extensions["db"].begin() as connection:
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.attributes["connection"] = connection
        schema = connection.exec_driver_sql("SELECT current_schema()").scalar_one()
        command.check(config)
        command.upgrade(config, "head")  # Repeated upgrades are a no-op.
        command.downgrade(config, "001_base")
        assert "payments" not in inspect(connection).get_table_names(schema=schema)
        assert connection.execute(text("SELECT count(*) FROM users")).scalar_one() == 2
        command.upgrade(config, "head")
        command.check(config)
        command.downgrade(config, "base")
        assert set(inspect(connection).get_table_names(schema=schema)) == {
            "alembic_version"
        }
        command.upgrade(config, "head")
        command.check(config)


@pytest.mark.parametrize(
    "quote", [None, (), (Decimal("70"),), (Decimal("70"), "USD", "extra"), "70 USD"]
)
def test_malformed_quote_is_dependency_error(app, quote):
    app.config["TOTAL_SERVICE"] = lambda _: quote
    response = post(app)
    assert response.status_code == 502
    assert response.json["error"] == "Total service returned an invalid quote"
    app.config["PAYMENT_PROVIDER"].assert_not_called()


def test_provider_receives_original_parameters(app):
    response = post(app)
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
        ("succeeded", None, None),
        ("failed", None, "secret-token"),
        ("pending", None, None),
    ],
)
def test_invalid_provider_results_remain_pending_but_report_adapter_error(
    app, result, caplog
):
    app.config["PAYMENT_PROVIDER"].side_effect = lambda **_: result
    response = post(app)
    assert response.status_code == 502
    assert execute(app, "SELECT status FROM payments")[0][0] == "pending"
    assert "Invalid provider response" in caplog.text
    assert "secret-token" not in caplog.text + response.get_data(as_text=True)
    assert post(app).status_code == 202
    assert app.config["PAYMENT_PROVIDER"].call_count == 1


def test_provider_bug_is_distinct_from_timeout_and_does_not_leak_payload(app, caplog):
    app.config["PAYMENT_PROVIDER"].side_effect = AttributeError("secret-token")
    response = post(app)
    assert response.status_code == 502
    assert "AttributeError" in caplog.text
    assert "secret-token" not in caplog.text + response.get_data(as_text=True)
    assert execute(app, "SELECT status FROM payments")[0][0] == "pending"


def test_two_authenticated_users_own_their_carts_and_methods(app):
    from shop.adapters import BOB_CART

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
    assert post(app, method=bob_method).status_code == 404
    assert post(app).status_code == 201
    assert execute(app, "SELECT count(*) FROM mock_charges")[0][0] == 2


def test_injected_identity_and_quote_support_another_cart(app):
    from uuid import UUID

    from shop.adapters import ALICE

    new_cart = uuid4()
    with app.extensions["sessions"].begin() as session:
        from shop.models import Cart, CartItem

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
        (Decimal("45"), "USD") if cart_id == new_cart else None
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


def test_timeout_recovery_reuses_snapshot_after_saved_method_changes(app):
    execute(app, "UPDATE user_payment_methods SET provider_token='tok_test_timeout'")
    response = post(app)
    assert response.status_code == 202
    assert execute(app, "SELECT count(*) FROM mock_charges")[0][0] == 1
    execute(app, "UPDATE user_payment_methods SET provider_token='tok_test_decline'")
    runner = app.test_cli_runner()
    result = runner.invoke(args=["reconcile-payment", response.json["id"]])
    assert result.exit_code == 0 and "succeeded" in result.output
    assert (
        app.config["PAYMENT_PROVIDER"].call_args.kwargs["token"] == "tok_test_timeout"
    )
    assert runner.invoke(args=["reconcile-payment", response.json["id"]]).exit_code == 0
    assert app.config["PAYMENT_PROVIDER"].call_count == 2
    assert execute(app, "SELECT count(*) FROM mock_charges")[0][0] == 1
    assert post(app).json["status"] == "succeeded"


def test_recovery_after_crash_before_provider_call(app):
    app.config["PAYMENT_PROVIDER"].side_effect = SystemExit
    with pytest.raises(SystemExit):
        post(app)
    payment_id = str(execute(app, "SELECT id FROM payments")[0][0])
    assert execute(app, "SELECT count(*) FROM mock_charges")[0][0] == 0
    app.config["PAYMENT_PROVIDER"].side_effect = None
    result = app.test_cli_runner().invoke(args=["reconcile-payment", payment_id])
    assert result.exit_code == 0
    assert execute(app, "SELECT count(*) FROM mock_charges")[0][0] == 1
    assert post(app).json["status"] == "succeeded"


def test_recovery_after_database_finalization_failure(app):
    execute(
        app,
        """CREATE FUNCTION reject_checkout() RETURNS trigger LANGUAGE plpgsql AS $$
                 BEGIN RAISE EXCEPTION 'simulated database failure'; END; $$""",
    )
    execute(
        app,
        "CREATE TRIGGER fail_checkout BEFORE UPDATE ON carts FOR EACH ROW EXECUTE FUNCTION reject_checkout()",
    )
    assert post(app).status_code == 503
    payment_id = str(execute(app, "SELECT id FROM payments")[0][0])
    execute(app, "DROP TRIGGER fail_checkout ON carts")
    assert (
        app.test_cli_runner().invoke(args=["reconcile-payment", payment_id]).exit_code
        == 0
    )
    assert execute(app, "SELECT count(*) FROM mock_charges")[0][0] == 1
    assert post(app).json["status"] == "succeeded"


def test_concurrent_recovery_does_not_duplicate_charge(app):
    from uuid import UUID

    from shop.payments import reconcile_payment

    app.config["PAYMENT_PROVIDER"].side_effect = SystemExit
    with pytest.raises(SystemExit):
        post(app)
    payment_id = execute(app, "SELECT id FROM payments")[0][0]
    app.config["PAYMENT_PROVIDER"].side_effect = None
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = [
            pool.submit(
                reconcile_payment,
                app.extensions["sessions"],
                UUID(str(payment_id)),
                app.config["PAYMENT_PROVIDER"],
            )
            for _ in range(4)
        ]
        assert all(job.result(5).status == "succeeded" for job in jobs)
    assert execute(app, "SELECT count(*) FROM mock_charges")[0][0] == 1


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
    execute(app, "UPDATE user_payment_methods SET provider_token='tok_test_timeout'")
    assert post(app).status_code == 202
    with pytest.raises(IntegrityError):
        execute(app, sql)
    assert (
        execute(app, f"SELECT sum(quantity) FROM cart_items WHERE cart_id='{CART}'")[0][
            0
        ]
        == 3
    )


def test_decline_allows_cart_edits_but_paid_cart_is_frozen(app):
    execute(app, "UPDATE user_payment_methods SET provider_token='tok_test_decline'")
    assert post(app).status_code == 402
    execute(app, f"UPDATE cart_items SET quantity=quantity+1 WHERE cart_id='{CART}'")
    # The total is supplied by the existing service for the new cart contents.
    app.config["TOTAL_SERVICE"] = lambda _: (Decimal("127.50"), "USD")
    execute(app, "UPDATE user_payment_methods SET provider_token='tok_test_alice_visa'")
    assert post(app, key="new").status_code == 201
    with pytest.raises(IntegrityError):
        execute(app, f"DELETE FROM cart_items WHERE cart_id='{CART}'")


def test_simultaneous_payment_creation_is_serialized(app):
    from threading import Barrier

    ready = Barrier(4)

    def request_payment():
        ready.wait(5)
        return post(app)

    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: request_payment(), range(4)))
    assert sum(response.status_code == 201 for response in responses) == 1
    assert all(response.status_code in (200, 201, 202) for response in responses)
    assert len({response.json["id"] for response in responses}) == 1
    assert app.config["PAYMENT_PROVIDER"].call_count == 1
    assert execute(app, "SELECT count(*) FROM mock_charges")[0][0] == 1


def test_cart_writer_waiting_on_quote_cannot_change_reserved_cart(app):
    quoting, release, writer_started = Event(), Event(), Event()

    def slow_quote(_):
        quoting.set()
        assert release.wait(5)
        return Decimal("70.00"), "USD"

    def change_item():
        writer_started.set()
        execute(app, f"UPDATE cart_items SET quantity=10 WHERE cart_id='{CART}'")

    app.config["TOTAL_SERVICE"] = slow_quote
    with ThreadPoolExecutor(max_workers=2) as pool:
        payment = pool.submit(post, app)
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
        execute(app, f"SELECT sum(quantity) FROM cart_items WHERE cart_id='{CART}'")[0][
            0
        ]
        == 3
    )


def test_recovery_survives_new_provider_instance_and_rejects_changed_parameters(app):
    execute(app, "UPDATE user_payment_methods SET provider_token='tok_test_timeout'")
    response = post(app)
    app.config["PAYMENT_PROVIDER"] = MockProvider(app.extensions["sessions"])
    assert (
        app.test_cli_runner()
        .invoke(args=["reconcile-payment", response.json["id"]])
        .exit_code
        == 0
    )
    with pytest.raises(ValueError, match="parameters changed"):
        app.config["PAYMENT_PROVIDER"](
            token="another-token",
            amount=Decimal("70"),
            currency="USD",
            idempotency_key=response.json["id"],
        )
    assert execute(app, "SELECT count(*) FROM mock_charges")[0][0] == 1


def test_unavailable_recovery_stays_pending_and_exits_unsuccessfully(app):
    app.config["PAYMENT_PROVIDER"].side_effect = TimeoutError
    response = post(app)
    result = app.test_cli_runner().invoke(
        args=["reconcile-payment", response.json["id"]]
    )
    assert result.exit_code != 0 and "still pending" in result.output
    assert post(app).json["status"] == "pending"
    assert post(app, key="new").status_code == 409


def test_legacy_attempt_is_not_recharged_with_current_card(app):
    execute(app, "UPDATE user_payment_methods SET provider_token='tok_test_timeout'")
    response = post(app)
    execute(app, "UPDATE payments SET provider_token=NULL")
    result = app.test_cli_runner().invoke(
        args=["reconcile-payment", response.json["id"]]
    )
    assert result.exit_code != 0 and "original token unavailable" in result.output
    assert app.config["PAYMENT_PROVIDER"].call_count == 1
