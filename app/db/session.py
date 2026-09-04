"""SQLAlchemy engine and session configuration."""

from __future__ import annotations

import os

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


def normalize_database_url(url: str) -> str:
    """Select Psycopg 3 explicitly for PostgreSQL connection strings."""

    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url.removeprefix("postgres://")
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return url


def create_database_engine(database_url: str | None = None) -> Engine:
    """Create an engine from an explicit URL or DATABASE_URL."""

    resolved_url = database_url or os.getenv("DATABASE_URL")
    if not resolved_url:
        raise ValueError("DATABASE_URL is required")
    normalized_url = normalize_database_url(resolved_url)
    kwargs: dict[str, object] = {"pool_pre_ping": True}
    if normalized_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs.update({"pool_size": 5, "max_overflow": 2})
    return create_engine(normalized_url, **kwargs)


def create_session_factory(
    database_url: str | None = None,
) -> sessionmaker[Session]:
    """Create the application's short-lived database session factory."""

    engine = create_database_engine(database_url)
    return sessionmaker(bind=engine, expire_on_commit=False)
