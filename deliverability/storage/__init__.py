"""Storage layer.

Business logic talks to :mod:`deliverability.storage.repositories` only. All
SQL and table definitions live here, so swapping SQLite for Postgres later is a
change to the engine URL and this package, not to the ingestion or dashboard
code.
"""

from .database import Database, get_database
from .repositories import (
    BlacklistRepository,
    BounceRepository,
    DismissedFlagRepository,
    DmarcRepository,
    DnsRepository,
    DomainConfigRepository,
    IngestionRunRepository,
    MailboxConfigRepository,
)

__all__ = [
    "Database",
    "get_database",
    "DmarcRepository",
    "DnsRepository",
    "BounceRepository",
    "BlacklistRepository",
    "IngestionRunRepository",
    "DomainConfigRepository",
    "MailboxConfigRepository",
    "DismissedFlagRepository",
]
