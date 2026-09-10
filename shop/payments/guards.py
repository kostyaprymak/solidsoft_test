import re
from functools import wraps

from flask import g, request

from shop.payments.http_errors import InvalidIdempotencyKey


def idempotency_key_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        key = request.headers.get("Idempotency-Key", "")
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", key):
            raise InvalidIdempotencyKey
        g.idempotency_key = key
        return view(*args, **kwargs)

    return wrapped
