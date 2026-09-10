"""HTTP validation, authentication and payment response serialization."""

from uuid import UUID

from flask import Blueprint, current_app, g, request
from pydantic import ValidationError

from shop.guards import login_required
from shop.payments.entities import Payment
from shop.payments.guards import idempotency_key_required
from shop.payments.http_errors import InvalidPaymentRequest
from shop.payments.schemas import PaymentRequest
from shop.payments.service import start_payment

blueprint = Blueprint("payments", __name__)


def payment_response(payment: Payment, created: bool):
    body = {
        key: str(getattr(payment, key))
        for key in (
            "id",
            "cart_id",
            "payment_method_id",
            "amount",
            "currency",
            "status",
        )
    }
    body["failure_code"] = payment.failure_code
    status = {"pending": 202, "failed": 402, "succeeded": 201 if created else 200}
    return body, status[payment.status]


@blueprint.post("/carts/<uuid:cart_id>/payments")
@login_required
@idempotency_key_required
def pay(cart_id: UUID):
    body = request.get_json()
    try:
        payload = PaymentRequest.model_validate(body)
    except ValidationError as error:
        raise InvalidPaymentRequest from error
    payment, created = start_payment(
        current_app.extensions["payment_repository"],
        user_id=g.user_id,
        cart_id=cart_id,
        method_id=payload.payment_method_id,
        key=g.idempotency_key,
        total_service=current_app.config["TOTAL_SERVICE"],
        provider=current_app.config["PAYMENT_PROVIDER"],
    )
    return payment_response(payment, created)
