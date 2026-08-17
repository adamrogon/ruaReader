"""Encryption for mailbox passwords stored in the database.

Passwords used to live only in ``.env``, referenced by variable name. That
works when mailboxes are added by editing a YAML file, but not when they are
added through the dashboard — the app has to persist the password itself.

So they are encrypted with Fernet (AES-128-CBC + HMAC) and stored as ciphertext
in the ``mailboxes`` table. The key stays in ``.env`` as ``SECRET_KEY`` and is
never written to the database, so the original rule still holds: no secret is
ever committed, and the database file alone is not enough to recover a password.

Losing ``SECRET_KEY`` means every stored password becomes unreadable and has to
be re-entered — which is why it is generated once and left alone.
"""

from __future__ import annotations

import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

SECRET_KEY_ENV = "SECRET_KEY"


class SecretsError(RuntimeError):
    """Raised when the encryption key is missing or a value cannot be decrypted."""


def generate_key() -> str:
    """A fresh Fernet key, for pasting into .env."""
    return Fernet.generate_key().decode("ascii")


def _fernet() -> Fernet:
    key = (os.environ.get(SECRET_KEY_ENV) or "").strip()
    if not key:
        raise SecretsError(
            f"{SECRET_KEY_ENV} is not set. Generate one with "
            f"`python -m deliverability.cli genkey` and add it to your .env file. "
            f"It is needed to store or read mailbox passwords."
        )
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise SecretsError(
            f"{SECRET_KEY_ENV} is not a valid Fernet key. Generate a new one with "
            f"`python -m deliverability.cli genkey`."
        ) from exc


def encrypt(plaintext: str) -> str:
    """Encrypt a password for storage."""
    if plaintext is None:
        raise SecretsError("Cannot encrypt a null value.")
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    """Decrypt a stored password.

    A failure here almost always means ``SECRET_KEY`` changed since the value
    was written, so the message says that rather than just "invalid token".
    """
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise SecretsError(
            "Stored password could not be decrypted. This usually means SECRET_KEY "
            "changed since it was saved — re-enter the password for this mailbox."
        ) from exc


def is_configured() -> bool:
    """Whether a usable key is present, for showing a warning in the UI."""
    try:
        _fernet()
        return True
    except SecretsError:
        return False


def mask(value: Optional[str]) -> str:
    """A placeholder for display — the real password is never sent to the UI."""
    return "••••••••" if value else ""
