"""Module 3 — bounce / NDR ingestion.

NDRs come back to the mailbox that sent the message, so this reads a list of
sending mailboxes rather than one consolidated inbox.

RFC 3464 defines a structured ``message/delivery-status`` part, and when it is
present this parses it properly. Plenty of real bounces do not comply — some
providers send a human-readable message with no delivery-status part at all, and
others put the status code only in the body text. The parser is therefore
deliberately tolerant: it falls back through progressively looser strategies and,
if it still cannot find a status code, stores the full text with
``parse_ok = False`` rather than dropping the record. A bounce nobody can parse
is still evidence that something is wrong.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import re
from email.message import Message
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..classify.bounce_codes import BounceClass, classify_bounce, extract_status_code, smtp_class
from ..classify.esp import esp_from_email_domain, esp_from_mta, esp_from_mx
from ..config import Mailbox, Settings, load_bounce_mailboxes
from ..storage import BounceRepository, Database, IngestionRunRepository, get_database
from .imap_client import fetch_messages, header_datetime, move_to_folder, open_mailbox

logger = logging.getLogger(__name__)

STREAM = "bounce"

# Subjects and senders that mark a message as a bounce when there is no
# machine-readable delivery-status part.
_BOUNCE_SUBJECTS = re.compile(
    r"undeliverable|delivery\s+(?:status|has\s+failed|failure|incomplete)|"
    r"returned\s+mail|mail\s+delivery\s+failed|failure\s+notice|"
    r"undelivered\s+mail|delivery\s+notification|nie\s+dostarczono|niedostarczon",
    re.IGNORECASE,
)
_BOUNCE_SENDERS = re.compile(
    r"mailer-daemon|postmaster|mail\s*delivery\s*(?:subsystem|system)|no-?reply.*deliver",
    re.IGNORECASE,
)
# RFC 7489 §7.2.1's mandated subject line for DMARC aggregate report emails:
# "Report Domain: <policy domain> Submitter: <report generator> Report-ID:
# <id>". Report generators are recommended to send from postmaster@<domain>,
# which _BOUNCE_SENDERS also matches — so on a mailbox used for both rua and
# bounce (one inbox, both mailbox configs pointing at it), a DMARC report
# email would otherwise pass the sender check and get scanned as a bounce,
# correctly fail to parse as one, and land in the log as noise.
_DMARC_REPORT_SUBJECT = re.compile(r"^report\s+domain:.*submitter:.*report-id:", re.IGNORECASE)

_ADDRESS_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_SMTP_CODE_RE = re.compile(r"\b([45]\d{2})\b")


def hash_recipient(address: str, salt: str) -> str:
    """Hash a recipient address.

    The local part is hashed so repeat failures to the same address can still be
    counted without storing the address itself. The domain is kept separately in
    clear, because "which provider is rejecting us" is the diagnostic question
    the tool exists to answer.
    """
    return hashlib.sha256(f"{salt}:{address.strip().lower()}".encode("utf-8")).hexdigest()


def looks_like_bounce(message: Message) -> bool:
    """Cheap pre-filter before doing the work of parsing."""
    content_type = (message.get_content_type() or "").lower()
    if content_type == "multipart/report":
        report_type = (message.get_param("report-type") or "").lower()
        if report_type == "delivery-status":
            return True
    subject = message.get("Subject") or ""
    if _DMARC_REPORT_SUBJECT.search(subject):
        return False
    sender = message.get("From") or ""
    return bool(_BOUNCE_SUBJECTS.search(subject) or _BOUNCE_SENDERS.search(sender))


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
    """All human-readable text in the message, for fallback parsing."""
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


def parse_delivery_status(message: Message) -> Optional[Dict[str, Any]]:
    """Parse a structured RFC 3464 ``message/delivery-status`` part.

    Returns None when no such part exists, which is common enough that it is a
    normal path rather than an error.
    """
    for part in message.walk():
        if (part.get_content_type() or "").lower() != "message/delivery-status":
            continue

        text = _decode_part(part)
        if not text:
            # Some mailers nest the per-recipient fields as sub-messages
            # instead of inline text.
            payload = part.get_payload()
            if isinstance(payload, list):
                text = "\n\n".join(
                    "\n".join(f"{k}: {v}" for k, v in sub.items()) for sub in payload if hasattr(sub, "items")
                )
        if not text:
            continue

        fields: Dict[str, str] = {}
        # Unfold continuation lines before splitting on ':'.
        unfolded = re.sub(r"\n[ \t]+", " ", text)
        for line in unfolded.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip().lower()
            value = value.strip()
            # Later per-recipient blocks should not overwrite an earlier
            # Status that actually carried a code.
            if key not in fields or not fields[key]:
                fields[key] = value

        if fields:
            return fields
    return None


def _clean_address(raw: Optional[str]) -> Optional[str]:
    """Pull a bare address out of 'rfc822; user@example.com' style fields."""
    if not raw:
        return None
    match = _ADDRESS_RE.search(raw)
    return match.group(0).lower() if match else None


def parse_bounce(message: Message, mailbox: Mailbox, salt: str) -> Dict[str, Any]:
    """Extract everything usable from one NDR.

    Never raises on malformed input — the worst case is a record with
    ``parse_ok = False`` and the full text preserved.
    """
    raw_text = message_text(message)
    subject = message.get("Subject") or ""
    fields = parse_delivery_status(message) or {}

    status_code = None
    diagnostic = fields.get("diagnostic-code")
    reporting_mta = fields.get("reporting-mta")
    remote_mta = fields.get("remote-mta")
    recipient = _clean_address(fields.get("final-recipient") or fields.get("original-recipient"))

    # Strategy 1 — the Status field, as the RFC intends.
    raw_status = fields.get("status")
    if raw_status:
        status_code = extract_status_code(raw_status)

    # Strategy 2 — a code embedded in the Diagnostic-Code text.
    if not status_code and diagnostic:
        status_code = extract_status_code(diagnostic)

    # Strategy 3 — anywhere in the body.
    if not status_code:
        status_code = extract_status_code(raw_text)

    # A structured part with a usable code is the only case counted as a clean
    # parse; everything else is flagged for review while still being stored.
    parse_ok = bool(status_code)

    if not recipient:
        # Fall back to the first address in the body that is not our own.
        for candidate in _ADDRESS_RE.findall(raw_text):
            if candidate.lower() != (mailbox.username or "").lower():
                recipient = candidate.lower()
                break

    if not diagnostic:
        # Keep a bounded slice of the body so the dashboard has something to
        # show without storing the entire message twice.
        diagnostic = (raw_text or "").strip()[:1000] or None

    bounce_class, reason = classify_bounce(status_code, diagnostic, raw_text)

    smtp_match = _SMTP_CODE_RE.search(diagnostic or "") or _SMTP_CODE_RE.search(raw_text or "")
    recipient_domain = recipient.split("@", 1)[1] if recipient and "@" in recipient else None

    # Prefer the rejecting MTA's identity for ESP attribution; a custom domain
    # hosted at Google only reveals itself through the MTA hostname.
    # Three-step fallback: rejecting MTA first (most authoritative — the actual
    # server that answered), then the address's own domain string, and finally
    # a real MX lookup for domains that don't reveal the provider by name (a
    # custom-domain Google Workspace tenant, a home.pl-hosted mailbox, etc.).
    # The MX step is cached per-process so 500 bounces to 30 unique domains
    # cost 30 DNS queries, not 500.
    recipient_esp = esp_from_mta(remote_mta or reporting_mta)
    if recipient_esp in ("Unknown", "Other"):
        from_domain_esp = esp_from_email_domain(recipient_domain)
        if from_domain_esp not in ("Unknown", "Other"):
            recipient_esp = from_domain_esp
    if recipient_esp in ("Unknown", "Other") and recipient_domain:
        mx_esp = esp_from_mx(recipient_domain)
        if mx_esp not in ("Unknown", "Other"):
            recipient_esp = mx_esp

    return {
        "sending_domain": mailbox.domain,
        "mailbox_name": mailbox.name,
        "message_id": (message.get("Message-Id") or "").strip() or f"no-id-{hash_recipient(subject + str(header_datetime(message)), salt)[:32]}",
        "received_at": header_datetime(message),
        "status_code": status_code,
        "smtp_code": smtp_match.group(1) if smtp_match else None,
        "bounce_class": bounce_class,
        "bounce_reason": reason,
        "diagnostic_code": (diagnostic or "")[:2000] or None,
        "reporting_mta": (reporting_mta or "")[:255] or None,
        "remote_mta": (remote_mta or "")[:255] or None,
        "recipient_hash": hash_recipient(recipient, salt) if recipient else None,
        "recipient_domain": recipient_domain,
        "recipient_esp": recipient_esp,
        "parse_ok": parse_ok,
        # Bounded so one enormous message cannot bloat the database.
        "raw_text": (raw_text or "")[:20000] or None,
        "subject": subject[:500] or None,
    }


def ingest_mailbox(
    mailbox: Mailbox,
    repository: BounceRepository,
    salt: str,
    since_days: int = 7,
) -> Dict[str, int]:
    """Read one sending mailbox and store any new bounces."""
    stats = {"messages": 0, "bounces_found": 0, "bounces_stored": 0, "duplicates": 0, "unparsed": 0}
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=since_days)).date()

    with open_mailbox(mailbox) as client:
        # Filtered at fetch time, not after: most messages in a mailbox
        # window are not bounces, and unlike a compact DMARC XML report a
        # bounce can carry the entire original message echoed back (images,
        # HTML, attachments) — holding every non-bounce message in memory
        # too, just to discard it a moment later, was real memory pressure
        # for no reason. "messages" below is therefore a count of candidates
        # that passed the cheap looks_like_bounce() check, not the whole
        # mailbox's traffic.
        messages = fetch_messages(client, since=since, predicate=looks_like_bounce)
        stats["messages"] = len(messages)

        candidates: List[Tuple[int, Dict[str, Any]]] = []
        for uid, message in messages:
            stats["bounces_found"] += 1
            try:
                candidates.append((uid, parse_bounce(message, mailbox, salt)))
            except Exception:  # noqa: BLE001
                # Should not happen — parse_bounce is written not to raise —
                # but a parser crash must never lose the whole run.
                logger.exception("Unexpected failure parsing a bounce in %s", mailbox.name)

        existing = repository.existing_message_ids(mailbox.name, [rec["message_id"] for _, rec in candidates])
        fresh = [(uid, rec) for uid, rec in candidates if rec["message_id"] not in existing]
        stats["duplicates"] = len(candidates) - len(fresh)
        stats["unparsed"] = sum(1 for _, rec in fresh if not rec["parse_ok"])

        if fresh:
            stats["bounces_stored"] = repository.insert_many([rec for _, rec in fresh])

        if mailbox.processed_folder:
            for uid, _ in fresh:
                move_to_folder(client, uid, mailbox.processed_folder)

    return stats


def run(
    settings: Optional[Settings] = None,
    database: Optional[Database] = None,
    mailboxes: Optional[Sequence[Mailbox]] = None,
    since_days: int = 7,
) -> Dict[str, Any]:
    """Ingest every configured bounce mailbox, recording the run."""
    settings = settings or Settings.from_env()
    database = database or get_database(settings)
    mailboxes = mailboxes if mailboxes is not None else load_bounce_mailboxes()

    repository = BounceRepository(database, settings.project_id)
    runs = IngestionRunRepository(database, settings.project_id)
    run_id = runs.start(STREAM)

    totals = {"messages": 0, "bounces_found": 0, "bounces_stored": 0, "duplicates": 0, "unparsed": 0}
    per_mailbox: Dict[str, Any] = {}
    failures: List[str] = []

    for mailbox in mailboxes:
        try:
            stats = ingest_mailbox(mailbox, repository, settings.recipient_hash_salt, since_days=since_days)
            per_mailbox[mailbox.name] = stats
            for key, value in stats.items():
                totals[key] = totals.get(key, 0) + value
        except Exception as exc:  # noqa: BLE001
            logger.exception("Bounce ingestion failed for mailbox %s", mailbox.name)
            failures.append(f"{mailbox.name}: {exc}")
            per_mailbox[mailbox.name] = {"error": str(exc)}

    # "partial" matters as much as "error" once there is more than a handful
    # of mailboxes: one broken mailbox among thirty must not read as "ok"
    # just because the other twenty-nine succeeded.
    if not failures:
        status = "ok"
    elif len(failures) == len(mailboxes):
        status = "error"
    else:
        status = "partial"
    runs.finish(
        run_id,
        status=status,
        items_seen=totals["bounces_found"],
        items_ingested=totals["bounces_stored"],
        error="; ".join(failures) or None,
        detail=per_mailbox,
    )
    return {"status": status, "totals": totals, "mailboxes": per_mailbox, "errors": failures}
