from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import UUID

import pytest
from conftest import charge_count, execute_sql, post_payment, query_rows, run_demo_cli

from shop.payments.demo.adapters import MockProvider
from shop.payments.service import reconcile_payment


def test_timeout_recovery_reuses_snapshot_after_saved_method_changes(app):
    execute_sql(
        app, "UPDATE user_payment_methods SET provider_token='tok_test_timeout'"
    )
    response = post_payment(app)
    assert response.status_code == 202
    assert charge_count(app) == 1
    execute_sql(
        app, "UPDATE user_payment_methods SET provider_token='tok_test_decline'"
    )
    runner = app.test_cli_runner()
    result = runner.invoke(args=["reconcile-payment", response.json["id"]])
    assert result.exit_code == 0 and "succeeded" in result.output
    assert (
        app.config["PAYMENT_PROVIDER"].call_args.kwargs["token"] == "tok_test_timeout"
    )
    assert runner.invoke(args=["reconcile-payment", response.json["id"]]).exit_code == 0
    assert app.config["PAYMENT_PROVIDER"].call_count == 2
    assert charge_count(app) == 1
    assert post_payment(app).json["status"] == "succeeded"


def test_recovery_after_crash_before_provider_call(app):
    app.config["PAYMENT_PROVIDER"].side_effect = SystemExit
    with pytest.raises(SystemExit):
        post_payment(app)
    payment_id = str(query_rows(app, "SELECT id FROM payments")[0][0])
    assert charge_count(app) == 0
    app.config["PAYMENT_PROVIDER"].side_effect = None
    result = app.test_cli_runner().invoke(args=["reconcile-payment", payment_id])
    assert result.exit_code == 0
    assert charge_count(app) == 1
    assert post_payment(app).json["status"] == "succeeded"


def test_recovery_after_database_finalization_failure(app):
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
    payment_id = str(query_rows(app, "SELECT id FROM payments")[0][0])
    execute_sql(app, "DROP TRIGGER fail_checkout ON carts")
    assert (
        app.test_cli_runner().invoke(args=["reconcile-payment", payment_id]).exit_code
        == 0
    )
    assert charge_count(app) == 1
    assert post_payment(app).json["status"] == "succeeded"


def test_concurrent_recovery_does_not_duplicate_charge(app):
    app.config["PAYMENT_PROVIDER"].side_effect = SystemExit
    with pytest.raises(SystemExit):
        post_payment(app)
    payment_id = query_rows(app, "SELECT id FROM payments")[0][0]
    app.config["PAYMENT_PROVIDER"].side_effect = None
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = [
            pool.submit(
                reconcile_payment,
                app.extensions["payment_repository"],
                UUID(str(payment_id)),
                app.config["PAYMENT_PROVIDER"],
            )
            for _ in range(4)
        ]
        assert all(job.result(5).status == "succeeded" for job in jobs)
    assert charge_count(app) == 1


def test_recovery_survives_separate_cli_process_without_duplicate_charge(app):
    execute_sql(
        app, "UPDATE user_payment_methods SET provider_token='tok_test_timeout'"
    )
    app.config["PAYMENT_PROVIDER"] = MockProvider(app.extensions["sessions"])
    response = post_payment(app)
    assert response.status_code == 202

    first = run_demo_cli(app, "reconcile-payment", response.json["id"])
    second = run_demo_cli(app, "reconcile-payment", response.json["id"])

    assert first.returncode == 0 and "succeeded" in first.stdout
    assert second.returncode == 0 and "succeeded" in second.stdout
    assert charge_count(app) == 1
    with pytest.raises(ValueError, match="parameters changed"):
        app.config["PAYMENT_PROVIDER"](
            token="another-token",
            amount=Decimal("70"),
            currency="USD",
            idempotency_key=response.json["id"],
        )


def test_unavailable_recovery_stays_pending_and_exits_unsuccessfully(app):
    app.config["PAYMENT_PROVIDER"].side_effect = TimeoutError
    response = post_payment(app)
    result = app.test_cli_runner().invoke(
        args=["reconcile-payment", response.json["id"]]
    )
    assert result.exit_code != 0 and "still pending" in result.output
    assert post_payment(app).json["status"] == "pending"
    assert post_payment(app, key="new").status_code == 409


def test_legacy_attempt_is_not_recharged_with_current_card(app):
    execute_sql(
        app, "UPDATE user_payment_methods SET provider_token='tok_test_timeout'"
    )
    response = post_payment(app)
    execute_sql(app, "UPDATE payments SET provider_token=NULL")
    result = app.test_cli_runner().invoke(
        args=["reconcile-payment", response.json["id"]]
    )
    assert result.exit_code != 0 and "original token unavailable" in result.output
    assert app.config["PAYMENT_PROVIDER"].call_count == 1
