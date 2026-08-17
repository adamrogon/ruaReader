"""Bounce classification.

Three ordinary outcomes plus one that matters far more than the others:

* ``hard``          — 5.x.x, the address is not deliverable.
* ``soft``          — 4.x.x, temporary; retry may succeed.
* ``sender_block``  — 5.1.8 and 5.7.x. Not "this address is wrong" but "we are
  refusing mail from you". A single one of these can mean the whole domain or
  tenant is blocked, which is why it is separated out and treated as the
  highest-priority alert in the tool.
* ``unknown``       — nothing parseable; the raw text is kept regardless.

The distinction is the point of the module: 200 hard bounces from a stale list
is a data-quality problem, while one 5.7.1 from Google is an emergency.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple


class BounceClass:
    HARD = "hard"
    SOFT = "soft"
    SENDER_BLOCK = "sender_block"
    UNKNOWN = "unknown"


# Enhanced status code, e.g. "5.7.1". Kept deliberately loose — the subject
# field can run to three digits at some providers (5.7.708 at Microsoft).
_STATUS_RE = re.compile(r"\b([245])\.(\d{1,3})\.(\d{1,3})\b")

# Reputation/blocklist language that appears when a provider rejects on sender
# reputation without using a 5.7.x code.
_BLOCK_PHRASES = re.compile(
    r"blocked|blacklist|blocklist|spamhaus|barracuda|reputation|"
    r"not\s+authorized|denied\s+access|banned|abuse|"
    r"unsolicited|bulk\s+mail|spam\s*(?:content|message|source)?|"
    r"rejected\s+due\s+to|policy\s+reasons|sender\s+verification",
    re.IGNORECASE,
)

# Per-code explanations, written for someone deciding whether to keep sending
# from a domain today.
_CODE_EXPLANATIONS = {
    "5.1.1": "The mailbox does not exist at this provider. Normal list decay — remove the address.",
    "5.1.2": "The recipient's domain does not exist or has no mail servers. Check for a typo in the domain.",
    "5.1.6": "The mailbox has moved and no forwarding address is available.",
    "5.1.8": (
        "The provider rejected the SENDER address, not the recipient. This usually means the "
        "sending domain or account is not accepted — check that the mailbox still exists and "
        "that the domain has not been suspended."
    ),
    "5.1.10": "The recipient address does not resolve and the provider treats it as non-existent.",
    "5.2.1": "The mailbox is disabled and not accepting messages.",
    "5.2.2": "The recipient's mailbox is full. Often temporary in practice despite the 5.x.x code.",
    "5.4.1": "No answer from the recipient's mail server — the domain may be misconfigured or gone.",
    "5.4.4": "The recipient's domain has no reachable mail server (DNS or MX problem on their side).",
    "5.7.0": (
        "The provider refused the message on policy grounds. Frequently sender reputation or "
        "authentication rather than anything about the recipient."
    ),
    "5.7.1": (
        "Delivery refused by policy — the most common sender-block code. The provider is "
        "declining mail from this sender, IP, or domain. Treat as a live deliverability problem, "
        "not a bad address."
    ),
    "5.7.5": "Cryptographic failure during the session, often a TLS negotiation problem.",
    "5.7.9": "The provider requires authentication the sending server did not supply.",
    "5.7.13": "The sending account is disabled or suspended by its own provider.",
    "5.7.23": "The message failed SPF checks at the recipient and was rejected outright.",
    "5.7.25": "The sending IP's reverse DNS does not resolve, and the provider requires that it does.",
    "5.7.26": (
        "The message failed multiple authentication checks (typically both SPF and DKIM). "
        "Fix authentication before sending more from this domain."
    ),
    "5.7.28": "The provider is rate-limiting or blocking this IP for suspicious sending volume.",
    "5.7.508": "The provider saw abnormal traffic from this sender and is refusing it.",
    "5.7.509": "The message failed the provider's DMARC evaluation and was rejected.",
    "5.7.511": "The sender is on the recipient's block list.",
    "5.7.606": "The sending IP is blocked by the provider's reputation system.",
    "5.7.708": "Access denied — the provider has blocked this IP range for reputation reasons.",
    "4.2.2": "The recipient's mailbox is full. Temporary; may clear on its own.",
    "4.3.2": "The recipient's server is not accepting messages right now. Temporary.",
    "4.4.1": "No response from the recipient's server. Temporary connectivity problem.",
    "4.4.2": "The connection to the recipient's server dropped mid-delivery.",
    "4.7.0": (
        "Temporarily deferred on policy or reputation grounds. Not yet a block, but repeated "
        "4.7.x from one provider often precedes one."
    ),
    "4.7.1": (
        "Temporarily refused by policy — commonly greylisting or rate limiting. Watch for this "
        "turning into 5.7.x."
    ),
    "4.7.28": "The provider is throttling this IP for unusual sending patterns.",
}


def extract_status_code(text: Optional[str]) -> Optional[str]:
    """Pull an enhanced status code out of arbitrary DSN text."""
    if not text:
        return None
    match = _STATUS_RE.search(text)
    return f"{match.group(1)}.{match.group(2)}.{match.group(3)}" if match else None


def classify_bounce(
    status_code: Optional[str],
    diagnostic_code: Optional[str] = None,
    raw_text: Optional[str] = None,
) -> Tuple[str, str]:
    """Classify a bounce into a class and a plain-language reason.

    Falls back to scanning the diagnostic text when no status code was parsed,
    because plenty of real-world bounces are not standards-compliant.

    Returns ``(bounce_class, reason)``.
    """
    haystack = " ".join(filter(None, (status_code, diagnostic_code, raw_text)))
    code = status_code or extract_status_code(diagnostic_code) or extract_status_code(raw_text)

    if code:
        klass, subject, detail = code.split(".")
        explanation = _CODE_EXPLANATIONS.get(code)

        # Sender-block detection, exactly as specified: 5.1.8 and the whole
        # 5.7.x family.
        if klass == "5" and (code == "5.1.8" or subject == "7"):
            return (
                BounceClass.SENDER_BLOCK,
                explanation
                or (
                    f"{code} — the provider refused mail from this sender on policy or "
                    f"reputation grounds. This is about the sending domain, not the recipient."
                ),
            )

        if klass == "5":
            # A permanent failure whose text is clearly about blocking rather
            # than a missing mailbox still means the domain is in trouble.
            if _BLOCK_PHRASES.search(diagnostic_code or raw_text or ""):
                return (
                    BounceClass.SENDER_BLOCK,
                    f"{code} — reported as a permanent failure, but the provider's message "
                    f"describes a block or reputation problem rather than an unknown address.",
                )
            return (
                BounceClass.HARD,
                explanation or f"{code} — permanent failure. The address is not deliverable.",
            )

        if klass == "4":
            reason = explanation or f"{code} — temporary failure. A retry may still get through."
            if subject == "7":
                # Not a sender_block by the agreed rule, but worth naming
                # because sustained 4.7.x is how a block usually starts.
                reason += " Repeated 4.7.x from one provider is an early warning of a block."
            return BounceClass.SOFT, reason

    # No usable code. Judge on wording alone rather than discarding the record.
    if _BLOCK_PHRASES.search(haystack):
        return (
            BounceClass.SENDER_BLOCK,
            "No status code could be parsed, but the message text describes a block, "
            "blocklist, or reputation rejection. Review the raw text.",
        )

    return (
        BounceClass.UNKNOWN,
        "No status code could be parsed from this message. The full text has been stored "
        "for manual review.",
    )


def smtp_class(status_code: Optional[str]) -> Optional[str]:
    """The leading digit of a status code ('5', '4', '2')."""
    if not status_code:
        return None
    return status_code.split(".")[0] if "." in status_code else status_code[:1]
