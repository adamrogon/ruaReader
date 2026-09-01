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
from typing import Any, Dict, Iterator, List, Optional, Tuple

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


# If this many *consecutive* per-UID fetches fail during fallback, the
# connection itself is broken, not one odd message — stop trying the rest
# individually. Without this cap, a genuinely dead connection turned "one
# fast batch failure" into up to 50 sequential ~60s socket timeouts (the
# IMAPClient timeout in open_mailbox) — a single bad mailbox could then
# hang the whole ingestion run for the better part of an hour instead of
# failing fast the way it used to.
_MAX_CONSECUTIVE_FALLBACK_FAILURES = 3


def _fetch_batch_with_fallback(client: IMAPClient, uids: List[int]) -> Dict[int, Dict[bytes, Any]]:
    """Fetch a batch of UIDs, recovering from a transient server error.

    A batch FETCH fails as a whole even when only one message in it is the
    actual problem (e.g. Gmail's occasional "System Error (Failure)" on a
    single oversized/odd message) — the other 49 shouldn't be lost over it.
    One immediate retry clears most transient hiccups; if it doesn't, fall
    back to fetching one UID at a time so only the genuinely bad message is
    skipped (and logged), not the whole batch — but bail out of that
    fallback fast if it's clearly the connection, not a message, that's bad
    (see _MAX_CONSECUTIVE_FALLBACK_FAILURES).
    """
    try:
        return client.fetch(uids, ["RFC822"])
    except Exception:  # noqa: BLE001
        logger.warning("Batch fetch failed for %d message(s), retrying once", len(uids), exc_info=True)

    try:
        return client.fetch(uids, ["RFC822"])
    except Exception:  # noqa: BLE001
        logger.warning(
            "Batch fetch still failing after retry — falling back to one message at a time", exc_info=True
        )

    response: Dict[int, Dict[bytes, Any]] = {}
    consecutive_failures = 0
    for uid in uids:
        try:
            response.update(client.fetch([uid], ["RFC822"]))
            consecutive_failures = 0
        except Exception:  # noqa: BLE001
            logger.warning("Skipping message %s — server would not fetch it", uid, exc_info=True)
            consecutive_failures += 1
            if consecutive_failures >= _MAX_CONSECUTIVE_FALLBACK_FAILURES:
                logger.warning(
                    "%d fetches in a row failed — treating this as a broken connection, "
                    "not skipping the remaining %d messages one by one",
                    consecutive_failures,
                    len(uids) - uids.index(uid) - 1,
                )
                break
    return response


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
        response = _fetch_batch_with_fallback(client, batch)
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


def find_junk_folder(client: IMAPClient) -> Optional[str]:
    """Find the mailbox's Spam/Junk folder, if any.

    Uses the IMAP SPECIAL-USE flag (``\\Junk``, RFC 6154) rather than a
    hardcoded name — Gmail localises "[Gmail]/Spam" to the account's own
    language (French, German, etc. all differ), so guessing a name would
    silently miss it for some accounts. Best-effort: a server that doesn't
    support SPECIAL-USE, or a transient LIST failure, just means no junk
    folder is checked this run — never worth aborting ingestion over.
    """
    try:
        for flags, _delimiter, name in client.list_folders():
            if b"\\Junk" in flags:
                return name
    except Exception:  # noqa: BLE001
        logger.debug("Could not list folders to find Junk", exc_info=True)
    return None


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
