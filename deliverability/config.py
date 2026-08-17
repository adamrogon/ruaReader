"""Configuration loading.

Two sources, kept deliberately separate:

* ``.env``      — secrets and machine-local paths. Never committed.
* ``config/*.yml`` — the inventory of domains and mailboxes. Safe to commit.

A mailbox entry names an environment variable (``password_env``) rather than
carrying a password, so the YAML stays committable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"

# Loaded once at import; values already in the real environment win, which is
# what you want when running under a scheduler that injects its own env.
load_dotenv(PROJECT_ROOT / ".env", override=False)


class ConfigError(RuntimeError):
    """Raised when configuration is missing or malformed."""


@dataclass(frozen=True)
class Mailbox:
    """An IMAP mailbox to poll.

    Used for both rua report mailboxes (Module 1) and bounce mailboxes
    (Module 3). ``domain`` is only meaningful for bounce mailboxes, where it
    ties an NDR back to the sending domain.

    A password comes from one of two places: ``password_encrypted`` for
    mailboxes added through the dashboard, or ``password_env`` for ones
    bootstrapped from YAML. Neither is resolved until it is actually needed.
    """

    name: str
    host: str
    username: str
    password_env: Optional[str] = None
    password_encrypted: Optional[str] = None
    port: int = 993
    ssl: bool = True
    folder: str = "INBOX"
    processed_folder: Optional[str] = None
    enabled: bool = True
    domain: Optional[str] = None
    id: Optional[int] = None
    kind: str = "rua"

    @property
    def password(self) -> str:
        """Resolve the password at use time.

        Deliberately not stored on the instance so a stray repr/log of the
        config object cannot leak it.
        """
        if self.password_encrypted:
            from .secrets import decrypt

            return decrypt(self.password_encrypted)

        if self.password_env:
            value = os.environ.get(self.password_env)
            if not value:
                raise ConfigError(
                    f"Mailbox {self.name!r} needs environment variable "
                    f"{self.password_env!r}, which is unset or empty. "
                    f"Add it to your .env file."
                )
            return value

        raise ConfigError(
            f"Mailbox {self.name!r} has no password configured. Set one in the "
            f"dashboard under Settings, or give it a password_env in "
            f"config/mailboxes.yml."
        )

    @property
    def has_password(self) -> bool:
        """Whether a password is configured at all, without decrypting it."""
        return bool(self.password_encrypted or (self.password_env and os.environ.get(self.password_env)))


@dataclass(frozen=True)
class Domain:
    """A sending domain under monitoring."""

    name: str
    dkim_selectors: List[str] = field(default_factory=list)
    notes: str = ""
    enabled: bool = True
    id: Optional[int] = None


@dataclass(frozen=True)
class Settings:
    """Machine-local settings from .env."""

    project_id: str
    database_url: str
    archive_dir: Path
    recipient_hash_salt: str

    @classmethod
    def from_env(cls) -> "Settings":
        database_url = os.environ.get("DATABASE_URL", "sqlite:///data/deliverability.db")
        archive_dir = Path(os.environ.get("ARCHIVE_DIR", "data/archive"))
        if not archive_dir.is_absolute():
            archive_dir = PROJECT_ROOT / archive_dir

        salt = os.environ.get("RECIPIENT_HASH_SALT", "")
        if not salt or salt == "change-me-to-a-long-random-string":
            # Not fatal — hashing still works, it is just not secret. Warned
            # about rather than raised so a first run does not hard-fail.
            salt = salt or "insecure-default-salt"

        # Relative sqlite paths are resolved against the project root so the
        # tool behaves the same regardless of the working directory it runs in.
        if database_url.startswith("sqlite:///") and not database_url.startswith("sqlite:////"):
            rel = database_url[len("sqlite:///") :]
            database_url = f"sqlite:///{(PROJECT_ROOT / rel).as_posix()}"

        return cls(
            project_id=os.environ.get("PROJECT_ID", "linkhouse"),
            database_url=database_url,
            archive_dir=archive_dir,
            recipient_hash_salt=salt,
        )


def _read_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"Missing config file: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a YAML mapping at the top level.")
    return data


def _build_mailbox(entry: Dict[str, Any], source: str) -> Mailbox:
    for required in ("name", "host", "username", "password_env"):
        if not entry.get(required):
            raise ConfigError(f"Mailbox in {source} is missing required key {required!r}: {entry!r}")
    return Mailbox(
        name=entry["name"],
        kind="bounce" if source == "bounce_mailboxes" else "rua",
        host=entry["host"],
        username=entry["username"],
        password_env=entry["password_env"],
        port=int(entry.get("port", 993)),
        ssl=bool(entry.get("ssl", True)),
        folder=entry.get("folder", "INBOX"),
        processed_folder=entry.get("processed_folder"),
        enabled=bool(entry.get("enabled", True)),
        domain=entry.get("domain"),
    )


def load_rua_mailboxes_from_yaml(path: Optional[Path] = None) -> List[Mailbox]:
    """Rua mailboxes as declared in YAML, used to seed the database."""
    path = path or CONFIG_DIR / "mailboxes.yml"
    data = _read_yaml(path)
    entries = data.get("rua_mailboxes") or []
    return [_build_mailbox(e, "rua_mailboxes") for e in entries]


def load_bounce_mailboxes_from_yaml(path: Optional[Path] = None) -> List[Mailbox]:
    """Bounce mailboxes as declared in YAML, used to seed the database."""
    path = path or CONFIG_DIR / "mailboxes.yml"
    data = _read_yaml(path)
    entries = data.get("bounce_mailboxes") or []
    boxes = [_build_mailbox(e, "bounce_mailboxes") for e in entries]
    for box in boxes:
        if not box.domain:
            raise ConfigError(
                f"Bounce mailbox {box.name!r} must declare a 'domain' so its "
                f"NDRs can be attributed to a sending domain."
            )
    return boxes


def load_domains_from_yaml(path: Optional[Path] = None) -> List[Domain]:
    """Domains as declared in YAML, used to seed the database."""
    path = path or CONFIG_DIR / "domains.yml"
    data = _read_yaml(path)
    defaults = data.get("defaults") or {}
    default_selectors = list(defaults.get("dkim_selectors") or [])

    domains: List[Domain] = []
    for entry in data.get("domains") or []:
        if not entry.get("name"):
            raise ConfigError(f"Domain entry missing 'name': {entry!r}")
        selectors = entry.get("dkim_selectors")
        domains.append(
            Domain(
                name=entry["name"].strip().lower(),
                # An explicit empty list means "no DKIM check", which is
                # different from omitting the key (inherit the defaults).
                dkim_selectors=list(selectors) if selectors is not None else list(default_selectors),
                notes=entry.get("notes", ""),
            )
        )
    return domains


# --- Database-backed configuration -------------------------------------------
#
# The database is the source of truth so the dashboard can manage domains and
# mailboxes. The YAML files are read exactly once — when the tables are still
# empty — so an existing checkout keeps working and YAML remains a valid way to
# bootstrap a new install.


def _domain_from_row(row: Dict[str, Any]) -> Domain:
    return Domain(
        id=row["id"],
        name=row["name"],
        dkim_selectors=list(row.get("dkim_selectors") or []),
        notes=row.get("notes") or "",
        enabled=bool(row.get("enabled", True)),
    )


def _mailbox_from_row(row: Dict[str, Any]) -> Mailbox:
    return Mailbox(
        id=row["id"],
        name=row["name"],
        kind=row["kind"],
        host=row["host"],
        port=int(row.get("port") or 993),
        ssl=bool(row.get("ssl", True)),
        username=row["username"],
        password_encrypted=row.get("password_encrypted"),
        password_env=row.get("password_env"),
        folder=row.get("folder") or "INBOX",
        processed_folder=row.get("processed_folder"),
        domain=row.get("domain"),
        enabled=bool(row.get("enabled", True)),
    )


def seed_config_from_yaml_if_empty(database=None, settings: Optional[Settings] = None) -> Dict[str, int]:
    """Copy YAML config into the database the first time it is needed.

    Only runs when the tables are empty, so it never fights with edits made in
    the dashboard. Missing YAML files are not an error — a fresh install can
    start with nothing configured and add everything through the UI.
    """
    from .storage import DomainConfigRepository, MailboxConfigRepository, get_database

    settings = settings or Settings.from_env()
    database = database or get_database(settings)

    domain_repo = DomainConfigRepository(database, settings.project_id)
    mailbox_repo = MailboxConfigRepository(database, settings.project_id)
    seeded = {"domains": 0, "mailboxes": 0}

    if domain_repo.count() == 0:
        try:
            for domain in load_domains_from_yaml():
                domain_repo.create(
                    name=domain.name,
                    dkim_selectors=domain.dkim_selectors,
                    notes=domain.notes,
                    enabled=True,
                )
                seeded["domains"] += 1
        except ConfigError:
            pass

    if mailbox_repo.count() == 0:
        try:
            entries = [(m, "rua") for m in load_rua_mailboxes_from_yaml()]
        except ConfigError:
            entries = []
        try:
            entries += [(m, "bounce") for m in load_bounce_mailboxes_from_yaml()]
        except ConfigError:
            pass

        for mailbox, kind in entries:
            mailbox_repo.create(
                name=mailbox.name,
                kind=kind,
                host=mailbox.host,
                port=mailbox.port,
                ssl=mailbox.ssl,
                username=mailbox.username,
                # YAML mailboxes keep pointing at an env var; the password is
                # not copied into the database on their behalf.
                password_env=mailbox.password_env,
                folder=mailbox.folder,
                processed_folder=mailbox.processed_folder,
                domain=mailbox.domain,
                enabled=mailbox.enabled,
            )
            seeded["mailboxes"] += 1

    return seeded


def load_domains(database=None, settings: Optional[Settings] = None) -> List[Domain]:
    """Enabled sending domains under monitoring (Modules 2 and 4)."""
    from .storage import DomainConfigRepository, get_database

    settings = settings or Settings.from_env()
    database = database or get_database(settings)
    seed_config_from_yaml_if_empty(database, settings)

    repo = DomainConfigRepository(database, settings.project_id)
    return [_domain_from_row(row) for row in repo.list_all(include_disabled=False)]


def load_rua_mailboxes(database=None, settings: Optional[Settings] = None) -> List[Mailbox]:
    """Enabled mailboxes that receive DMARC aggregate reports (Module 1)."""
    from .storage import MailboxConfigRepository, get_database

    settings = settings or Settings.from_env()
    database = database or get_database(settings)
    seed_config_from_yaml_if_empty(database, settings)

    repo = MailboxConfigRepository(database, settings.project_id)
    return [_mailbox_from_row(row) for row in repo.list_all(kind="rua", include_disabled=False)]


def load_bounce_mailboxes(database=None, settings: Optional[Settings] = None) -> List[Mailbox]:
    """Enabled sending mailboxes that receive bounces/NDRs (Module 3)."""
    from .storage import MailboxConfigRepository, get_database

    settings = settings or Settings.from_env()
    database = database or get_database(settings)
    seed_config_from_yaml_if_empty(database, settings)

    repo = MailboxConfigRepository(database, settings.project_id)
    boxes = [_mailbox_from_row(row) for row in repo.list_all(kind="bounce", include_disabled=False)]
    return [b for b in boxes if b.domain]
