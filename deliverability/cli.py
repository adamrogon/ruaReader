"""Ingestion entry points.

    python -m deliverability.cli rua       # DMARC aggregate reports  (daily)
    python -m deliverability.cli dns       # SPF / DMARC / DKIM / MX  (daily)
    python -m deliverability.cli bounce    # bounces / NDRs           (hourly)
    python -m deliverability.cli dnsbl     # blacklist checks         (daily)
    python -m deliverability.cli daily     # rua + dns + dnsbl
    python -m deliverability.cli status    # print current state, no network

Each command records an ingestion run, which is what the dashboard's 48-hour
freshness check reads.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any, Dict

from .config import Settings
from .health import domain_statuses, ingestion_health
from .storage import get_database


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # parsedmarc is chatty at INFO and drowns out our own output.
    logging.getLogger("parsedmarc").setLevel(logging.WARNING)


def _print(result: Dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(json.dumps(result, indent=2, default=str))


def cmd_rua(args: argparse.Namespace) -> int:
    from .ingest import rua

    result = rua.run(since_days=args.since_days, offline=args.offline)
    _print(result, args.json)
    return 0 if result["status"] == "ok" else 1


def cmd_dns(args: argparse.Namespace) -> int:
    from .ingest import dns_check

    result = dns_check.run()
    _print(result, args.json)
    return 0 if result["status"] == "ok" else 1


def cmd_bounce(args: argparse.Namespace) -> int:
    from .ingest import bounce

    result = bounce.run(since_days=args.since_days)
    _print(result, args.json)
    return 0 if result["status"] == "ok" else 1


def cmd_dnsbl(args: argparse.Namespace) -> int:
    from .ingest import blacklist

    result = blacklist.run()
    _print(result, args.json)
    return 0 if result["status"] == "ok" else 1


def cmd_daily(args: argparse.Namespace) -> int:
    """The once-a-day batch. Each stream runs even if an earlier one failed."""
    from .ingest import blacklist, dns_check, rua

    results = {}
    exit_code = 0
    for name, runner in (
        ("rua", lambda: rua.run(since_days=args.since_days, offline=args.offline)),
        ("dns", dns_check.run),
        ("dnsbl", blacklist.run),
    ):
        try:
            results[name] = runner()
            if results[name].get("status") != "ok":
                exit_code = 1
        except Exception as exc:  # noqa: BLE001
            logging.exception("%s ingestion crashed", name)
            results[name] = {"status": "error", "error": str(exc)}
            exit_code = 1
    _print(results, args.json)
    return exit_code


def cmd_status(args: argparse.Namespace) -> int:
    """Print the same picture the dashboard shows, without starting a server."""
    settings = Settings.from_env()
    database = get_database(settings)

    print("Ingestion freshness")
    for row in ingestion_health(database, settings):
        marker = "ok " if row["state"] == "ok" else "!! "
        print(f"  {marker}{row['label']:<18} {row['state']:<10} {row['message']}")

    print(f"\nDomains by urgency (last {args.days} days)")
    for status in domain_statuses(settings, database, window_days=args.days):
        metrics = status.metrics
        rate = f"{metrics['compliance']:.0%}" if metrics["compliance"] is not None else "n/a"
        print(f"  [{status.severity:<8}] {status.domain:<24} compliance={rate:<5} {status.headline}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="deliverability", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--json", action="store_true", help="Machine-readable output.")
    sub = parser.add_subparsers(dest="command", required=True)

    rua_parser = sub.add_parser("rua", help="Ingest DMARC aggregate reports.")
    rua_parser.add_argument("--since-days", type=int, default=14)
    rua_parser.add_argument(
        "--offline",
        action="store_true",
        help="Skip reverse-DNS lookups. Faster, but forwarding cannot be detected without them.",
    )
    rua_parser.set_defaults(func=cmd_rua)

    dns_parser = sub.add_parser("dns", help="Check SPF/DMARC/DKIM/MX per domain.")
    dns_parser.set_defaults(func=cmd_dns)

    bounce_parser = sub.add_parser("bounce", help="Ingest bounces/NDRs.")
    bounce_parser.add_argument("--since-days", type=int, default=7)
    bounce_parser.set_defaults(func=cmd_bounce)

    dnsbl_parser = sub.add_parser("dnsbl", help="Check sending IPs against DNSBLs.")
    dnsbl_parser.set_defaults(func=cmd_dnsbl)

    daily_parser = sub.add_parser("daily", help="Run rua + dns + dnsbl.")
    daily_parser.add_argument("--since-days", type=int, default=14)
    daily_parser.add_argument("--offline", action="store_true")
    daily_parser.set_defaults(func=cmd_daily)

    status_parser = sub.add_parser("status", help="Print current state without network access.")
    status_parser.add_argument("--days", type=int, default=7)
    status_parser.set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    _configure_logging(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
