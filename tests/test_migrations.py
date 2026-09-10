import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def test_mock_provider_migration_isolated_from_fallback_schema():
    url = os.getenv(
        "TEST_DATABASE_URL", "postgresql+psycopg://shop:shop@localhost:55432/shop"
    )
    target = "target_" + uuid4().hex
    fallback = "fallback_" + uuid4().hex
    engine = create_engine(url)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{target}"')
            connection.exec_driver_sql(f'CREATE SCHEMA "{fallback}"')
            connection.exec_driver_sql(
                f'CREATE TABLE "{fallback}".mock_charges (sentinel text)'
            )
            connection.exec_driver_sql(
                f"INSERT INTO \"{fallback}\".mock_charges VALUES ('keep-me')"
            )
            connection.exec_driver_sql(
                f'SET LOCAL search_path = "{target}", "{fallback}", public'
            )
            config.attributes["connection"] = connection
            command.upgrade(config, "003_recovery")
            command.upgrade(config, "004_mock_provider")
            assert inspect(connection).has_table("mock_charges", schema=target)
            command.downgrade(config, "003_recovery")
            assert not inspect(connection).has_table("mock_charges", schema=target)
            assert (
                connection.execute(
                    text(f'SELECT sentinel FROM "{fallback}".mock_charges')
                ).scalar_one()
                == "keep-me"
            )
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{target}" CASCADE')
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{fallback}" CASCADE')
        engine.dispose()


def test_migration_round_trip_and_metadata(app):
    with app.extensions["db"].begin() as connection:
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.attributes["connection"] = connection
        schema = connection.exec_driver_sql("SELECT current_schema()").scalar_one()
        command.check(config)
        command.upgrade(config, "head")  # Repeated upgrades are a no-op.
        command.downgrade(config, "001_base")
        assert "payments" not in inspect(connection).get_table_names(schema=schema)
        assert connection.execute(text("SELECT count(*) FROM users")).scalar_one() == 2
        command.upgrade(config, "head")
        command.check(config)
        command.downgrade(config, "base")
        assert set(inspect(connection).get_table_names(schema=schema)) == {
            "alembic_version"
        }
        command.upgrade(config, "head")
        command.check(config)
