"""HTTP validation, demo authentication and payment response serialization."""

import re
from uuid import UUID

from flask import Blueprint, abort, current_app, request

from .contracts import IdentityResolver
from .models import Payment
from .payments import start_payment

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
def pay(cart_id: UUID):
    resolver: IdentityResolver = current_app.config["IDENTITY_RESOLVER"]
    user_id = resolver(request.headers.get("Authorization", ""))
    if not isinstance(user_id, UUID):
        abort(401, "Valid bearer token required")
    key = request.headers.get("Idempotency-Key", "")
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", key):
        abort(400, "Idempotency-Key must contain 1-128 letters, digits, ., _, : or -")
    body = request.get_json()
    if not isinstance(body, dict) or set(body) != {"payment_method_id"}:
        abort(400, "Provide only payment_method_id in a JSON object")
    try:
        method_id = UUID(body["payment_method_id"])
    except (ValueError, TypeError, AttributeError):
        abort(400, "payment_method_id must be a UUID string")
    payment, created = start_payment(
        current_app.extensions["sessions"],
        user_id=user_id,
        cart_id=cart_id,
        method_id=method_id,
        key=key,
        total_service=current_app.config["TOTAL_SERVICE"],
        provider=current_app.config["PAYMENT_PROVIDER"],
    )
    return payment_response(payment, created)
