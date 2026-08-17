"""Separating forwarded mail from genuine authentication failures.

Mail that gets forwarded (mailing lists, `.forward` rules, ImprovMX-style
aliases) routinely breaks SPF: the forwarding host relays the message from an IP
that was never in the original domain's SPF record. DKIM usually survives,
because the signature covers the message rather than the path.

Counting those as failures makes a healthy domain look broken, so ``forwarded``
is a stored outcome alongside ``pass`` and ``failed`` — a value in the data
model, not a filter applied in the UI.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

# Hostname patterns that indicate a forwarding relay rather than an
# unauthorised sender. Matched against the reverse DNS of the source IP.
FORWARDER_PATTERNS: Tuple[str, ...] = (
    r"^srs\d*[.-]",          # Sender Rewriting Scheme relays
    r"srs\d*\.",             # ...also mid-hostname
    r"forward",              # *forward*, e.g. forward.mail.example
    r"^redirect\.",
    r"improvmx",
    r"^fwd\.",
    r"\.fwd\.",
)

_COMPILED = tuple(re.compile(p, re.IGNORECASE) for p in FORWARDER_PATTERNS)


class Evaluation:
    """Stored values for ``dmarc_records.evaluation``."""

    PASS = "pass"
    FAILED = "failed"
    FORWARDED = "forwarded"


def looks_like_forwarder(source_host: Optional[str]) -> bool:
    """True when a hostname matches a known forwarder naming pattern."""
    if not source_host:
        return False
    host = source_host.strip().lower().rstrip(".")
    return any(pattern.search(host) for pattern in _COMPILED)


def _passed(result: Optional[str]) -> bool:
    return (result or "").strip().lower() == "pass"


def classify_evaluation(
    dkim_result: Optional[str],
    spf_result: Optional[str],
    source_host: Optional[str],
    dkim_aligned: Optional[bool] = None,
    spf_aligned: Optional[bool] = None,
) -> Tuple[str, str]:
    """Classify one DMARC record.

    Returns ``(evaluation, reason)`` where evaluation is one of
    :class:`Evaluation` and reason is a short human-readable explanation stored
    alongside the record.

    DMARC itself passes when *either* mechanism passes **and** is aligned, so
    that is checked first. The forwarding rule then reclaims the specific
    SPF-fail/DKIM-pass-from-a-forwarder case, which would otherwise be counted
    as the domain's own traffic and skew its compliance rate.
    """
    dkim_ok = _passed(dkim_result)
    spf_ok = _passed(spf_result)

    # parsedmarc reports alignment separately; fall back to the raw result when
    # alignment is not available.
    dkim_ali = dkim_aligned if dkim_aligned is not None else dkim_ok
    spf_ali = spf_aligned if spf_aligned is not None else spf_ok

    is_forwarder = looks_like_forwarder(source_host)

    # The rule as specified: SPF broken, DKIM intact, relay looks like a
    # forwarder. Takes precedence over the plain pass/fail verdict so this
    # traffic can be excluded from compliance rates entirely.
    if not spf_ok and dkim_ok and is_forwarder:
        return (
            Evaluation.FORWARDED,
            f"SPF failed but DKIM passed and {source_host!r} matches a known "
            f"forwarder pattern — typical of relayed mail, not a sending fault.",
        )

    if (dkim_ok and dkim_ali) or (spf_ok and spf_ali):
        parts = []
        if dkim_ok and dkim_ali:
            parts.append("DKIM aligned and passing")
        if spf_ok and spf_ali:
            parts.append("SPF aligned and passing")
        return Evaluation.PASS, " and ".join(parts) + "."

    reasons = []
    reasons.append("DKIM passed but is not aligned" if dkim_ok else f"DKIM {dkim_result or 'missing'}")
    reasons.append("SPF passed but is not aligned" if spf_ok else f"SPF {spf_result or 'missing'}")
    suffix = (
        f" Source {source_host!r} looks like a forwarder, but DKIM did not "
        f"survive the relay, so this cannot be discounted as forwarding."
        if is_forwarder
        else ""
    )
    return Evaluation.FAILED, f"Neither mechanism aligned: {'; '.join(reasons)}.{suffix}"
