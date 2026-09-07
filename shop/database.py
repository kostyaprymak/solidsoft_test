"""Database setup and explicit demo-data loading."""

from uuid import UUID

import click
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .payments import PaymentError, reconcile_payment
from .seed import seed_demo


def init_app(app: Flask) -> None:
    engine = create_engine(
        app.config["DATABASE_URL"], pool_pre_ping=True, hide_parameters=True
    )
    app.extensions["db"] = engine
    # Results remain readable after the short transaction's session closes.
    app.extensions["sessions"] = sessionmaker(engine, expire_on_commit=False)

    @app.cli.command("seed-db")
    def seed_db():
        """Insert sample data once, after applying migrations."""
        with app.extensions["sessions"].begin() as session:
            seed_demo(session)
        click.echo("Sample data installed.")

    @app.cli.command("reconcile-payment")
    @click.argument("payment_id", type=click.UUID)
    def reconcile(payment_id: UUID):
        """Recover one pending attempt using the original provider idempotency key."""
        try:
            payment = reconcile_payment(
                app.extensions["sessions"], payment_id, app.config["PAYMENT_PROVIDER"]
            )
        except PaymentError as error:
            raise click.ClickException(str(error)) from error
        if payment.status == "pending":
            raise click.ClickException(
                f"{payment.id}: still pending; retry reconciliation later"
            )
        click.echo(f"{payment.id}: {payment.status}")
