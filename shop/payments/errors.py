class PaymentError(Exception):
    def __init__(self, message: str, *, payment_id=None):
        super().__init__(message)
        self.payment_id = payment_id


class PaymentNotFound(PaymentError):
    pass


class PaymentConflict(PaymentError):
    pass


class InvalidQuote(PaymentError):
    pass


class TotalServiceFailure(PaymentError):
    pass


class InvalidProviderResponse(PaymentError):
    pass


class ProviderFailure(PaymentError):
    pass


class DependencyUnavailable(PaymentError):
    pass
