"""Module 5 — Sent-folder ingestion.

Extender (Linkhouse's real campaign sender) and Instantly (third-party
warm-up traffic) both relay through the same connected mailbox, so both land
in the same IMAP "Sent" folder. This reads that folder, counts every message
toward the sending domain it belongs to, and classifies each one (see
classify/sent_source.py) so warm-up traffic can be excluded from the
real-campaign count shown on the dashboard.

Unlike DMARC aggregate reports (Module 1), which typically lag 24-72h and
only reflect mail that reached a DMARC-participating receiver, this reads
what the mailbox itself actually submitted — same-run visibility, and it
counts mail no receiver ever got around to reporting on.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import re
from email.message import Message
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..classify.sent_source import classify_sent_message
from ..config import Mailbox, Settings, load_sent_mailboxes
from ..storage import Database, IngestionRunRepository, SentRepository, get_database
from .imap_client import fetch_messages, header_datetime, open_mailbox

logger = logging.getLogger(__name__)

STREAM = "sent"

_ADDRESS_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _decode_part(part: Message) -> str:
    try:
        payload = part.get_payload(decode=True)
    except Exception:  # noqa: BLE001
        return ""
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return payload.decode("utf-8", errors="replace")


def message_text(message: Message) -> str:
    """All human-readable text in the message — the classifier's pixel check
    runs against this, so the HTML part has to be included, not just plain
    text."""
    chunks: List[str] = []
    for part in message.walk():
        if part.get_content_maintype() == "multipart":
            continue
        content_type = (part.get_content_type() or "").lower()
        if content_type.startswith("text/") or content_type.startswith("message/"):
            text = _decode_part(part)
            if text:
                chunks.append(text)
    return "\n".join(chunks)


def parse_sent(message: Message, mailbox: Mailbox) -> Dict[str, Any]:
    """Extract everything usable from one Sent-folder message.

    Never raises — a message this can't fully parse still gets stored with
    whatever fields it could pull out, rather than being dropped and quietly
    undercounting the day's volume.
    """
    raw_text = message_text(message)
    subject = str(message.get("Subject") or "")
    to = str(message.get("To") or "")

    recipient_domain = None
    match = _ADDRESS_RE.search(to)
    if match and "@" in match.group(0):
        recipient_domain = match.group(0).split("@", 1)[1].lower()

    source, reason = classify_sent_message(message, raw_text)

    message_id = str(message.get("Message-Id") or "").strip()
    if not message_id:
        # A rare Sent-folder message with no Message-Id at all — a stable
        # fallback id so it still dedupes across re-runs instead of being
        # re-stored (and re-counted) on every poll.
        fingerprint = f"{subject}:{to}:{header_datetime(message)}"
        message_id = f"no-id-{hashlib.sha256(fingerprint.encode('utf-8')).hexdigest()[:32]}"

    return {
        "sending_domain": mailbox.domain,
        "mailbox_name": mailbox.name,
        "message_id": message_id,
        "sent_at": header_datetime(message),
        "source": source,
        "match_reason": reason,
        "recipient_domain": recipient_domain,
        "subject": subject[:500] or None,
    }


def ingest_mailbox(
    mailbox: Mailbox,
    repository: SentRepository,
    since_days: int = 7,
) -> Dict[str, int]:
    """Read one sending mailbox's Sent folder and store any new messages."""
    stats = {
        "messages": 0,
        "stored": 0,
        "duplicates": 0,
        "extender": 0,
        "instantly": 0,
        "unrecognized": 0,
    }
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=since_days)).date()

    with open_mailbox(mailbox) as client:
        messages = fetch_messages(client, since=since)
        stats["messages"] = len(messages)

        candidates: List[Tuple[int, Dict[str, Any]]] = []
        for uid, message in messages:
            try:
                candidates.append((uid, parse_sent(message, mailbox)))
            except Exception:  # noqa: BLE001
                # parse_sent is written not to raise, but a parser crash must
                # never lose the whole run — see the equivalent guard in
                # bounce.ingest_mailbox().
                logger.exception("Unexpected failure parsing a sent message in %s", mailbox.name)

        existing = repository.existing_message_ids(mailbox.name, [rec["message_id"] for _, rec in candidates])
        fresh = [rec for _, rec in candidates if rec["message_id"] not in existing]
        stats["duplicates"] = len(candidates) - len(fresh)

        if fresh:
            stats["stored"] = repository.insert_many(fresh)
            for rec in fresh:
                stats[rec["source"]] = stats.get(rec["source"], 0) + 1

    return stats


def run(
    settings: Optional[Settings] = None,
    database: Optional[Database] = None,
    mailboxes: Optional[Sequence[Mailbox]] = None,
    since_days: int = 7,
) -> Dict[str, Any]:
    """Ingest every configured sent mailbox, recording the run."""
    settings = settings or Settings.from_env()
    database = database or get_database(settings)
    mailboxes = mailboxes if mailboxes is not None else load_sent_mailboxes()

    repository = SentRepository(database, settings.project_id)
    runs = IngestionRunRepository(database, settings.project_id)
    run_id = runs.start(STREAM)

    totals = {
        "messages": 0,
        "stored": 0,
        "duplicates": 0,
        "extender": 0,
        "instantly": 0,
        "unrecognized": 0,
    }
    per_mailbox: Dict[str, Any] = {}
    failures: List[str] = []

    for mailbox in mailboxes:
        try:
            try:
                stats = ingest_mailbox(mailbox, repository, since_days=since_days)
            except Exception:  # noqa: BLE001
                # ingest_mailbox() opens its own connection, so retrying the
                # whole call gets a fresh IMAPClient/socket — see
                # bounce.run()'s identical retry for why this matters more
                # than a batch-level retry inside fetch_messages() can.
                logger.warning("Sent ingestion failed for %s, retrying once with a fresh connection", mailbox.name)
                stats = ingest_mailbox(mailbox, repository, since_days=since_days)
            per_mailbox[mailbox.name] = stats
            for key, value in stats.items():
                totals[key] = totals.get(key, 0) + value
        except Exception as exc:  # noqa: BLE001
            logger.exception("Sent ingestion failed for mailbox %s", mailbox.name)
            failures.append(f"{mailbox.name}: {exc}")
            per_mailbox[mailbox.name] = {"error": str(exc)}

    if not failures:
        status = "ok"
    elif len(failures) == len(mailboxes):
        status = "error"
    else:
        status = "partial"
    runs.finish(
        run_id,
        status=status,
        items_seen=totals["messages"],
        items_ingested=totals["stored"],
        error="; ".join(failures) or None,
        detail=per_mailbox,
    )
    return {"status": status, "totals": totals, "mailboxes": per_mailbox, "errors": failures}
