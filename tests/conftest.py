import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from shop.app import create_app
from shop.payments.demo.adapters import (
    ALICE,
    DemoIdentityResolver,
    DemoTotalService,
    MockProvider,
)
from shop.payments.demo.seed import DEMO_QUOTES, seed_demo

CART = "c1c1c1c1-c1c1-c1c1-c1c1-c1c1c1c1c1c1"
METHOD = "11111111-2222-3333-4444-555555555555"
URL = f"/carts/{CART}/payments"


@pytest.fixture
def app():
    url = os.getenv(
        "TEST_DATABASE_URL", "postgresql+psycopg://shop:shop@localhost:55432/shop"
    )
    admin = create_engine(url)
    schema = "test_" + uuid4().hex
    with admin.begin() as conn:
        conn.exec_driver_sql(f"CREATE SCHEMA {schema}")
    engine = create_engine(
        url, connect_args={"options": f"-csearch_path={schema},public"}
    )
    with engine.begin() as conn:
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.attributes["connection"] = conn
        command.upgrade(config, "head")
        with Session(bind=conn) as session:
            seed_demo(session)
            session.flush()

    test_url = make_url(url).update_query_dict(
        {"options": f"-csearch_path={schema},public"}
    )
    application = create_app(
        {
            "TESTING": True,
            "DATABASE_URL": test_url,
            "IDENTITY_RESOLVER": DemoIdentityResolver(
                {
                    "test-secret": str(ALICE),
                    "bob-secret": "22222222-2222-2222-2222-222222222222",
                }
            ),
            "TOTAL_SERVICE": DemoTotalService(DEMO_QUOTES),
            "PAYMENT_PROVIDER": Mock(wraps=MockProvider(sessionmaker(engine))),
        }
    )
    yield application
    application.extensions["db"].dispose()
    engine.dispose()
    with admin.begin() as conn:
        conn.exec_driver_sql(f"DROP SCHEMA {schema} CASCADE")
    admin.dispose()


def post_payment(app, key="attempt-1", method=METHOD, **kwargs):
    with app.test_client() as client:
        return client.post(
            URL,
            headers={"Authorization": "Bearer test-secret", "Idempotency-Key": key},
            json={"payment_method_id": method},
            **kwargs,
        )


def query_rows(app, sql):
    with app.extensions["db"].begin() as conn:
        return conn.execute(text(sql)).all()


def execute_sql(app, sql):
    with app.extensions["db"].begin() as conn:
        return conn.execute(text(sql))


def charge_count(app):
    return query_rows(app, "SELECT count(*) FROM mock_charges")[0][0]


def run_demo_cli(app, *args):
    database_url = app.config["DATABASE_URL"]
    env = os.environ | {
        "APP_CONFIG": "demo",
        "DATABASE_URL": (
            database_url.render_as_string(hide_password=False)
            if hasattr(database_url, "render_as_string")
            else database_url
        ),
    }
    return subprocess.run(
        [sys.executable, "-m", "flask", "--app", "app", *args],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
