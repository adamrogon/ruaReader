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
    """

    name: str
    host: str
    username: str
    password_env: str
    port: int = 993
    ssl: bool = True
    folder: str = "INBOX"
    processed_folder: Optional[str] = None
    enabled: bool = True
    domain: Optional[str] = None

    @property
    def password(self) -> str:
        """Resolve the password from the environment at use time.

        Deliberately not stored on the instance so a stray repr/log of the
        config object cannot leak it.
        """
        value = os.environ.get(self.password_env)
        if not value:
            raise ConfigError(
                f"Mailbox {self.name!r} needs environment variable "
                f"{self.password_env!r}, which is unset or empty. "
                f"Add it to your .env file."
            )
        return value


@dataclass(frozen=True)
class Domain:
    """A sending domain under monitoring."""

    name: str
    dkim_selectors: List[str] = field(default_factory=list)
    notes: str = ""


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


def load_rua_mailboxes(path: Optional[Path] = None) -> List[Mailbox]:
    """Mailboxes that receive DMARC aggregate reports (Module 1)."""
    path = path or CONFIG_DIR / "mailboxes.yml"
    data = _read_yaml(path)
    entries = data.get("rua_mailboxes") or []
    return [m for m in (_build_mailbox(e, "rua_mailboxes") for e in entries) if m.enabled]


def load_bounce_mailboxes(path: Optional[Path] = None) -> List[Mailbox]:
    """Sending mailboxes that receive bounces/NDRs (Module 3)."""
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
    return [m for m in boxes if m.enabled]


def load_domains(path: Optional[Path] = None) -> List[Domain]:
    """Sending domains under monitoring (Modules 2 and 4)."""
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
