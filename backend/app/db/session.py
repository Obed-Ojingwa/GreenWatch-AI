from collections.abc import Iterator
from urllib.parse import urlsplit

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


def normalize_database_url(url: str) -> str:
    """Supabase/Render hand out `postgres://` or `postgresql://`; SQLAlchemy would then pick the
    psycopg2 driver, which is not installed. We use psycopg 3."""
    if urlsplit(url).scheme.lower() == "https":
        raise ValueError(
            "DATABASE_URL must be a PostgreSQL connection string, not the Supabase HTTPS project URL. "
            "Use the database connection string for DATABASE_URL and keep the HTTPS project URL in SUPABASE_URL."
        )
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def _make_engine():
    url = normalize_database_url(get_settings().database_url)
    kwargs: dict = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs = {"connect_args": {"check_same_thread": False}}
    elif url.startswith("postgresql"):
        # Supabase's transaction pooler (port 6543) cannot reuse server-side prepared statements.
        kwargs["connect_args"] = {"prepare_threshold": None}
    return create_engine(url, **kwargs)


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_dev_db() -> None:
    """Create tables for local/SQLite use. Production applies schema.sql in Supabase instead."""
    from app.db import models  # noqa: F401

    Base.metadata.create_all(engine)

