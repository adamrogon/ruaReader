"""Engine and connection handling.

Thin wrapper over a SQLAlchemy Core engine. Nothing above this module should
import SQLAlchemy directly.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from sqlalchemy import Connection, create_engine, event
from sqlalchemy.engine import Engine

from ..config import Settings
from .schema import metadata


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

        self.engine: Engine = create_engine(url, future=True)

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
        """Create any missing tables. Safe to call on every run."""
        metadata.create_all(self.engine)

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
