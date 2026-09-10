from uuid import UUID

import click
from flask import Flask

from shop.payments.errors import PaymentError
from shop.payments.repository import PaymentRepository
from shop.payments.routes import blueprint
from shop.payments.service import reconcile_payment


def init_app(app: Flask) -> None:
    app.extensions["payment_repository"] = PaymentRepository(app.extensions["sessions"])
    app.register_blueprint(blueprint)

    @app.cli.command("reconcile-payment")
    @click.argument("payment_id", type=click.UUID)
    def reconcile(payment_id: UUID):
        try:
            payment = reconcile_payment(
                app.extensions["payment_repository"],
                payment_id,
                app.config["PAYMENT_PROVIDER"],
            )
        except PaymentError as error:
            raise click.ClickException(str(error)) from error
        if payment.status == "pending":
            raise click.ClickException(
                f"{payment.id}: still pending; retry reconciliation later"
            )
        click.echo(f"{payment.id}: {payment.status}")
