"""Shared database setup."""

from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def init_app(app: Flask) -> None:
    engine = create_engine(
        app.config["DATABASE_URL"], pool_pre_ping=True, hide_parameters=True
    )
    app.extensions["db"] = engine
    app.extensions["sessions"] = sessionmaker(engine, expire_on_commit=False)
