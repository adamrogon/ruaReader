"""Turning four data streams into one ranked answer.

The dashboard has to answer "which domain can I safely send from today", so the
domain list is ordered by urgency rather than alphabetically. Individual
indicators are kept separate and shown separately — there is deliberately no
single composite health score.

Urgency is a sort key, not a score: it decides row order and nothing else. The
weights encode the operational judgement that an active sender block outranks a
blacklist listing, which outranks a broken DNS record, which outranks a poor
compliance rate.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .classify.esp import ESP_DISPLAY_ORDER
from .config import Domain, Settings, load_domains
from .i18n import Nested, translate
from .storage import (
    BlacklistRepository,
    BounceRepository,
    Database,
    DismissedFlagRepository,
    DmarcRepository,
    DnsRepository,
    IngestionRunRepository,
    get_database,
)

# A stream with no fresh data for longer than this is reported as stale rather
# than quietly showing an old number as if it were current.
STALENESS_HOURS = 48

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2, "ok": 3}

# Urgency weights. A sender block at a MAJOR provider is the one signal that
# means "stop sending from this domain now". The same code from a small,
# unknown mailbox host is often just their local quirk (a whitelist scheme,
# a badly-set-up filter) and does not warrant the same alarm — so it becomes
# a lesser warning instead of a critical block. See MAJOR_ESPS below.
URGENCY_SENDER_BLOCK = 1000
URGENCY_SENDER_BLOCK_MINOR = 150
URGENCY_BLACKLISTED = 800
URGENCY_DNS_CRITICAL = 600
URGENCY_HIGH_HARD_BOUNCE = 400
URGENCY_LOW_COMPLIANCE = 300
URGENCY_DNS_WARNING = 150
URGENCY_NO_DATA = 90
URGENCY_SOFT_BOUNCE = 50

# Providers whose sender-block verdict genuinely matters at scale. A 5.7.x
# from these means real trouble; from anywhere else, treat it as a signal
# worth showing but not worth halting a domain over.
MAJOR_ESPS = frozenset(
    {"Google", "Microsoft", "Yahoo", "Apple", "Proton",
     "Seznam", "WP/O2", "Onet", "Interia", "Mail.ru", "GMX/United Internet"}
)

# Thresholds for the derived warnings.
HARD_BOUNCE_RATE_WARN = 0.05
HARD_BOUNCE_RATE_CRITICAL = 0.10
COMPLIANCE_WARN = 0.95
COMPLIANCE_CRITICAL = 0.85

# DNSBLs that receivers actually act on. A listing is reported as critical
# either way, but the distinction belongs in the explanation: lists such as
# UCEProtect level 2/3 list whole netblocks or ASNs rather than individual
# senders, so a hit there usually says something about the hosting provider
# rather than about this domain's sending.
HIGH_SIGNAL_DNSBLS = frozenset(
    {
        "zen.spamhaus.org",
        "sbl.spamhaus.org",
        "xbl.spamhaus.org",
        "pbl.spamhaus.org",
        "b.barracudacentral.org",
        "bl.spamcop.net",
        "cbl.abuseat.org",
        "psbl.surriel.com",
    }
)
NETBLOCK_WIDE_DNSBLS = frozenset(
    {"dnsbl-2.uceprotect.net", "dnsbl-3.uceprotect.net", "bogons.cymru.com", "korea.services.net"}
)


@dataclass
class Flag:
    """One problem, with an explanation written for a human.

    Title and message are stored as translation keys plus their format
    parameters rather than finished sentences — the language a user has
    selected is a property of a request, not of the data, so text is resolved
    once, at render time, via :meth:`localize`.

    ``urgency_weight`` is this flag's own contribution to the domain's sort
    position, held per-flag so a domain's total is just the sum of its flags.
    """

    severity: str
    title_key: str
    message_key: str
    source: str  # 'bounce' | 'dnsbl' | 'dns' | 'rua' | 'ingestion'
    # Stable id for "this kind of flag on this domain", set explicitly at each
    # Flag(...) call site rather than derived — a generic derivation (e.g.
    # title_key + esp) would silently break for the one flag type that
    # legitimately repeats per ESP (sender_block) vs. the rest, which don't.
    # Used to persist a dismissal across ingestion runs; see
    # DismissedFlagRepository.
    fingerprint: str = ""
    title_params: Dict[str, Any] = field(default_factory=dict)
    message_params: Dict[str, Any] = field(default_factory=dict)
    esp: Optional[str] = None
    urgency_weight: int = 0

    def localize(self, lang: str) -> Dict[str, Any]:
        """Resolve title/message to text in ``lang``.

        Any :class:`~deliverability.i18n.Nested` value in the params dicts is
        itself translated first — see that helper for why.
        """
        return {
            "severity": self.severity,
            "title": translate(self.title_key, lang, **self.title_params),
            "message": translate(self.message_key, lang, **self.message_params),
            "source": translate(f"source.{self.source}", lang),
            "esp": self.esp,
            "fingerprint": self.fingerprint,
        }


@dataclass
class DomainStatus:
    domain: str
    urgency: int = 0
    severity: str = "ok"
    flags: List[Flag] = field(default_factory=list)
    # Flags matching a fingerprint the user dismissed for this domain. Kept
    # out of `flags` (and so out of urgency/severity) but still available to
    # show in a collapsed "dismissed" list.
    dismissed_flags: List[Flag] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    esp_rows: List[Dict[str, Any]] = field(default_factory=list)

    def localize(self, lang: str) -> Dict[str, Any]:
        """Resolve every flag and the one-sentence headline to ``lang``.

        The web layer calls this once per request on each status before
        handing it to a template; nothing upstream needs to know which
        language is active.
        """
        localized_flags = [f.localize(lang) for f in self.flags]
        return {
            "domain": self.domain,
            "urgency": self.urgency,
            "severity": self.severity,
            "headline": _headline_text(self, localized_flags, lang),
            "flags": localized_flags,
            "dismissed_flags": [f.localize(lang) for f in self.dismissed_flags],
            "metrics": self.metrics,
            "esp_rows": self.esp_rows,
        }


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _as_aware(value: Any) -> Optional[dt.datetime]:
    """SQLite hands back naive datetimes; treat them as UTC."""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    return None


def ingestion_health(
    database: Database, settings: Settings, streams: Sequence[str] = ("rua", "bounce", "dns", "dnsbl")
) -> List[Dict[str, Any]]:
    """Freshness of each ingestion stream.

    A stream that has never run and one that stopped running both need to be
    visible; neither should look like "no problems found". Returns message
    keys/params rather than finished text — call :func:`localize_ingestion_health`
    to resolve them for display.
    """
    runs = IngestionRunRepository(database, settings.project_id)
    last_success = runs.last_success_per_stream()
    last_run = runs.last_run_per_stream()
    now = _utcnow()

    report: List[Dict[str, Any]] = []
    for stream in streams:
        success_at = _as_aware(last_success.get(stream))
        latest = last_run.get(stream) or {}
        latest_status = latest.get("status")
        latest_error = latest.get("error")
        label_key = f"stream.{stream}"

        if success_at is None:
            state = "never_run"
            age_hours = None
            message_key, message_params = "ingest.never_run", {"label": Nested(label_key)}
        else:
            age_hours = (now - success_at).total_seconds() / 3600
            if age_hours > STALENESS_HOURS:
                state = "stale"
                message_key = "ingest.stale"
                message_params = {"label": Nested(label_key), "hours": age_hours, "limit": STALENESS_HOURS}
            else:
                state = "ok"
                message_key, message_params = "ingest.ok", {"hours": age_hours}

        if latest_status == "error" and state == "ok":
            state = "erroring"
            message_key, message_params = "ingest.erroring", {"error": latest_error or "unknown error"}
        elif latest_status == "partial" and state == "ok":
            # Some mailboxes/domains in the last run failed while others
            # succeeded — data is flowing (so this isn't "stale"), but part
            # of it silently isn't, which "ok" would hide completely.
            state = "partial"
            message_key, message_params = "ingest.partial", {"error": latest_error or "unknown error"}

        report.append(
            {
                "stream": stream,
                "label_key": label_key,
                "state": state,
                "last_success": success_at,
                "age_hours": age_hours,
                "message_key": message_key,
                "message_params": message_params,
                "last_status": latest_status,
                "error": latest_error,
            }
        )
    return report


def localize_ingestion_health(rows: Sequence[Dict[str, Any]], lang: str) -> List[Dict[str, Any]]:
    """Resolve :func:`ingestion_health` rows to display text in ``lang``."""
    out = []
    for row in rows:
        state_key = f"ingest.state.{row['state']}"
        state_params = {"hours": row["age_hours"]} if row["age_hours"] is not None else {}
        out.append(
            {
                **row,
                "label": translate(row["label_key"], lang),
                "message": translate(row["message_key"], lang, **row["message_params"]),
                "state_label": translate(state_key, lang, **state_params),
            }
        )
    return out


def _compliance_from_rows(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Compliance rate with forwarded mail excluded from the denominator.

    Forwarded messages are neither successes nor failures of the domain's own
    configuration, so counting them either way distorts the rate.
    """
    passed = sum(r["messages"] for r in rows if r["evaluation"] == "pass")
    failed = sum(r["messages"] for r in rows if r["evaluation"] == "failed")
    forwarded = sum(r["messages"] for r in rows if r["evaluation"] == "forwarded")
    evaluated = passed + failed
    return {
        "passed": passed,
        "failed": failed,
        "forwarded": forwarded,
        "total": passed + failed + forwarded,
        "evaluated": evaluated,
        "compliance": (passed / evaluated) if evaluated else None,
    }


def _recompute(status: DomainStatus) -> None:
    """Derive urgency and severity from the domain's flags."""
    status.urgency = sum(f.urgency_weight for f in status.flags)
    status.severity = status.flags[0].severity if status.flags else "ok"
    if status.severity == "info" and status.urgency < URGENCY_NO_DATA:
        status.severity = "ok"


def build_domain_status(
    domain_name: str,
    dns_row: Optional[Dict[str, Any]],
    compliance: Dict[str, Any],
    esp_rows: Sequence[Dict[str, Any]],
    bounce_classes: Dict[str, int],
    sender_blocks: Sequence[Dict[str, Any]],
    blacklist_rows: Sequence[Dict[str, Any]],
    dismissed_fingerprints: Optional[Sequence[str]] = None,
) -> DomainStatus:
    """Assemble one domain's status from the four streams."""
    status = DomainStatus(domain=domain_name)
    flags: List[Flag] = []

    # --- Sender blocks -----------------------------------------------------
    # One flag per ESP that rejected us. Major providers get a critical flag
    # (their opinion is worth halting a domain over); small/unknown hosts get
    # a warning-level "minor" variant with a lighter message — same block,
    # much less alarming presentation.
    if sender_blocks:
        by_esp: Dict[str, int] = {}
        for row in sender_blocks:
            by_esp[row.get("recipient_esp") or "Unknown"] = by_esp.get(row.get("recipient_esp") or "Unknown", 0) + 1

        def _weight_for(esp_name: str) -> tuple:
            """severity, urgency_weight, title_key, message_key for this ESP."""
            if esp_name in MAJOR_ESPS:
                return "critical", URGENCY_SENDER_BLOCK, "flag.sender_block.title", "flag.sender_block.message"
            return "warning", URGENCY_SENDER_BLOCK_MINOR, "flag.sender_block.title_minor", "flag.sender_block.message_minor"

        # Rank so the loudest (most rejections at a major ESP) leads. Major
        # first, then by count within each tier — a single rejection at
        # Google outranks ten at a random hoster.
        ranked = sorted(
            by_esp.items(),
            key=lambda kv: (kv[0] not in MAJOR_ESPS, -kv[1]),
        )
        for esp, count in ranked:
            severity, weight, title_key, message_key = _weight_for(esp)
            codes = sorted({
                row.get("status_code") for row in sender_blocks
                if (row.get("recipient_esp") or "Unknown") == esp and row.get("status_code")
            })
            codes_suffix = Nested("flag.sender_block.codes_suffix", codes=", ".join(codes)) if codes else Nested(None)

            flags.append(
                Flag(
                    severity=severity,
                    title_key=title_key,
                    title_params={"esp": esp, "count": count},
                    message_key=message_key,
                    message_params={"esp": esp, "codes_suffix": codes_suffix},
                    source="bounce",
                    esp=esp,
                    urgency_weight=weight,
                    fingerprint=f"sender_block:{esp}",
                )
            )

    # --- Blacklist ---------------------------------------------------------
    listed = [row for row in blacklist_rows if row.get("listed")]
    if listed:
        names = sorted({name for row in listed for name in (row.get("listed_by") or [])})
        ips = sorted({row["ip"] for row in listed})

        high_signal = sorted(n for n in names if n in HIGH_SIGNAL_DNSBLS)
        netblock_wide = sorted(n for n in names if n in NETBLOCK_WIDE_DNSBLS)

        if high_signal:
            detail = Nested("flag.blacklist.detail_high_signal", names=", ".join(high_signal))
        elif netblock_wide and len(netblock_wide) == len(names):
            # Worth showing, but chasing a delisting here is usually wasted
            # effort — the listing is about the hosting provider, not you.
            detail = Nested("flag.blacklist.detail_netblock_wide", names=", ".join(netblock_wide))
        else:
            detail = Nested("flag.blacklist.detail_mixed")

        flags.append(
            Flag(
                severity="critical",
                title_key="flag.blacklist.title",
                title_params={"ip_count": len(ips), "list_count": len(names)},
                message_key="flag.blacklist.message",
                message_params={
                    "ips": ", ".join(ips[:3]) + ("…" if len(ips) > 3 else ""),
                    "names": ", ".join(names[:4]) + ("…" if len(names) > 4 else ""),
                    "detail": detail,
                },
                source="dnsbl",
                urgency_weight=URGENCY_BLACKLISTED,
                fingerprint="blacklist",
            )
        )

    # --- DNS ---------------------------------------------------------------
    if dns_row:
        checked_at = _as_aware(dns_row.get("checked_at"))
        for warning in dns_row.get("warnings") or []:
            severity = warning.get("severity", "info")
            weight = (
                URGENCY_DNS_CRITICAL
                if severity == "critical"
                else (URGENCY_DNS_WARNING if severity == "warning" else 0)
            )
            flags.append(
                Flag(
                    severity=severity,
                    title_key=warning.get("title_key", "flag.dns.spf_error.title"),
                    title_params=warning.get("title_params") or {},
                    message_key=warning.get("message_key", "flag.dns.spf_error.title"),
                    message_params=warning.get("message_params") or {},
                    source="dns",
                    urgency_weight=weight,
                    fingerprint=f"dns:{warning.get('title_key', 'unknown')}",
                )
            )

    # --- Bounces -----------------------------------------------------------
    hard = bounce_classes.get("hard", 0)
    soft = bounce_classes.get("soft", 0)
    unknown = bounce_classes.get("unknown", 0)
    blocks = bounce_classes.get("sender_block", 0)
    total_bounces = hard + soft + unknown + blocks

    sent_estimate = compliance.get("total") or 0
    hard_rate = (hard / sent_estimate) if sent_estimate else None

    if hard_rate is not None:
        if hard_rate >= HARD_BOUNCE_RATE_CRITICAL:
            flags.append(
                Flag(
                    severity="critical",
                    title_key="flag.bounce.hard_rate_critical.title",
                    title_params={"rate": hard_rate},
                    message_key="flag.bounce.hard_rate_critical.message",
                    message_params={"hard": hard, "sent": sent_estimate, "threshold": HARD_BOUNCE_RATE_CRITICAL},
                    source="bounce",
                    urgency_weight=URGENCY_HIGH_HARD_BOUNCE,
                    fingerprint="hard_bounce_rate_critical",
                )
            )
        elif hard_rate >= HARD_BOUNCE_RATE_WARN:
            flags.append(
                Flag(
                    severity="warning",
                    title_key="flag.bounce.hard_rate_warning.title",
                    title_params={"rate": hard_rate},
                    message_key="flag.bounce.hard_rate_warning.message",
                    message_params={"hard": hard, "sent": sent_estimate, "threshold": HARD_BOUNCE_RATE_WARN},
                    source="bounce",
                    urgency_weight=URGENCY_SOFT_BOUNCE,
                    fingerprint="hard_bounce_rate_warning",
                )
            )

    if unknown:
        flags.append(
            Flag(
                severity="info",
                title_key="flag.bounce.unparsed.title",
                title_params={"count": unknown},
                message_key="flag.bounce.unparsed.message",
                source="bounce",
                fingerprint="bounce_unparsed",
            )
        )

    # --- DMARC compliance --------------------------------------------------
    rate = compliance.get("compliance")
    if rate is not None:
        if rate < COMPLIANCE_CRITICAL:
            severity = "critical"
            weight = URGENCY_LOW_COMPLIANCE
        elif rate < COMPLIANCE_WARN:
            severity = "warning"
            weight = URGENCY_LOW_COMPLIANCE // 2
        else:
            severity = None
            weight = 0

        if severity:
            worst = _worst_esp(esp_rows)
            worst_suffix = Nested("flag.rua.low_compliance.worst_suffix", esp=worst) if worst else Nested(None)
            flags.append(
                Flag(
                    severity=severity,
                    title_key="flag.rua.low_compliance.title",
                    title_params={"rate": rate},
                    message_key="flag.rua.low_compliance.message",
                    message_params={
                        "failed": compliance["failed"],
                        "evaluated": compliance["evaluated"],
                        "worst_suffix": worst_suffix,
                        "forwarded": compliance["forwarded"],
                    },
                    source="rua",
                    esp=worst,
                    urgency_weight=weight,
                    fingerprint=f"low_compliance_{severity}",
                )
            )

    if compliance.get("total", 0) == 0:
        flags.append(
            Flag(
                severity="info",
                title_key="flag.rua.no_data.title",
                message_key="flag.rua.no_data.message",
                source="rua",
                urgency_weight=URGENCY_NO_DATA,
                fingerprint="no_data",
            )
        )

    # --- Roll up -----------------------------------------------------------
    dismissed = set(dismissed_fingerprints or ())
    active = [f for f in flags if f.fingerprint not in dismissed]
    hidden = [f for f in flags if f.fingerprint in dismissed]
    status.flags = sorted(active, key=lambda f: SEVERITY_ORDER.get(f.severity, 9))
    status.dismissed_flags = sorted(hidden, key=lambda f: SEVERITY_ORDER.get(f.severity, 9))
    # Urgency and severity are derived from the active (non-dismissed) flags
    # only — a dismissed blacklist hit should not keep the domain looking
    # critical.
    _recompute(status)

    status.metrics = {
        "messages": compliance.get("total", 0),
        "passed": compliance.get("passed", 0),
        "failed": compliance.get("failed", 0),
        "forwarded": compliance.get("forwarded", 0),
        "compliance": rate,
        "bounces_total": total_bounces,
        "bounces_hard": hard,
        "bounces_soft": soft,
        "bounces_unknown": unknown,
        "bounces_sender_block": blocks,
        "hard_bounce_rate": hard_rate,
        "blacklisted_ips": len({row["ip"] for row in listed}),
        "dmarc_policy": (dns_row or {}).get("dmarc_policy"),
        "spf_lookups": (dns_row or {}).get("spf_lookup_count"),
        "spf_lookup_limit": (dns_row or {}).get("spf_lookup_limit"),
        "dns_checked_at": (dns_row or {}).get("checked_at"),
    }
    status.esp_rows = _esp_summary(esp_rows)
    return status


def _worst_esp(esp_rows: Sequence[Dict[str, Any]]) -> Optional[str]:
    """The ESP seeing the most failures — the 'who has a problem' answer."""
    failures: Dict[str, int] = {}
    for row in esp_rows:
        if row["evaluation"] == "failed":
            esp = row.get("esp") or "Unknown"
            failures[esp] = failures.get(esp, 0) + (row.get("messages") or 0)
    return max(failures, key=failures.get) if failures else None


def _esp_summary(esp_rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Per-ESP totals for one domain, ready for the dashboard table."""
    by_esp: Dict[str, Dict[str, Any]] = {}
    for row in esp_rows:
        esp = row.get("esp") or "Unknown"
        entry = by_esp.setdefault(esp, {"esp": esp, "pass": 0, "failed": 0, "forwarded": 0})
        entry[row["evaluation"]] = entry.get(row["evaluation"], 0) + (row.get("messages") or 0)

    summary = []
    for esp, entry in by_esp.items():
        evaluated = entry["pass"] + entry["failed"]
        summary.append(
            {
                **entry,
                "total": entry["pass"] + entry["failed"] + entry["forwarded"],
                "evaluated": evaluated,
                "compliance": (entry["pass"] / evaluated) if evaluated else None,
            }
        )
    return sorted(summary, key=lambda e: (ESP_DISPLAY_ORDER.get(e["esp"], 50), -e["total"]))


def _headline_text(status: DomainStatus, localized_flags: List[Dict[str, Any]], lang: str) -> str:
    """One sentence answering 'can I send from this domain today'.

    Works from already-localized flag dicts (each with a translated
    ``title``) so composing "+N more" suffixes is just string concatenation
    in the caller's language, not a second round of key resolution.
    """
    if not localized_flags or status.severity == "ok":
        rate = status.metrics.get("compliance")
        if status.metrics.get("messages", 0) == 0:
            return translate("headline.no_data", lang)
        return (
            translate("headline.ok_with_rate", lang, rate=rate)
            if rate is not None
            else translate("headline.ok_no_rate", lang)
        )

    critical = [f for f in localized_flags if f["severity"] == "critical"]
    if critical:
        first = critical[0]
        extra = translate("headline.critical_more", lang, n=len(critical) - 1) if len(critical) > 1 else ""
        return f"{first['title']}{extra}"

    warnings = [f for f in localized_flags if f["severity"] == "warning"]
    if warnings:
        first = warnings[0]
        extra = translate("headline.warning_more", lang, n=len(warnings) - 1) if len(warnings) > 1 else ""
        return f"{first['title']}{extra}"
    return localized_flags[0]["title"]


def domain_statuses(
    settings: Optional[Settings] = None,
    database: Optional[Database] = None,
    domains: Optional[Sequence[Domain]] = None,
    window_days: int = 7,
) -> List[DomainStatus]:
    """Every configured domain, ordered by urgency (worst first)."""
    settings = settings or Settings.from_env()
    database = database or get_database(settings)
    domains = domains if domains is not None else load_domains()

    dmarc = DmarcRepository(database, settings.project_id)
    dns_repo = DnsRepository(database, settings.project_id)
    bounce_repo = BounceRepository(database, settings.project_id)
    blacklist_repo = BlacklistRepository(database, settings.project_id)
    dismissed_repo = DismissedFlagRepository(database, settings.project_id)
    dismissed_by_domain = dismissed_repo.all_by_domain()

    since = _utcnow() - dt.timedelta(days=window_days)

    compliance_rows = dmarc.compliance_by_domain(since)
    esp_rows = dmarc.esp_breakdown(since)
    bounce_rows = bounce_repo.counts_by_class(since)
    block_rows = bounce_repo.sender_blocks(since)
    dns_latest = dns_repo.latest_per_domain()
    blacklist_latest = blacklist_repo.latest_per_domain()

    def rows_for(rows: Sequence[Dict[str, Any]], name: str) -> List[Dict[str, Any]]:
        return [r for r in rows if r.get("domain") == name]

    statuses = []
    for domain in domains:
        bounce_classes = {
            r["bounce_class"]: r["count"] for r in bounce_rows if r.get("domain") == domain.name
        }
        status = build_domain_status(
            domain_name=domain.name,
            dns_row=dns_latest.get(domain.name),
            compliance=_compliance_from_rows(rows_for(compliance_rows, domain.name)),
            esp_rows=rows_for(esp_rows, domain.name),
            bounce_classes=bounce_classes,
            sender_blocks=rows_for(block_rows, domain.name),
            blacklist_rows=blacklist_latest.get(domain.name, []),
            dismissed_fingerprints=dismissed_by_domain.get(domain.name),
        )
        statuses.append(status)

    # Worst first; alphabetical only as a tie-break among equally healthy ones.
    return sorted(statuses, key=lambda s: (-s.urgency, s.domain))
