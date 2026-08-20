"""SQLAlchemy engine / session."""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine():
    settings = get_settings()
    url = settings.database_url
    connect_args = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, connect_args=connect_args, future=True)

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragma(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    # Import models so metadata is populated
    from app import models  # noqa: F401
    from sqlalchemy import inspect, text

    Base.metadata.create_all(bind=engine)

    # Lightweight SQLite column add for existing dev DBs
    if str(engine.url).startswith("sqlite"):
        with engine.begin() as conn:
            cols = {c["name"] for c in inspect(conn).get_columns("users")}
            if "photos_allowed" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE users ADD COLUMN photos_allowed BOOLEAN "
                        "DEFAULT 0 NOT NULL"
                    )
                )
            if "email_verified" not in cols:
                # Grandfather existing rows so local/admin accounts keep working
                conn.execute(
                    text(
                        "ALTER TABLE users ADD COLUMN email_verified BOOLEAN "
                        "DEFAULT 1 NOT NULL"
                    )
                )
