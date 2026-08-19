"""Module 4 — DNSBL checks for the IPs a domain sends from.

Sending IPs are derived from each domain's own DNS: ``ip4:``/``ip6:`` terms in
the SPF record (following includes) plus the A records of its MX hosts. Those
are then checked against the DNSBLs bundled with ``pydnsbl``.

One caveat is handled explicitly: several major lists — Spamhaus in particular —
refuse queries that arrive from public resolvers such as 8.8.8.8, and return a
non-answer rather than a verdict. Treating that as "not listed" would be
actively misleading, so providers that failed to answer are recorded separately
and surfaced instead of being folded into a clean result.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import logging
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import dns.exception
import dns.resolver
from pydnsbl import DNSBLIpChecker

from ..config import Domain, Settings, load_domains
from ..storage import BlacklistRepository, Database, IngestionRunRepository, get_database
from .dns_check import _query_txt, _resolver

logger = logging.getLogger(__name__)

STREAM = "dnsbl"

# Large cloud senders publish enormous SPF ranges; checking every address in a
# /16 is neither useful nor polite to the DNSBL operators.
MAX_IPS_PER_NETWORK = 8
MAX_IPS_PER_DOMAIN = 40


def _expand_network(cidr: str) -> List[str]:
    """Expand a CIDR to individual addresses, bounded.

    Networks larger than the bound are skipped rather than sampled: a handful of
    arbitrary addresses out of a /16 tells you nothing reliable about whether
    your actual sending IP is listed.
    """
    try:
        network = ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return []
    if network.num_addresses == 1:
        return [str(network.network_address)]
    if network.num_addresses > MAX_IPS_PER_NETWORK:
        return []
    return [str(addr) for addr in network.hosts()] or [str(network.network_address)]


def collect_spf_ips(
    domain: str,
    resolver: dns.resolver.Resolver,
    _seen: Optional[Set[str]] = None,
    _depth: int = 0,
) -> Set[str]:
    """Collect ip4:/ip6: addresses from a domain's SPF, following includes."""
    seen = _seen if _seen is not None else set()
    if domain in seen or _depth > 10:
        return set()
    seen.add(domain)

    addresses: Set[str] = set()
    try:
        records = [r for r in _query_txt(domain, resolver) if r.lower().startswith("v=spf1")]
    except RuntimeError:
        return addresses
    if not records:
        return addresses

    for term in records[0].split():
        lowered = term.lower().lstrip("+-~?")
        if lowered.startswith(("ip4:", "ip6:")):
            addresses.update(_expand_network(term.split(":", 1)[1]))
        elif lowered.startswith("include:") or lowered.startswith("redirect="):
            separator = ":" if lowered.startswith("include:") else "="
            target = term.split(separator, 1)[1].strip()
            if target:
                addresses |= collect_spf_ips(target, resolver, seen, _depth + 1)
    return addresses


def collect_mx_ips(domain: str, resolver: dns.resolver.Resolver) -> Set[str]:
    """Resolve the domain's MX hosts to A records."""
    addresses: Set[str] = set()
    try:
        mx_answers = resolver.resolve(domain, "MX")
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers, dns.exception.Timeout):
        return addresses

    for mx in mx_answers:
        host = str(mx.exchange).rstrip(".")
        for record_type in ("A", "AAAA"):
            try:
                for rdata in resolver.resolve(host, record_type):
                    addresses.add(str(rdata))
            except (
                dns.resolver.NXDOMAIN,
                dns.resolver.NoAnswer,
                dns.resolver.NoNameservers,
                dns.exception.Timeout,
            ):
                continue
    return addresses


def sending_ips_for_domain(
    domain: str, resolver: Optional[dns.resolver.Resolver] = None
) -> List[Tuple[str, str]]:
    """Return ``(ip, source)`` pairs to check for one domain."""
    resolver = resolver or _resolver()
    pairs: List[Tuple[str, str]] = []

    spf_ips = collect_spf_ips(domain, resolver)
    mx_ips = collect_mx_ips(domain, resolver)

    for ip in sorted(spf_ips):
        pairs.append((ip, "spf"))
    for ip in sorted(mx_ips - spf_ips):
        pairs.append((ip, "mx"))

    if len(pairs) > MAX_IPS_PER_DOMAIN:
        logger.info("Domain %s yielded %d IPs; checking the first %d", domain, len(pairs), MAX_IPS_PER_DOMAIN)
        pairs = pairs[:MAX_IPS_PER_DOMAIN]
    return pairs


def check_ip(
    checker: DNSBLIpChecker,
    ip: str,
    source: str,
    domain: str,
    checked_at: Optional[dt.datetime] = None,
) -> Dict[str, Any]:
    """Check one IP and build a storable row.

    ``checked_at`` identifies the check round and must be identical for every
    IP in a run: :meth:`BlacklistRepository.latest_per_domain` selects the rows
    matching a domain's most recent timestamp, so per-IP timestamps would leave
    only one IP visible and could hide a listed one.
    """
    row: Dict[str, Any] = {
        "domain": domain,
        "ip": ip,
        "ip_source": source,
        "checked_at": checked_at or dt.datetime.now(dt.timezone.utc),
        "listed": False,
        "listed_by": [],
        "providers_checked": 0,
        "detail_text": None,
        "error": None,
    }
    try:
        result = checker.check(ip)
    except Exception as exc:  # noqa: BLE001
        row["error"] = f"DNSBL check failed: {exc}"
        return row

    detected = dict(result.detected_by or {})
    failed = [str(p) for p in (result.failed_providers or [])]

    row["listed"] = bool(result.blacklisted)
    row["listed_by"] = sorted(detected.keys())
    row["providers_checked"] = len(result.providers or []) - len(failed)

    if row["listed"]:
        categories = sorted({c for reasons in detected.values() for c in reasons if c != "unknown"})
        row["detail_text"] = (
            f"Listed by {len(detected)} DNSBL(s): {', '.join(row['listed_by'])}."
            + (f" Reported categories: {', '.join(categories)}." if categories else "")
        )

    if failed:
        # Recorded rather than ignored: a list that did not answer has not
        # cleared the IP, and Spamhaus refusing public resolvers is common.
        row["error"] = (
            f"{len(failed)} provider(s) did not return an answer and were not counted: "
            f"{', '.join(sorted(failed)[:6])}"
            + ("…" if len(failed) > 6 else "")
            + ". Lists such as Spamhaus refuse queries from public DNS resolvers; "
            "use a local resolver for a complete result."
        )
    return row


def run(
    settings: Optional[Settings] = None,
    database: Optional[Database] = None,
    domains: Optional[Sequence[Domain]] = None,
    nameservers: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Check every configured domain's sending IPs, recording the run."""
    settings = settings or Settings.from_env()
    database = database or get_database(settings)
    domains = domains if domains is not None else load_domains()

    repository = BlacklistRepository(database, settings.project_id)
    runs = IngestionRunRepository(database, settings.project_id)
    run_id = runs.start(STREAM)

    resolver = _resolver(nameservers)
    checker = DNSBLIpChecker()

    # One timestamp for the whole round so every IP of a domain is retrievable
    # together — see the note in check_ip().
    checked_at = dt.datetime.now(dt.timezone.utc)

    rows: List[Dict[str, Any]] = []
    failures: List[str] = []
    detail: Dict[str, Any] = {}

    for domain in domains:
        try:
            pairs = sending_ips_for_domain(domain.name, resolver)
            if not pairs:
                detail[domain.name] = {"ips": 0, "note": "No sending IPs found in SPF or MX."}
                continue
            domain_rows = [check_ip(checker, ip, source, domain.name, checked_at) for ip, source in pairs]
            rows.extend(domain_rows)
            detail[domain.name] = {
                "ips": len(domain_rows),
                "listed": sum(1 for r in domain_rows if r["listed"]),
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception("DNSBL check failed for %s", domain.name)
            failures.append(f"{domain.name}: {exc}")
            detail[domain.name] = {"error": str(exc)}

    stored = repository.insert_many(rows)
    listed_total = sum(1 for r in rows if r["listed"])

    # "partial" matters as much as "error" once there are many domains: one
    # domain failing its blacklist check among thirty must not read as "ok"
    # just because the other twenty-nine succeeded.
    if not failures:
        status = "ok"
    elif len(failures) == len(domains):
        status = "error"
    else:
        status = "partial"
    runs.finish(
        run_id,
        status=status,
        items_seen=len(rows),
        items_ingested=stored,
        error="; ".join(failures) or None,
        detail=detail,
    )
    return {"status": status, "checked": len(rows), "listed": listed_total, "detail": detail, "errors": failures}
