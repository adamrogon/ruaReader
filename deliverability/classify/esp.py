"""Mapping providers to a small set of stable ESP labels.

Reporting organisations name themselves inconsistently across reports
("google.com", "Google Inc.", "Enterprise Outlook", "Outlook.com",
"Yahoo! Inc."). Collapsing them to a fixed label keeps the per-ESP breakdown
readable and stable over time.

Regional providers matter here — Seznam for Czech recipients, WP/Onet and
Interia for Polish ones — so they get their own labels rather than falling into
"Other".
"""

from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

GOOGLE = "Google"
MICROSOFT = "Microsoft"
YAHOO = "Yahoo"
SEZNAM = "Seznam"
WP = "WP/O2"
ONET = "Onet"
INTERIA = "Interia"
MAILRU = "Mail.ru"
PROTON = "Proton"
ZOHO = "Zoho"
APPLE = "Apple"
AOL = "AOL"
FASTMAIL = "Fastmail"
GMX = "GMX/United Internet"
COMCAST = "Comcast"
OTHER = "Other"
UNKNOWN = "Unknown"

# Ordered — first match wins, so put narrower patterns first.
_ORG_PATTERNS: Tuple[Tuple[str, str], ...] = (
    (r"google|gmail", GOOGLE),
    (r"outlook|hotmail|microsoft|enterprise\s*outlook|office\s*365|msn\b", MICROSOFT),
    (r"yahoo|oath|verizon\s*media", YAHOO),
    (r"\baol\b", AOL),
    (r"seznam", SEZNAM),
    (r"wp\.pl|o2\.pl|wirtualna", WP),
    (r"onet", ONET),
    (r"interia", INTERIA),
    (r"mail\.ru|corp\.mail", MAILRU),
    (r"proton", PROTON),
    (r"zoho", ZOHO),
    (r"apple|icloud|me\.com", APPLE),
    (r"fastmail|messagingengine", FASTMAIL),
    (r"gmx|united[\s-]*internet|web\.de|1&1|ionos", GMX),
    (r"comcast", COMCAST),
)

_COMPILED_ORG = tuple((re.compile(p, re.IGNORECASE), label) for p, label in _ORG_PATTERNS)

# Sending-side classification, matched against reverse DNS of the source IP.
_SOURCE_PATTERNS: Tuple[Tuple[str, str], ...] = (
    (r"google|gmail|1e100\.net", GOOGLE),
    (r"outlook|hotmail|microsoft|protection\.outlook", MICROSOFT),
    (r"amazonses|amazonaws", "Amazon SES"),
    (r"sendgrid", "SendGrid"),
    (r"mailgun", "Mailgun"),
    (r"mailjet", "Mailjet"),
    (r"sparkpost", "SparkPost"),
    (r"postmark", "Postmark"),
    (r"mandrill|mailchimp", "Mailchimp"),
    (r"zoho", ZOHO),
    (r"improvmx|forward|^srs|^fwd\.|redirect\.", "Forwarder"),
)

_COMPILED_SOURCE = tuple((re.compile(p, re.IGNORECASE), label) for p, label in _SOURCE_PATTERNS)


def esp_from_org_name(org_name: Optional[str]) -> str:
    """Label the RECEIVING provider from a report's ``org_name``.

    This is the grouping used by the dashboard's per-ESP view: the provider
    that generated the report is the one making a judgement about your mail.
    """
    if not org_name:
        return UNKNOWN
    text = org_name.strip()
    for pattern, label in _COMPILED_ORG:
        if pattern.search(text):
            return label
    return OTHER


def esp_from_source(source_host: Optional[str], source_ip: Optional[str] = None) -> str:
    """Label the SENDING source from its reverse DNS, when available."""
    if not source_host:
        return UNKNOWN
    text = source_host.strip()
    for pattern, label in _COMPILED_SOURCE:
        if pattern.search(text):
            return label
    return OTHER


def esp_from_email_domain(domain: Optional[str]) -> str:
    """Label a recipient's provider from the domain part of their address.

    Used for bounces, where there is no reporting organisation — only the
    address that rejected the message.
    """
    if not domain:
        return UNKNOWN
    text = domain.strip().lower()
    # Recipients on custom domains hosted by Google/Microsoft cannot be
    # identified from the domain alone; those land in OTHER and are separated
    # later by the rejecting MTA's hostname if the DSN carries one.
    for pattern, label in _COMPILED_ORG:
        if pattern.search(text):
            return label
    return OTHER


def esp_from_mta(mta_host: Optional[str]) -> str:
    """Label a provider from the hostname of the MTA that issued a rejection."""
    if not mta_host:
        return UNKNOWN
    text = mta_host.strip().lower()
    for pattern, label in _COMPILED_SOURCE:
        if pattern.search(text):
            return label
    for pattern, label in _COMPILED_ORG:
        if pattern.search(text):
            return label
    return OTHER


# Colours are assigned in the dashboard; this ordering controls display order
# so the providers that matter most for cold outreach appear first.
ESP_DISPLAY_ORDER: Dict[str, int] = {
    GOOGLE: 0,
    MICROSOFT: 1,
    YAHOO: 2,
    SEZNAM: 3,
    WP: 4,
    ONET: 5,
    INTERIA: 6,
    APPLE: 7,
    PROTON: 8,
    GMX: 9,
    MAILRU: 10,
    AOL: 11,
    ZOHO: 12,
    FASTMAIL: 13,
    COMCAST: 14,
    OTHER: 98,
    UNKNOWN: 99,
}
