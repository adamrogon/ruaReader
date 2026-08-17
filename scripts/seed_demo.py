"""Populate the database with synthetic data for the domains in config/domains.yml.

This exists so the dashboard can be exercised — and its urgency ordering
checked — without waiting for real reports to arrive. It writes to the same
tables the real ingestion writes to.

Run with `--reset` to clear existing rows first. Never point this at a database
holding real data you want to keep.

    python scripts/seed_demo.py --reset
"""

from __future__ import annotations

import argparse
import datetime as dt
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deliverability.classify.bounce_codes import classify_bounce  # noqa: E402
from deliverability.config import Settings, load_domains  # noqa: E402
from deliverability.storage import (  # noqa: E402
    BlacklistRepository,
    BounceRepository,
    DmarcRepository,
    DnsRepository,
    IngestionRunRepository,
    get_database,
)
from deliverability.storage.schema import (  # noqa: E402
    blacklist_checks,
    bounces,
    dmarc_records,
    dmarc_reports,
    dns_checks,
    ingestion_runs,
)

RNG = random.Random(20260817)

# Each demo domain gets a deliberately different failure mode so the urgency
# ordering has something to sort.
PROFILES = {
    "outreach-alpha.com": {
        "story": "healthy",
        "compliance": 0.99,
        "volume": 320,
        "hard_bounces": 2,
        "soft_bounces": 1,
        "sender_blocks": 0,
        "blacklisted": False,
        "spf_lookups": 4,
        "dkim_ok": True,
    },
    "outreach-beta.com": {
        # The emergency case: Microsoft is refusing mail outright.
        "story": "sender blocked at Microsoft",
        "compliance": 0.91,
        "volume": 280,
        "hard_bounces": 6,
        "soft_bounces": 3,
        "sender_blocks": 4,
        "block_esp": "Microsoft",
        "block_code": "5.7.708",
        "blacklisted": False,
        "spf_lookups": 6,
        "dkim_ok": True,
    },
    "outreach-gamma.com": {
        # Listed on a blacklist, DNS otherwise fine.
        "story": "blacklisted IP",
        "compliance": 0.88,
        "volume": 240,
        "hard_bounces": 9,
        "soft_bounces": 5,
        "sender_blocks": 0,
        "blacklisted": True,
        "spf_lookups": 5,
        "dkim_ok": True,
    },
    "outreach-delta.com": {
        # SPF over the RFC limit -> silent authentication failure.
        "story": "SPF over the lookup limit",
        "compliance": 0.72,
        "volume": 300,
        "hard_bounces": 4,
        "soft_bounces": 2,
        "sender_blocks": 0,
        "blacklisted": False,
        "spf_lookups": 13,
        "dkim_ok": True,
        "worst_esp": "Google",
    },
    "outreach-epsilon.com": {
        # List quality problem rather than a reputation problem.
        "story": "high hard bounce rate",
        "compliance": 0.96,
        "volume": 210,
        "hard_bounces": 28,
        "soft_bounces": 6,
        "sender_blocks": 0,
        "blacklisted": False,
        "spf_lookups": 7,
        "dkim_ok": True,
    },
    "outreach-zeta.com": {
        # Newly added, DKIM selector never published.
        "story": "DKIM selector missing",
        "compliance": 0.94,
        "volume": 90,
        "hard_bounces": 1,
        "soft_bounces": 1,
        "sender_blocks": 0,
        "blacklisted": False,
        "spf_lookups": 3,
        "dkim_ok": False,
    },
}

ESP_MIX = [
    ("Google", "google.com", 0.55),
    ("Microsoft", "Enterprise Outlook", 0.28),
    ("Yahoo", "Yahoo! Inc.", 0.10),
    ("Seznam", "seznam.cz", 0.04),
    ("Other", "mailbox.org", 0.03),
]

FORWARDER_HOSTS = [
    "srs0.forward.hostedemail.com",
    "mail-out.improvmx.com",
    "fwd.mailrelay.example",
]


def reset(database) -> None:
    with database.connect() as conn:
        for table in (dmarc_records, dmarc_reports, dns_checks, bounces, blacklist_checks, ingestion_runs):
            conn.execute(table.delete())
    print("Cleared existing rows.")


def seed_rua(database, settings, domains, days: int) -> int:
    repo = DmarcRepository(database, settings.project_id)
    now = dt.datetime.now(dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    stored = 0

    for domain in domains:
        profile = PROFILES.get(domain.name)
        if not profile:
            continue

        for day_offset in range(days, 0, -1):
            begin = now - dt.timedelta(days=day_offset)
            end = begin + dt.timedelta(days=1)
            daily_total = max(10, int(profile["volume"] / days * RNG.uniform(0.6, 1.4)))

            for esp, org_name, share in ESP_MIX:
                esp_volume = int(daily_total * share)
                if esp_volume <= 0:
                    continue

                # One domain fails disproportionately at a single provider, so
                # the per-ESP view has something real to surface.
                rate = profile["compliance"]
                if profile.get("worst_esp") == esp:
                    rate = max(0.2, rate - 0.35)

                # Forwarded mail is carved out first, then the pass/fail split
                # is taken over what remains. Rounded rather than truncated:
                # at the 10-40/day volumes these domains actually send, int()
                # would floor every forwarded count to zero and the category
                # would never appear.
                forwarded = max(0, int(round(esp_volume * RNG.uniform(0.02, 0.08))))
                evaluated = max(0, esp_volume - forwarded)
                passed = int(round(evaluated * rate))
                failed = max(0, evaluated - passed)

                records = []
                if passed:
                    records.append(
                        {
                            "source_ip": f"209.85.220.{RNG.randint(2, 250)}",
                            "source_host": "mail-sor.google.com",
                            "source_esp": "Google",
                            "message_count": passed,
                            "disposition": "none",
                            "dkim_aligned": True,
                            "spf_aligned": True,
                            "dkim_result": "pass",
                            "spf_result": "pass",
                            "header_from": domain.name,
                            "envelope_from": domain.name,
                            "evaluation": "pass",
                            "evaluation_reason": "DKIM aligned and passing and SPF aligned and passing.",
                        }
                    )
                if forwarded:
                    host = RNG.choice(FORWARDER_HOSTS)
                    records.append(
                        {
                            "source_ip": f"185.12.{RNG.randint(1, 250)}.{RNG.randint(1, 250)}",
                            "source_host": host,
                            "source_esp": "Forwarder",
                            "message_count": forwarded,
                            "disposition": "none",
                            "dkim_aligned": True,
                            "spf_aligned": False,
                            "dkim_result": "pass",
                            "spf_result": "fail",
                            "header_from": domain.name,
                            "envelope_from": domain.name,
                            "evaluation": "forwarded",
                            "evaluation_reason": (
                                f"SPF failed but DKIM passed and {host!r} matches a known forwarder "
                                f"pattern — typical of relayed mail, not a sending fault."
                            ),
                        }
                    )
                if failed:
                    records.append(
                        {
                            "source_ip": f"45.{RNG.randint(1, 250)}.{RNG.randint(1, 250)}.{RNG.randint(1, 250)}",
                            "source_host": None,
                            "source_esp": "Unknown",
                            "message_count": failed,
                            "disposition": "none" if RNG.random() > 0.3 else "quarantine",
                            "dkim_aligned": False,
                            "spf_aligned": False,
                            "dkim_result": "fail",
                            "spf_result": "fail",
                            "header_from": domain.name,
                            "envelope_from": domain.name,
                            "evaluation": "failed",
                            "evaluation_reason": "Neither mechanism aligned: DKIM fail; SPF fail.",
                        }
                    )

                if not records:
                    continue

                report = {
                    "report_id": f"{esp.lower()}-{domain.name}-{begin:%Y%m%d}",
                    "org_name": org_name,
                    "org_email": f"dmarc@{org_name}",
                    "date_begin": begin,
                    "date_end": end,
                    "policy_domain": domain.name,
                    "policy_p": "none",
                    "policy_sp": "none",
                    "policy_pct": 100,
                    "policy_adkim": "r",
                    "policy_aspf": "r",
                    "raw_xml_path": None,
                    "source_mailbox": "rua-main",
                }
                if repo.report_exists(report["report_id"], org_name, begin, end):
                    continue
                repo.insert_report(report, records, esp)
                stored += 1
    return stored


def seed_bounces(database, settings, domains, days: int) -> int:
    repo = BounceRepository(database, settings.project_id)
    now = dt.datetime.now(dt.timezone.utc)
    rows = []

    hard_codes = ["5.1.1", "5.1.1", "5.1.2", "5.2.1", "5.4.4"]
    soft_codes = ["4.2.2", "4.7.1", "4.3.2"]
    esp_domains = [("Google", "gmail.com"), ("Microsoft", "outlook.com"), ("Yahoo", "yahoo.com"), ("Seznam", "seznam.cz")]

    for domain in domains:
        profile = PROFILES.get(domain.name)
        if not profile:
            continue

        def add(code, esp, rcpt_domain, index, diagnostic, max_age_hours=None):
            klass, reason = classify_bounce(code, diagnostic)
            rows.append(
                {
                    "sending_domain": domain.name,
                    "mailbox_name": f"outreach-{domain.name.split('.')[0]}",
                    "message_id": f"<{klass}-{index}-{domain.name}@demo>",
                    "received_at": now - dt.timedelta(hours=RNG.uniform(1, max_age_hours or days * 24)),
                    "status_code": code,
                    "smtp_code": "550" if (code or "5").startswith("5") else "452",
                    "bounce_class": klass,
                    "bounce_reason": reason,
                    "diagnostic_code": diagnostic,
                    "reporting_mta": f"dns; mx.{rcpt_domain}",
                    "remote_mta": f"dns; mx.{rcpt_domain}",
                    "recipient_hash": f"{abs(hash((domain.name, index, code))):064x}"[:64],
                    "recipient_domain": rcpt_domain,
                    "recipient_esp": esp,
                    "parse_ok": code is not None,
                    "raw_text": diagnostic,
                    "subject": "Undeliverable: Quick question",
                }
            )

        for i in range(profile["hard_bounces"]):
            esp, rcpt = RNG.choice(esp_domains)
            code = RNG.choice(hard_codes)
            add(code, esp, rcpt, i, f"smtp; 550 {code} The email account does not exist.")

        for i in range(profile["soft_bounces"]):
            esp, rcpt = RNG.choice(esp_domains)
            code = RNG.choice(soft_codes)
            add(code, esp, rcpt, 1000 + i, f"smtp; 452 {code} Temporary problem, try again later.")

        for i in range(profile.get("sender_blocks", 0)):
            esp = profile.get("block_esp", "Microsoft")
            rcpt = {"Microsoft": "outlook.com", "Google": "gmail.com", "Yahoo": "yahoo.com"}.get(esp, "outlook.com")
            code = profile.get("block_code", "5.7.1")
            # Kept inside the last 48h — a block is the signal the dashboard is
            # meant to surface today, not a historical curiosity.
            add(
                code,
                esp,
                rcpt,
                2000 + i,
                f"smtp; 550 {code} Access denied, traffic not accepted from this IP",
                max_age_hours=48,
            )

        # One unparseable bounce so the tolerant-parser path is represented.
        if profile["story"] != "healthy":
            klass, reason = classify_bounce(None, "Wiadomosc nie zostala dostarczona.")
            rows.append(
                {
                    "sending_domain": domain.name,
                    "mailbox_name": f"outreach-{domain.name.split('.')[0]}",
                    "message_id": f"<unparsed-{domain.name}@demo>",
                    "received_at": now - dt.timedelta(hours=RNG.uniform(1, days * 24)),
                    "status_code": None,
                    "smtp_code": None,
                    "bounce_class": klass,
                    "bounce_reason": reason,
                    "diagnostic_code": "Wiadomosc nie zostala dostarczona. Skontaktuj sie z administratorem.",
                    "reporting_mta": None,
                    "remote_mta": None,
                    "recipient_hash": None,
                    "recipient_domain": None,
                    "recipient_esp": "Unknown",
                    "parse_ok": False,
                    "raw_text": "Wiadomosc nie zostala dostarczona. Skontaktuj sie z administratorem.",
                    "subject": "Undeliverable",
                }
            )

    existing = repo.existing_message_ids(rows[0]["mailbox_name"], []) if rows else set()
    fresh = [r for r in rows if r["message_id"] not in existing]
    return repo.insert_many(fresh)


def seed_dns(database, settings, domains) -> int:
    from deliverability.ingest.dns_check import build_warnings

    repo = DnsRepository(database, settings.project_id)
    now = dt.datetime.now(dt.timezone.utc)
    count = 0

    for domain in domains:
        profile = PROFILES.get(domain.name)
        if not profile:
            continue

        selectors = domain.dkim_selectors or ["google"]
        dkim_results = [
            {
                "selector": s,
                "present": profile["dkim_ok"],
                "record": "v=DKIM1; k=rsa; p=MIIBIjANBgkq..." if profile["dkim_ok"] else None,
                "error": None if profile["dkim_ok"] else f"No DKIM record at {s}._domainkey.{domain.name}.",
            }
            for s in selectors
        ]

        lookups = profile["spf_lookups"]
        check = {
            "domain": domain.name,
            "checked_at": now - dt.timedelta(hours=RNG.uniform(1, 12)),
            "spf_present": True,
            "spf_record": "v=spf1 include:_spf.google.com include:sendgrid.net ~all",
            "spf_valid": lookups <= 10,
            "spf_lookup_count": lookups,
            "spf_lookup_limit": 10,
            "spf_includes": [
                {"target": "_spf.google.com", "kind": "include", "lookups": 4, "children": []},
                {"target": "sendgrid.net", "kind": "include", "lookups": max(1, lookups - 4), "children": []},
            ],
            "spf_error": None,
            "dmarc_present": True,
            "dmarc_record": f"v=DMARC1; p=none; rua=mailto:dmarc@{domain.name}",
            "dmarc_policy": "none",
            "dmarc_subdomain_policy": None,
            "dmarc_pct": 100,
            "dmarc_rua": [f"mailto:dmarc@{domain.name}"],
            "dmarc_error": None,
            "dkim_results": dkim_results,
            "mx_records": [{"preference": 1, "host": "aspmx.l.google.com"}],
            "mx_error": None,
        }
        check["warnings"] = build_warnings(check, dkim_results)
        repo.insert_check(check)
        count += 1
    return count


def seed_blacklist(database, settings, domains) -> int:
    repo = BlacklistRepository(database, settings.project_id)
    now = dt.datetime.now(dt.timezone.utc)
    rows = []

    # One timestamp for the whole round, matching what blacklist.run() does —
    # per-IP timestamps would leave only one IP per domain retrievable.
    checked_at = now - dt.timedelta(hours=4)

    for domain in domains:
        profile = PROFILES.get(domain.name)
        if not profile:
            continue
        listed = profile["blacklisted"]
        for index in range(2):
            ip = f"198.51.100.{10 + index}"
            rows.append(
                {
                    "domain": domain.name,
                    "ip": ip,
                    "ip_source": "spf" if index == 0 else "mx",
                    "checked_at": checked_at,
                    "listed": listed and index == 0,
                    "listed_by": ["zen.spamhaus.org", "b.barracudacentral.org"] if (listed and index == 0) else [],
                    "providers_checked": 41,
                    "detail_text": (
                        "Listed by 2 DNSBL(s): b.barracudacentral.org, zen.spamhaus.org."
                        if (listed and index == 0)
                        else None
                    ),
                    "error": None,
                }
            )
    return repo.insert_many(rows)


def seed_runs(database, settings) -> None:
    """Record ingestion runs, leaving one stream deliberately stale."""
    repo = IngestionRunRepository(database, settings.project_id)
    now = dt.datetime.now(dt.timezone.utc)

    with database.connect() as conn:
        for stream, hours_ago, status in [
            ("rua", 3, "ok"),
            ("bounce", 1, "ok"),
            ("dns", 6, "ok"),
            # Deliberately older than the 48h threshold so the staleness state
            # is visible in the dashboard.
            ("dnsbl", 74, "ok"),
        ]:
            started = now - dt.timedelta(hours=hours_ago)
            conn.execute(
                ingestion_runs.insert().values(
                    project_id=settings.project_id,
                    stream=stream,
                    started_at=started,
                    finished_at=started + dt.timedelta(minutes=2),
                    status=status,
                    items_seen=10,
                    items_ingested=10,
                    error=None,
                    detail={"seeded": True},
                )
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="Delete existing rows first.")
    parser.add_argument("--days", type=int, default=14, help="Days of history to generate.")
    args = parser.parse_args()

    settings = Settings.from_env()
    database = get_database(settings)
    domains = load_domains()

    if args.reset:
        reset(database)

    print(f"Seeding {len(domains)} domains over {args.days} days…")
    print(f"  DMARC reports : {seed_rua(database, settings, domains, args.days)}")
    print(f"  Bounces       : {seed_bounces(database, settings, domains, args.days)}")
    print(f"  DNS checks    : {seed_dns(database, settings, domains)}")
    print(f"  DNSBL rows    : {seed_blacklist(database, settings, domains)}")
    seed_runs(database, settings)
    print("  Ingestion runs: 4 (dnsbl deliberately stale at 74h)")
    print("\nDone. Start the dashboard with: uvicorn deliverability.web.app:app --reload")


if __name__ == "__main__":
    main()
