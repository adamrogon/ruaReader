"""Engine and connection handling.

Thin wrapper over a SQLAlchemy Core engine. Nothing above this module should
import SQLAlchemy directly.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from sqlalchemy import Connection, create_engine, event, inspect, text
from sqlalchemy.engine import Engine

from ..config import Settings
from .schema import metadata

logger = logging.getLogger(__name__)


class Database:
    """Owns the engine and hands out connections."""

    def __init__(self, url: str) -> None:
        self.url = url
        if url.startswith("sqlite"):
            # SQLite only: make sure the parent directory exists before the
            # engine tries to open the file.
            path = url.replace("sqlite:///", "", 1)
            if path and path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)

        connect_args = {}
        if url.startswith("sqlite"):
            # Ingestion triggered from the dashboard runs on a worker thread
            # while the request thread keeps serving pages, so connections
            # have to be usable across threads.
            connect_args["check_same_thread"] = False
            # Wait rather than fail instantly if the single writer lock is
            # held — an ingestion run and a page load overlapping is normal.
            connect_args["timeout"] = 30

        self.engine: Engine = create_engine(url, future=True, connect_args=connect_args)

        if url.startswith("sqlite"):
            # WAL lets the dashboard read while an ingestion run is writing;
            # foreign keys are off by default in SQLite and have to be asked for.
            @event.listens_for(self.engine, "connect")
            def _sqlite_pragmas(dbapi_connection, _record):  # noqa: ANN001
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

    def create_all(self) -> None:
        """Create any missing tables, and add any missing columns to existing ones.

        ``metadata.create_all()`` only creates tables that don't exist yet — it
        never alters a table that is already there, so a column added to
        schema.py after a database already has real data (e.g. the
        ``blacklist_checks.ptr_hostname`` hint) would otherwise never appear on
        an existing install. Both SQLite and Postgres support ``ADD COLUMN``
        for a plain nullable column, so this stays a safe, idempotent no-op
        once the column exists — safe to run on every startup, including a
        brand-new database where create_all() just created it already.
        """
        metadata.create_all(self.engine)
        inspector = inspect(self.engine)
        with self.engine.begin() as conn:
            for table in metadata.tables.values():
                if not inspector.has_table(table.name):
                    continue
                existing = {col["name"] for col in inspector.get_columns(table.name)}
                for column in table.columns:
                    if column.name in existing:
                        continue
                    try:
                        col_type = column.type.compile(self.engine.dialect)
                        conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type}'))
                        logger.info("Added missing column %s.%s", table.name, column.name)
                    except Exception:  # noqa: BLE001 — never block startup over a schema tweak
                        logger.warning("Could not add column %s.%s", table.name, column.name, exc_info=True)

    @contextmanager
    def connect(self) -> Iterator[Connection]:
        """A connection wrapped in a transaction that commits on clean exit."""
        with self.engine.begin() as connection:
            yield connection

    def dispose(self) -> None:
        self.engine.dispose()


_database: Optional[Database] = None


def get_database(settings: Optional[Settings] = None) -> Database:
    """Process-wide database handle, created on first use."""
    global _database
    if _database is None:
        settings = settings or Settings.from_env()
        _database = Database(settings.database_url)
        _database.create_all()
    return _database
