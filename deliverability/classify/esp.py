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
    (r"google|gmail|1e100\.net", GOOGLE),
    (r"outlook|hotmail|microsoft|enterprise\s*outlook|office\s*365|msn\b|protection\.outlook", MICROSOFT),
    (r"yahoo|oath|verizon\s*media", YAHOO),
    (r"\baol\b", AOL),
    (r"seznam", SEZNAM),
    (r"wp\.pl|o2\.pl|wirtualna", WP),
    (r"onet", ONET),
    (r"interia", INTERIA),
    (r"mail\.ru|corp\.mail", MAILRU),
    (r"proton", PROTON),
    (r"zoho", ZOHO),
    (r"apple|icloud|me\.com|mac\.com", APPLE),
    (r"fastmail|messagingengine", FASTMAIL),
    (r"gmx|united[\s-]*internet|web\.de|1&1|ionos|kundenserver", GMX),
    (r"comcast", COMCAST),
    # Extra European hosters/ESPs that show up regularly in cold-outreach
    # bounce streams; without these they fall into "Other" and make the
    # per-provider view less useful than it should be.
    (r"home\.pl|homepl", "home.pl"),
    (r"nazwa\.pl|serwer\.pl", "nazwa.pl"),
    (r"cyberfolks|smtp\.cyber", "cyberFolks"),
    (r"hekko|hetiner|linuxpl|mydevil", "Hekko/MyDevil"),
    (r"seohost", "SEOhost"),
    (r"ovh|ovhcloud", "OVH"),
    (r"all-?inkl|allinkl", "All-Inkl"),
    (r"hosteurope|host-europe", "HostEurope"),
    (r"strato", "Strato"),
    (r"mittwald", "Mittwald"),
    (r"cloudflare|email-routing", "Cloudflare"),
    (r"amazonses|amazonaws\.com", "Amazon SES"),
    (r"sendgrid", "SendGrid"),
    (r"mailgun", "Mailgun"),
    (r"mailjet", "Mailjet"),
    (r"postmark", "Postmark"),
    (r"sparkpost", "SparkPost"),
    (r"mailchimp|mandrill", "Mailchimp"),
    (r"mailerlite", "MailerLite"),
    (r"getresponse", "GetResponse"),
    (r"activecampaign", "ActiveCampaign"),
    (r"sitesell", "SiteSell"),
    (r"emaillabs\.net\.pl|emaillabs", "EmailLabs"),
    (r"freshmail", "FreshMail"),
    (r"woodpecker", "Woodpecker"),
    (r"instantly", "Instantly"),
    (r"smartlead", "Smartlead"),
    (r"apollo\.io|apolloio", "Apollo"),
    (r"reply\.io", "Reply.io"),
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


# In-process cache for MX-based classification. One lookup per unique recipient
# domain across an ingestion run; a batch of 500 bounces to 30 distinct domains
# is 30 DNS queries, not 500.
_MX_ESP_CACHE: Dict[str, str] = {}


def esp_from_mx(domain: Optional[str], timeout: float = 3.0) -> str:
    """Label a provider by looking up the recipient domain's MX record.

    Answers what pattern matching on the address string alone cannot:
    ``foo@custom-company.com`` gives no clue where the mail actually lives,
    but the MX often does (e.g. ``mx.google.com`` → Google, ``.protection.outlook``
    → Microsoft, ``mx.home.pl`` → home.pl). Falls back to :const:`OTHER` on
    lookup failure or when no MX host matches any known pattern — the caller
    then treats the result the same as any other unclassified case.

    Results are cached per process; safe to call repeatedly.
    """
    if not domain:
        return UNKNOWN
    key = domain.strip().lower()
    if not key:
        return UNKNOWN
    if key in _MX_ESP_CACHE:
        return _MX_ESP_CACHE[key]

    try:
        import dns.exception
        import dns.resolver

        resolver = dns.resolver.Resolver()
        resolver.timeout = timeout
        resolver.lifetime = timeout
        answers = resolver.resolve(key, "MX")
    except Exception:  # noqa: BLE001 — any DNS problem is a graceful OTHER
        _MX_ESP_CACHE[key] = OTHER
        return OTHER

    for rdata in answers:
        host = str(rdata.exchange).rstrip(".").lower()
        # esp_from_mta already knows the source and org patterns.
        label = esp_from_mta(host)
        if label not in (UNKNOWN, OTHER):
            _MX_ESP_CACHE[key] = label
            return label

    _MX_ESP_CACHE[key] = OTHER
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
