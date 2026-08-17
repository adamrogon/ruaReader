"""Shared IMAP access for the rua (Module 1) and bounce (Module 3) readers.

Both modules do the same thing — open a mailbox, pull raw messages, optionally
file them away afterwards — so the connection handling lives in one place.
"""

from __future__ import annotations

import datetime as dt
import email
import logging
from contextlib import contextmanager
from email.message import Message
from typing import Iterator, List, Optional, Tuple

from imapclient import IMAPClient

from ..config import Mailbox

logger = logging.getLogger(__name__)


@contextmanager
def open_mailbox(mailbox: Mailbox) -> Iterator[IMAPClient]:
    """Connect, log in, select the folder, and always log out."""
    client = IMAPClient(mailbox.host, port=mailbox.port, ssl=mailbox.ssl, timeout=60)
    try:
        client.login(mailbox.username, mailbox.password)
        client.select_folder(mailbox.folder)
        yield client
    finally:
        try:
            client.logout()
        except Exception:  # noqa: BLE001 — logout failures must not mask real errors
            logger.debug("IMAP logout failed for %s", mailbox.name, exc_info=True)


def fetch_messages(
    client: IMAPClient,
    since: Optional[dt.date] = None,
    unseen_only: bool = False,
    limit: Optional[int] = None,
) -> List[Tuple[int, Message]]:
    """Fetch messages as ``(uid, parsed_message)`` pairs.

    Deduplication happens against the database rather than via the \\Seen flag,
    so a re-run over already-read mail is harmless.
    """
    criteria: List = []
    if unseen_only:
        criteria.append("UNSEEN")
    if since:
        criteria.extend(["SINCE", since])
    if not criteria:
        criteria = ["ALL"]

    uids = client.search(criteria)
    if limit is not None:
        uids = uids[-limit:]
    if not uids:
        return []

    results: List[Tuple[int, Message]] = []
    # Batched so a mailbox with thousands of reports does not build one huge
    # response.
    for start in range(0, len(uids), 50):
        batch = uids[start : start + 50]
        response = client.fetch(batch, ["RFC822"])
        for uid, data in response.items():
            raw = data.get(b"RFC822")
            if not raw:
                continue
            results.append((uid, email.message_from_bytes(raw)))
    return results


def move_to_folder(client: IMAPClient, uid: int, folder: str) -> None:
    """File a processed message away, creating the folder if needed."""
    try:
        if not client.folder_exists(folder):
            client.create_folder(folder)
        client.move([uid], folder)
    except Exception:  # noqa: BLE001
        # Never let filing failures abort an ingestion run — the data is
        # already stored and dedupe will skip the message next time.
        logger.warning("Could not move message %s to %s", uid, folder, exc_info=True)


def iter_attachments(message: Message) -> Iterator[Tuple[str, bytes]]:
    """Yield ``(filename, payload)`` for every attachment-like part."""
    for part in message.walk():
        if part.get_content_maintype() == "multipart":
            continue
        filename = part.get_filename() or ""
        disposition = (part.get("Content-Disposition") or "").lower()
        content_type = part.get_content_type()

        is_attachment = "attachment" in disposition or bool(filename)
        # Some reporters send the XML as the body with no disposition header.
        is_report_body = content_type in {
            "application/zip",
            "application/gzip",
            "application/x-gzip",
            "application/xml",
            "text/xml",
        }
        if not (is_attachment or is_report_body):
            continue

        try:
            payload = part.get_payload(decode=True)
        except Exception:  # noqa: BLE001
            continue
        if payload:
            yield filename, payload


def header_datetime(message: Message) -> dt.datetime:
    """The message's Date header as an aware UTC datetime.

    Falls back to now() when the header is missing or unparseable, which does
    happen with malformed NDRs.
    """
    raw = message.get("Date")
    if raw:
        try:
            parsed = email.utils.parsedate_to_datetime(raw)
            if parsed is not None:
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=dt.timezone.utc)
                return parsed.astimezone(dt.timezone.utc)
        except (TypeError, ValueError):
            pass
    return dt.datetime.now(dt.timezone.utc)
