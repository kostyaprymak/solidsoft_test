"""Application factory: configuration and module registration."""

import os
import traceback

from flask import Flask
from sqlalchemy.exc import InterfaceError, OperationalError, SQLAlchemyError
from werkzeug.exceptions import HTTPException

from shop import database
from shop.config import load
from shop.payments import extension as payments_extension
from shop.payments.demo.adapters import MockProvider
from shop.payments.errors import PaymentError
from shop.payments.http_errors import to_http_error

REQUIRED_ADAPTERS = ("IDENTITY_RESOLVER", "TOTAL_SERVICE", "PAYMENT_PROVIDER")


def create_app(config=None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(MAX_CONTENT_LENGTH=4096)
    app.config.update(load(os.getenv("APP_CONFIG", "production")))
    app.config.update(config or {})
    missing = [
        name
        for name in ("DATABASE_URL", *REQUIRED_ADAPTERS)
        if not app.config.get(name)
    ]
    if missing:
        raise RuntimeError(f"Missing required configuration: {', '.join(missing)}")
    invalid = [name for name in REQUIRED_ADAPTERS if not callable(app.config[name])]
    if invalid:
        raise RuntimeError(f"Configuration must be callable: {', '.join(invalid)}")
    database.init_app(app)
    if app.config["PAYMENT_PROVIDER"] is MockProvider:
        app.config["PAYMENT_PROVIDER"] = MockProvider(app.extensions["sessions"])
    payments_extension.init_app(app)
    if loader := app.config.get("DATA_LOADER"):
        with app.extensions["sessions"].begin() as session:
            loader(session)

    @app.errorhandler(PaymentError)
    def payment_error(error):
        return http_error(to_http_error(error))

    @app.errorhandler(HTTPException)
    def http_error(error):
        if error.code >= 500:
            cause = error.__cause__
            app.logger.error(
                "HTTP failure status=%s type=%s cause=%s payment=%s traceback=%s",
                error.code,
                getattr(error, "domain_error", type(error).__name__),
                type(cause).__name__ if cause else None,
                getattr(error, "payment_id", None),
                traceback.extract_tb(cause.__traceback__) if cause else None,
            )
        response = error.get_response()
        response.data = app.json.dumps({"error": error.description})
        response.content_type = "application/json"
        return response

    @app.errorhandler(SQLAlchemyError)
    def database_error(error):
        app.logger.exception("Database operation failed (%s)", type(error).__name__)
        if isinstance(error, (OperationalError, InterfaceError)):
            return {
                "error": "Database unavailable; retry with the same idempotency key"
            }, 503
        return {"error": "Database operation failed"}, 500

    return app
