"""Classifying Sent-folder mail: who actually generated this message.

Extender (Linkhouse's own campaign sender) and Instantly (third-party
warm-up traffic) both relay through the same connected mailbox, so both
land in the same IMAP "Sent" folder. Neither tool is the default — a
message that matches nothing is 'unrecognized', never silently folded into
'extender', because a heuristic that goes quiet when its signal drifts (a
hosting migration, a mailer-library upgrade) must become a visible, growing
"unrecognized" share on the dashboard, not a silently wrong "extender" count.
See CLAUDE.md's ``esp.py`` note for the same tradeoff made elsewhere in this
codebase, and why an unrecognized bucket has to stay separate.

Signals were derived from two real message samples (2026-09-21) and are
expected to drift over time — if the unrecognized share climbs, re-derive
them from fresh samples rather than patching the regexes blind.
"""

from __future__ import annotations

import re
from email.message import Message
from typing import Tuple

SOURCE_EXTENDER = "extender"
SOURCE_INSTANTLY = "instantly"
SOURCE_UNRECOGNIZED = "unrecognized"

# Extender's own tracking pixel, embedded in the HTML body
# (app.linkhouse.co/mail/icon.png?rcptid=...&cpgid=...&hstid=...). Scheduled
# for removal from the product — once gone this simply stops matching and
# classification falls through to the weaker signals below, it does not
# error.
_EXTENDER_PIXEL_RE = re.compile(r"app\.linkhouse\.co/mail/icon\.png", re.IGNORECASE)

# SwiftMailer's default Message-ID domain when no explicit one is configured
# — a side effect of Extender's mailer library, not an intentional marker.
_EXTENDER_MESSAGE_ID_RE = re.compile(r"@swift\.generated$", re.IGNORECASE)

# Instantly relays via SMTP AUTH from AWS EC2 (us-east-1, "compute-1") into
# the connected inbox's own provider — visible in the Received header chain.
_INSTANTLY_RECEIVED_RE = re.compile(r"\bec2-[\d-]+\.compute-1\.amazonaws\.com\b", re.IGNORECASE)

# Instantly appends a short uppercase alphanumeric tracking code to the
# subject, e.g. "Ed, are you free? | 83CMDKB DHFGPG3".
_INSTANTLY_SUBJECT_CODE_RE = re.compile(r"\|\s*[A-Z0-9]{5,10}\s+[A-Z0-9]{5,10}\s*$")


def classify_sent_message(message: Message, body_text: str = "") -> Tuple[str, str]:
    """Return ``(source, match_reason)`` for one message from a Sent folder.

    Checked positive-match-first (strongest signal wins), never by
    exclusion — see module docstring. ``match_reason`` names which signal
    fired, stored alongside the classification so a human can sanity-check
    *why* a message landed where it did without re-deriving the heuristic.
    """
    # str(): a header can come back as a non-string, truthy Header object —
    # see bounce.py's looks_like_bounce() for the same guard and why it's
    # needed on every header read, not just the one that happened to crash.
    subject = str(message.get("Subject") or "")
    received_headers = " ".join(str(v) for v in message.get_all("Received") or [])
    message_id = str(message.get("Message-Id") or "")

    if _EXTENDER_PIXEL_RE.search(body_text or ""):
        return SOURCE_EXTENDER, "pixel"
    if _INSTANTLY_RECEIVED_RE.search(received_headers):
        return SOURCE_INSTANTLY, "received_ec2"
    if _INSTANTLY_SUBJECT_CODE_RE.search(subject):
        return SOURCE_INSTANTLY, "subject_code"
    if _EXTENDER_MESSAGE_ID_RE.search(message_id):
        return SOURCE_EXTENDER, "message_id"
    return SOURCE_UNRECOGNIZED, "no_match"
