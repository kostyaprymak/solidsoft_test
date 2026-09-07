"""Application factory: configuration and module registration."""

import json
import os

from flask import Flask
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import HTTPException

from . import database
from .adapters import (
    ALICE,
    BOB,
    BOB_CART,
    DEMO_CART,
    DemoIdentityResolver,
    DemoTotalService,
    MockProvider,
)
from .payments import PaymentError
from .routes import blueprint


def create_app(config=None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(
        DATABASE_URL=os.getenv(
            "DATABASE_URL", "postgresql+psycopg://shop:shop@localhost:55432/shop"
        ),
        DEMO_API_TOKEN=os.getenv("DEMO_API_TOKEN"),
        DEMO_BOB_API_TOKEN=os.getenv("DEMO_BOB_API_TOKEN"),
        DEMO_IDENTITIES=json.loads(os.getenv("DEMO_IDENTITIES_JSON", "{}")),
        DEMO_QUOTES=json.loads(
            os.getenv(
                "DEMO_QUOTES_JSON",
                json.dumps(
                    {
                        str(DEMO_CART): ["70.00", "USD"],
                        str(BOB_CART): ["89.99", "USD"],
                    }
                ),
            )
        ),
        MAX_CONTENT_LENGTH=4096,
    )
    app.config.update(config or {})
    database.init_app(app)
    identities = dict(app.config["DEMO_IDENTITIES"])
    for name, user in [("DEMO_API_TOKEN", ALICE), ("DEMO_BOB_API_TOKEN", BOB)]:
        if app.config.get(name):
            identities[app.config[name]] = str(user)
    app.config.setdefault("IDENTITY_RESOLVER", DemoIdentityResolver(identities))
    app.config.setdefault("TOTAL_SERVICE", DemoTotalService(app.config["DEMO_QUOTES"]))
    app.config.setdefault("PAYMENT_PROVIDER", MockProvider(app.extensions["sessions"]))
    app.register_blueprint(blueprint)

    @app.errorhandler(HTTPException)
    def http_error(error):
        response = error.get_response()
        response.data = app.json.dumps({"error": error.description})
        response.content_type = "application/json"
        return response

    @app.errorhandler(PaymentError)
    def payment_error(error):
        statuses = {
            "not_found": 404,
            "conflict": 409,
            "invalid_quote": 502,
            "provider_error": 502,
            "unavailable": 503,
        }
        return {"error": str(error)}, statuses[error.code]

    @app.errorhandler(SQLAlchemyError)
    def database_error(error):
        app.logger.error("Database operation failed (%s)", type(error).__name__)
        return {
            "error": "Database unavailable; retry with the same idempotency key"
        }, 503

    return app
