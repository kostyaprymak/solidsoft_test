"""Alembic configuration for CLI commands and connection-sharing tests."""

import os

from alembic import context
from sqlalchemy import create_engine, pool

from shop.models import Base

config = context.config
url = os.getenv("DATABASE_URL", config.get_main_option("sqlalchemy.url"))


def run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        # Keep test schemas independent from a public alembic_version table.
        version_table_schema=connection.exec_driver_sql(
            "SELECT current_schema()"
        ).scalar_one(),
        include_name=lambda name, type_, parents: (
            not (type_ == "table" and name == "alembic_version")
        ),
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(url=url, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    connection = config.attributes.get("connection")
    if connection is not None:
        run_migrations(connection)
    else:
        engine = create_engine(url, poolclass=pool.NullPool)
        with engine.begin() as connection:
            run_migrations(connection)
        engine.dispose()
