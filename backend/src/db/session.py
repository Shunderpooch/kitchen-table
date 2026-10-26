"""Database session and engine management."""

import os
from pathlib import Path
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base


# Default database path
DEFAULT_DB_PATH = Path(__file__).parent.parent.parent.parent / "data" / "expenses.db"


def get_engine(db_path: Path | str | None = None):
    """Create and return a SQLAlchemy engine.

    Args:
        db_path: Path to the SQLite database file. Defaults to ../data/expenses.db
    """
    if db_path is None:
        db_path = os.environ.get("EXPENSES_DB_PATH", str(DEFAULT_DB_PATH))

    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Wait for a long-running writer (e.g. text indexing) instead of failing after 5 s.
    engine = create_engine(f"sqlite:///{db_path}", echo=False, connect_args={"timeout": 60})
    migrate_member_columns(engine)
    return engine


def migrate_member_columns(engine) -> None:
    """Databases from before household configuration kept each person's share in
    "<firstname>_spend"; rename those to member_a_spend / member_b_spend by the
    order of HOUSEHOLD_MEMBERS."""
    from ..household import members

    with engine.begin() as connection:
        columns = {r[1] for r in connection.exec_driver_sql("PRAGMA table_info(expenses)")}
        if not columns or "member_a_spend" in columns:
            return
        for slot, name in zip(("member_a_spend", "member_b_spend"), members()):
            legacy = f"{name.lower()}_spend"
            if legacy in columns:
                try:
                    connection.exec_driver_sql(f'ALTER TABLE expenses RENAME COLUMN "{legacy}" TO {slot}')
                except Exception:  # another process (the app, a task) renamed it first
                    pass


def get_session(db_path: Path | str | None = None) -> sessionmaker[Session]:
    """Create and return a session factory.

    Args:
        db_path: Path to the SQLite database file.
    """
    engine = get_engine(db_path)
    return sessionmaker(bind=engine)


def init_db(db_path: Path | str | None = None) -> None:
    """Initialize the database by creating all tables.

    Args:
        db_path: Path to the SQLite database file.
    """
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        for statement in [
            "ALTER TABLE expenses ADD COLUMN is_approved BOOLEAN DEFAULT 0",
            "ALTER TABLE expenses ADD COLUMN approved_at DATETIME",
            "ALTER TABLE merchant_mappings ADD COLUMN canonical_merchant VARCHAR(500)",
        ]:
            try:
                connection.exec_driver_sql(statement)
            except Exception:
                pass

        connection.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT)"
        )
        already_backfilled = connection.exec_driver_sql(
            "SELECT value FROM app_meta WHERE key = 'approval_backfill_v1'"
        ).first()
        if not already_backfilled:
            connection.exec_driver_sql(
                """
                UPDATE expenses
                SET is_approved = 1,
                    approved_at = NULL
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO app_meta (key, value)
                VALUES ('approval_backfill_v1', CURRENT_TIMESTAMP)
                """
            )
        connection.exec_driver_sql(
            """
            UPDATE expenses
            SET is_approved = 0,
                approved_at = NULL
            WHERE needs_validation = 1
            """
        )


@contextmanager
def session_scope(db_path: Path | str | None = None) -> Generator[Session, None, None]:
    """Provide a transactional scope around a series of operations.

    Usage:
        with session_scope() as session:
            session.add(expense)
            session.commit()
    """
    SessionLocal = get_session(db_path)
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
