from werkzeug.exceptions import (
    BadGateway,
    BadRequest,
    Conflict,
    NotFound,
    ServiceUnavailable,
)

from shop.payments.errors import (
    DependencyUnavailable,
    InvalidProviderResponse,
    InvalidQuote,
    PaymentConflict,
    PaymentError,
    PaymentNotFound,
    ProviderFailure,
    TotalServiceFailure,
)


class InvalidPaymentRequest(BadRequest):
    description = "Provide only a valid payment_method_id UUID in a JSON object"


class InvalidIdempotencyKey(BadRequest):
    description = "Idempotency-Key must contain 1-128 letters, digits, ., _, : or -"


def to_http_error(error: PaymentError):
    classes = {
        PaymentNotFound: NotFound,
        PaymentConflict: Conflict,
        InvalidQuote: BadGateway,
        TotalServiceFailure: BadGateway,
        InvalidProviderResponse: BadGateway,
        ProviderFailure: BadGateway,
        DependencyUnavailable: ServiceUnavailable,
    }
    http_error = classes[type(error)](description=str(error))
    http_error.payment_id = error.payment_id
    http_error.domain_error = type(error).__name__
    http_error.__cause__ = error.__cause__
    return http_error
