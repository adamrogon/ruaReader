"""Module 2 — per-domain DNS verification.

Checks SPF, DMARC, DKIM, and MX once a day and stores a timestamped row per
run, so the history of a record is visible rather than just its current value.
A domain that quietly lost its DKIM record last Tuesday is exactly the kind of
thing this is meant to catch.

The SPF check does the real work: it walks every ``include:`` and ``redirect=``
recursively and counts DNS-querying terms against the RFC 7208 limit of 10.
Exceeding that limit makes SPF evaluate to ``permerror`` at the receiver, which
in practice means silent authentication failure.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import dns.exception
import dns.rdatatype
import dns.resolver

from ..config import Domain, Settings, load_domains
from ..i18n import Nested
from ..storage import Database, DnsRepository, IngestionRunRepository, get_database

logger = logging.getLogger(__name__)

STREAM = "dns"

# RFC 7208 §4.6.4 — hard cap on DNS-querying terms during SPF evaluation.
SPF_LOOKUP_LIMIT = 10
# Warn before the limit is actually hit; adding one more sender is common.
SPF_LOOKUP_WARN_AT = 8

# Mechanisms and modifiers that cost a DNS lookup.
_LOOKUP_TERMS = ("include:", "a:", "mx:", "ptr:", "exists:", "redirect=")
_BARE_LOOKUP_TERMS = ("a", "mx", "ptr")


def _resolver(nameservers: Optional[Sequence[str]] = None, timeout: float = 5.0) -> dns.resolver.Resolver:
    resolver = dns.resolver.Resolver()
    if nameservers:
        resolver.nameservers = list(nameservers)
    resolver.timeout = timeout
    resolver.lifetime = timeout
    return resolver


def _query_txt(name: str, resolver: dns.resolver.Resolver) -> List[str]:
    """All TXT records for a name, reassembled from their string chunks."""
    try:
        answers = resolver.resolve(name, "TXT")
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return []
    except (dns.resolver.NoNameservers, dns.exception.Timeout) as exc:
        raise RuntimeError(f"DNS lookup failed for {name}: {exc}") from exc

    records = []
    for rdata in answers:
        # Long TXT records are split into 255-byte chunks that must be joined.
        parts = [chunk.decode("utf-8", errors="replace") for chunk in rdata.strings]
        records.append("".join(parts))
    return records


def _split_terms(record: str) -> List[str]:
    return [t for t in record.split() if t]


def count_spf_lookups(
    domain: str,
    resolver: dns.resolver.Resolver,
    _seen: Optional[Set[str]] = None,
    _depth: int = 0,
) -> Tuple[int, List[Dict[str, Any]], List[Tuple[str, Dict[str, Any]]]]:
    """Recursively count DNS-querying terms in a domain's SPF record.

    Returns ``(lookup_count, include_tree, errors)``, where each error is a
    ``(message_key, params)`` pair rather than a finished sentence — see
    :mod:`deliverability.i18n`. The tree is stored so the dashboard can show
    *which* include is expensive, not just that the total is too high.
    """
    seen = _seen if _seen is not None else set()
    errors: List[Tuple[str, Dict[str, Any]]] = []
    tree: List[Dict[str, Any]] = []
    count = 0

    if domain in seen:
        errors.append(("dns.error.include_loop", {"domain": domain}))
        return count, tree, errors
    if _depth > 10:
        errors.append(("dns.error.nesting_too_deep", {}))
        return count, tree, errors
    seen.add(domain)

    try:
        records = [r for r in _query_txt(domain, resolver) if r.lower().startswith("v=spf1")]
    except RuntimeError as exc:
        # A raw resolver error is technical, language-neutral text — passed
        # through as a param rather than translated, same as an SPF record's
        # own content.
        errors.append(("dns.error.resolver", {"error": str(exc)}))
        return count, tree, errors

    if not records:
        if _depth > 0:
            errors.append(("dns.error.include_missing", {"domain": domain}))
        return count, tree, errors
    if len(records) > 1 and _depth == 0:
        errors.append(("dns.error.multiple_records", {}))

    for term in _split_terms(records[0]):
        lowered = term.lower().lstrip("+-~?")

        if lowered.startswith("include:") or lowered.startswith("redirect="):
            separator = ":" if lowered.startswith("include:") else "="
            target = term.split(separator, 1)[1].strip() if separator in term else ""
            count += 1
            if not target:
                continue
            sub_count, sub_tree, sub_errors = count_spf_lookups(target, resolver, seen, _depth + 1)
            count += sub_count
            errors.extend(sub_errors)
            tree.append(
                {
                    "target": target,
                    "kind": "include" if separator == ":" else "redirect",
                    "lookups": sub_count + 1,
                    "children": sub_tree,
                }
            )
        elif lowered.startswith(("a:", "mx:", "ptr:", "exists:")):
            count += 1
        elif lowered in _BARE_LOOKUP_TERMS:
            # Bare "a" / "mx" / "ptr" resolve against the current domain.
            count += 1

    return count, tree, errors


def check_spf(domain: str, resolver: dns.resolver.Resolver) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "spf_present": False,
        "spf_record": None,
        "spf_valid": None,
        "spf_lookup_count": None,
        "spf_lookup_limit": SPF_LOOKUP_LIMIT,
        "spf_includes": None,
        "spf_error": None,
        # Structured (key, params) form of the same problems, consumed by
        # build_warnings() to produce a translated flag. Stripped before
        # storage — see check_domain() — since it has no matching DB column.
        "spf_error_details": [],
    }
    try:
        records = [r for r in _query_txt(domain, resolver) if r.lower().startswith("v=spf1")]
    except RuntimeError as exc:
        result["spf_error"] = str(exc)
        result["spf_error_details"] = [("dns.error.resolver", {"error": str(exc)})]
        return result

    if not records:
        result["spf_error"] = "No SPF record published."
        return result

    result["spf_present"] = True
    result["spf_record"] = records[0]

    errors: List[Tuple[str, Dict[str, Any]]] = []
    if len(records) > 1:
        errors.append(("dns.error.multiple_records", {}))

    terms = _split_terms(records[0])
    if not any(t.lower() in ("-all", "~all", "?all", "+all") for t in terms):
        errors.append(("dns.error.no_all", {}))
    if "+all" in [t.lower() for t in terms]:
        errors.append(("dns.error.plus_all", {}))

    count, tree, walk_errors = count_spf_lookups(domain, resolver)
    errors.extend(walk_errors)

    result["spf_lookup_count"] = count
    result["spf_includes"] = tree
    # English text kept for the DB column / CLI output, built from the same
    # structured entries so the two never drift apart.
    from ..i18n import translate

    result["spf_error"] = " ".join(translate(key, "en", **params) for key, params in errors) or None
    result["spf_error_details"] = errors

    invalidating = {"dns.error.multiple_records", "dns.error.include_missing", "dns.error.include_loop"}
    result["spf_valid"] = count <= SPF_LOOKUP_LIMIT and not any(key in invalidating for key, _ in errors)
    return result


def check_dmarc(domain: str, resolver: dns.resolver.Resolver) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "dmarc_present": False,
        "dmarc_record": None,
        "dmarc_policy": None,
        "dmarc_subdomain_policy": None,
        "dmarc_pct": None,
        "dmarc_rua": None,
        "dmarc_error": None,
    }
    try:
        records = [r for r in _query_txt(f"_dmarc.{domain}", resolver) if r.lower().startswith("v=dmarc1")]
    except RuntimeError as exc:
        result["dmarc_error"] = str(exc)
        return result

    if not records:
        result["dmarc_error"] = "No DMARC record published."
        return result

    result["dmarc_present"] = True
    result["dmarc_record"] = records[0]

    tags: Dict[str, str] = {}
    for part in records[0].split(";"):
        if "=" in part:
            key, value = part.split("=", 1)
            tags[key.strip().lower()] = value.strip()

    result["dmarc_policy"] = tags.get("p")
    result["dmarc_subdomain_policy"] = tags.get("sp")
    try:
        result["dmarc_pct"] = int(tags["pct"]) if "pct" in tags else 100
    except ValueError:
        result["dmarc_pct"] = None

    rua = tags.get("rua", "")
    result["dmarc_rua"] = [a.strip() for a in rua.split(",") if a.strip()] if rua else []

    errors = []
    if not result["dmarc_policy"]:
        errors.append("DMARC record has no 'p=' policy tag.")
    if not result["dmarc_rua"]:
        errors.append("DMARC record has no 'rua=' address, so no aggregate reports will be sent.")
    result["dmarc_error"] = " ".join(errors) or None
    return result


def check_dkim(domain: str, selectors: Sequence[str], resolver: dns.resolver.Resolver) -> List[Dict[str, Any]]:
    """Look up each configured selector.

    Selectors cannot be discovered from DNS, which is why they come from
    config — an empty list here means "not checked", not "not present".
    """
    results = []
    for selector in selectors:
        entry: Dict[str, Any] = {"selector": selector, "present": False, "record": None, "error": None}
        name = f"{selector}._domainkey.{domain}"
        try:
            records = [r for r in _query_txt(name, resolver) if "p=" in r or r.lower().startswith("v=dkim1")]
            if records:
                entry["present"] = True
                # Truncated so a 2048-bit key does not dominate the stored row.
                entry["record"] = records[0][:400]
                if "p=" in records[0] and records[0].split("p=", 1)[1].strip(" ;") == "":
                    entry["error"] = "Selector exists but its public key is empty — the key has been revoked."
            else:
                entry["error"] = f"No DKIM record at {name}."
        except RuntimeError as exc:
            entry["error"] = str(exc)
        results.append(entry)
    return results


def check_mx(domain: str, resolver: dns.resolver.Resolver) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    try:
        answers = resolver.resolve(domain, "MX")
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return [], "No MX records published — this domain cannot receive mail, including bounces."
    except (dns.resolver.NoNameservers, dns.exception.Timeout) as exc:
        return [], f"MX lookup failed: {exc}"

    records = sorted(
        ({"preference": int(r.preference), "host": str(r.exchange).rstrip(".")} for r in answers),
        key=lambda r: r["preference"],
    )
    return records, None


def _warning(severity: str, title_key: str, message_key: str, **params: Any) -> Dict[str, Any]:
    """One warning entry: a title/message key pair plus their shared params.

    Kept as keys rather than finished text so the dashboard can render it in
    whichever language the current request asked for — see
    :mod:`deliverability.i18n` and :meth:`deliverability.health.Flag.localize`.
    """
    return {
        "severity": severity,
        "title_key": title_key,
        "title_params": dict(params),
        "message_key": message_key,
        "message_params": dict(params),
    }


def build_warnings(check: Dict[str, Any], dkim_results: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Turn raw check results into plain-language warnings.

    Each warning carries a severity the dashboard sorts by and a message key
    explaining what it means for sending, rather than only naming the record.
    """
    warnings: List[Dict[str, Any]] = []

    if not check.get("spf_present"):
        warnings.append(_warning("critical", "flag.dns.no_spf.title", "flag.dns.no_spf.message"))
    else:
        count = check.get("spf_lookup_count") or 0
        if count > SPF_LOOKUP_LIMIT:
            warnings.append(
                _warning(
                    "critical",
                    "flag.dns.spf_over_limit.title",
                    "flag.dns.spf_over_limit.message",
                    count=count,
                    limit=SPF_LOOKUP_LIMIT,
                )
            )
        elif count >= SPF_LOOKUP_WARN_AT:
            warnings.append(
                _warning(
                    "warning",
                    "flag.dns.spf_near_limit.title",
                    "flag.dns.spf_near_limit.message",
                    count=count,
                    limit=SPF_LOOKUP_LIMIT,
                )
            )
        # Every structural SPF problem (multiple records, no all-mechanism,
        # +all, a broken include) gets its own warning under one shared title,
        # rather than being joined into a single opaque sentence.
        for key, params in check.get("spf_error_details") or []:
            warnings.append(
                {
                    "severity": "warning",
                    "title_key": "flag.dns.spf_error.title",
                    "title_params": {},
                    "message_key": key,
                    "message_params": params,
                }
            )

    if not check.get("dmarc_present"):
        warnings.append(_warning("critical", "flag.dns.no_dmarc.title", "flag.dns.no_dmarc.message"))
    else:
        if check.get("dmarc_present") and not check.get("dmarc_policy"):
            warnings.append(
                _warning(
                    "critical", "flag.dns.dmarc_no_policy_tag.title", "flag.dns.dmarc_no_policy_tag.message"
                )
            )
        if not check.get("dmarc_rua"):
            warnings.append(
                _warning("critical", "flag.dns.dmarc_no_rua.title", "flag.dns.dmarc_no_rua.message")
            )
        if check.get("dmarc_policy") == "none":
            warnings.append(
                _warning("info", "flag.dns.dmarc_p_none.title", "flag.dns.dmarc_p_none.message")
            )
        pct = check.get("dmarc_pct")
        if pct is not None and pct < 100:
            warnings.append(
                _warning("warning", "flag.dns.dmarc_pct.title", "flag.dns.dmarc_pct.message", pct=pct)
            )

    missing = [d["selector"] for d in dkim_results if not d["present"]]
    if dkim_results and missing:
        selector_word_key = (
            "flag.dns.dkim_missing.selector_word_plural"
            if len(missing) > 1
            else "flag.dns.dkim_missing.selector_word_singular"
        )
        warnings.append(
            {
                "severity": "critical" if len(missing) == len(dkim_results) else "warning",
                "title_key": "flag.dns.dkim_missing.title",
                "title_params": {"selectors": ", ".join(missing)},
                "message_key": "flag.dns.dkim_missing.message",
                # Resolved by translate()'s one-level Nested lookup, since the
                # plural/singular wording is itself language-dependent.
                "message_params": {"selector_word": Nested(selector_word_key)},
            }
        )
    revoked = [d["selector"] for d in dkim_results if d.get("error") and "revoked" in d["error"]]
    if revoked:
        warnings.append(
            _warning(
                "critical",
                "flag.dns.dkim_revoked.title",
                "flag.dns.dkim_revoked.message",
                selectors=", ".join(revoked),
            )
        )

    if not check.get("mx_records"):
        warnings.append(_warning("critical", "flag.dns.no_mx.title", "flag.dns.no_mx.message"))

    return warnings


def check_domain(domain: Domain, resolver: Optional[dns.resolver.Resolver] = None) -> Dict[str, Any]:
    """Run every DNS check for one domain and return a storable row."""
    resolver = resolver or _resolver()
    check: Dict[str, Any] = {"domain": domain.name, "checked_at": dt.datetime.now(dt.timezone.utc)}

    check.update(check_spf(domain.name, resolver))
    check.update(check_dmarc(domain.name, resolver))

    dkim_results = check_dkim(domain.name, domain.dkim_selectors, resolver)
    check["dkim_results"] = dkim_results

    mx_records, mx_error = check_mx(domain.name, resolver)
    check["mx_records"] = mx_records
    check["mx_error"] = mx_error

    check["warnings"] = build_warnings(check, dkim_results)

    # spf_error_details exists only to carry structured errors from check_spf()
    # into build_warnings(); it has no column and must not reach the insert.
    # Its content is already preserved twice over — as English text in
    # spf_error, and as translated warnings in warnings.
    check.pop("spf_error_details", None)
    return check


def run(
    settings: Optional[Settings] = None,
    database: Optional[Database] = None,
    domains: Optional[Sequence[Domain]] = None,
    nameservers: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Check every configured domain, recording the run for healthchecks."""
    settings = settings or Settings.from_env()
    database = database or get_database(settings)
    domains = domains if domains is not None else load_domains()

    repository = DnsRepository(database, settings.project_id)
    runs = IngestionRunRepository(database, settings.project_id)
    run_id = runs.start(STREAM)

    resolver = _resolver(nameservers)
    checked = 0
    failures: List[str] = []
    detail: Dict[str, Any] = {}

    for domain in domains:
        try:
            check = check_domain(domain, resolver)
            repository.insert_check(check)
            checked += 1
            detail[domain.name] = {
                "warnings": len(check["warnings"]),
                "spf_lookups": check.get("spf_lookup_count"),
                "dmarc_policy": check.get("dmarc_policy"),
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception("DNS check failed for %s", domain.name)
            failures.append(f"{domain.name}: {exc}")
            detail[domain.name] = {"error": str(exc)}

    status = "error" if failures and checked == 0 else "ok"
    runs.finish(
        run_id,
        status=status,
        items_seen=len(domains),
        items_ingested=checked,
        error="; ".join(failures) or None,
        detail=detail,
    )
    return {"status": status, "checked": checked, "failures": failures, "detail": detail}
