from functools import wraps
from uuid import UUID

from flask import current_app, g, request

from shop.errors import AuthenticationRequired


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user_id = current_app.config["IDENTITY_RESOLVER"](
            request.headers.get("Authorization", "")
        )
        if not isinstance(user_id, UUID):
            raise AuthenticationRequired
        g.user_id = user_id
        return view(*args, **kwargs)

    return wrapped
