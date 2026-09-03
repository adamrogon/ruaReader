"""Repositories — the only place that builds queries.

Ingestion and dashboard code calls these methods with plain Python values and
gets plain dicts back. No SQLAlchemy objects cross this boundary.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

from sqlalchemy import and_, case, desc, func, select

from .database import Database
from .schema import (
    blacklist_checks,
    bounces,
    dismissed_flags,
    dmarc_records,
    dmarc_reports,
    dns_checks,
    domain_folders,
    ingestion_runs,
)
from .schema import domains as domains_table
from .schema import folders as folders_table
from .schema import mailboxes as mailboxes_table


def _rows(result) -> List[Dict[str, Any]]:  # noqa: ANN001
    return [dict(row._mapping) for row in result]


class _BaseRepository:
    def __init__(self, database: Database, project_id: str) -> None:
        self.db = database
        self.project_id = project_id


# --- Module 1 -----------------------------------------------------------------


class DmarcRepository(_BaseRepository):
    """DMARC aggregate reports and their per-source records."""

    def report_exists(
        self,
        report_id: str,
        org_name: str,
        date_begin: dt.datetime,
        date_end: dt.datetime,
    ) -> bool:
        """Dedupe check on (report_id, org_name, date_range)."""
        stmt = select(dmarc_reports.c.id).where(
            and_(
                dmarc_reports.c.project_id == self.project_id,
                dmarc_reports.c.report_id == report_id,
                dmarc_reports.c.org_name == org_name,
                dmarc_reports.c.date_begin == date_begin,
                dmarc_reports.c.date_end == date_end,
            )
        )
        with self.db.connect() as conn:
            return conn.execute(stmt).first() is not None

    def insert_report(
        self,
        report: Dict[str, Any],
        records: Sequence[Dict[str, Any]],
        receiving_esp: Optional[str] = None,
    ) -> int:
        """Insert a report and its records in one transaction.

        ``receiving_esp`` and ``org_name`` are denormalised onto every record so
        the per-ESP dashboard queries need no join.

        Returns the new report's primary key.
        """
        payload = dict(report, project_id=self.project_id)
        payload.setdefault("ingested_at", dt.datetime.now(dt.timezone.utc))

        with self.db.connect() as conn:
            result = conn.execute(dmarc_reports.insert().values(**payload))
            report_pk = int(result.inserted_primary_key[0])
            if records:
                conn.execute(
                    dmarc_records.insert(),
                    [
                        dict(
                            rec,
                            project_id=self.project_id,
                            report_id_fk=report_pk,
                            policy_domain=payload["policy_domain"],
                            date_begin=payload["date_begin"],
                            org_name=payload["org_name"],
                            receiving_esp=receiving_esp,
                        )
                        for rec in records
                    ],
                )
        return report_pk

    def daily_volume(
        self, since: dt.datetime, domain: Optional[Union[str, Sequence[str]]] = None
    ) -> List[Dict[str, Any]]:
        """Message volume per day per domain, split by evaluation outcome."""
        day = func.date(dmarc_records.c.date_begin).label("day")
        conditions = [
            dmarc_records.c.project_id == self.project_id,
            dmarc_records.c.date_begin >= since,
        ]
        if isinstance(domain, str):
            conditions.append(dmarc_records.c.policy_domain == domain)
        elif domain:
            conditions.append(dmarc_records.c.policy_domain.in_(domain))

        stmt = (
            select(
                day,
                dmarc_records.c.policy_domain.label("domain"),
                dmarc_records.c.evaluation,
                func.sum(dmarc_records.c.message_count).label("messages"),
            )
            .where(and_(*conditions))
            .group_by(day, dmarc_records.c.policy_domain, dmarc_records.c.evaluation)
            .order_by(day)
        )
        with self.db.connect() as conn:
            return _rows(conn.execute(stmt))

    def daily_alignment_failures(
        self, since: dt.datetime, domain: Optional[Union[str, Sequence[str]]] = None
    ) -> List[Dict[str, Any]]:
        """Per-day SPF/DKIM *alignment* failure volume — the metric other DMARC
        dashboards usually label "SPF Fail"/"DKIM Fail".

        Deliberately not the same as raw spf_result/dkim_result: a message can
        fail the raw check but still align (or vice versa isn't possible, but
        alignment is the field that actually gates DMARC pass/fail), and a
        message failing SPF alignment routinely still passes DMARC overall via
        DKIM — see classify/forwarding.py. These two counts are shown as
        overlay lines alongside the pass/forwarded/failed stack, not as
        another slice of it, because they are not mutually exclusive with
        "passed".
        """
        day = func.date(dmarc_records.c.date_begin).label("day")
        conditions = [
            dmarc_records.c.project_id == self.project_id,
            dmarc_records.c.date_begin >= since,
        ]
        if isinstance(domain, str):
            conditions.append(dmarc_records.c.policy_domain == domain)
        elif domain:
            conditions.append(dmarc_records.c.policy_domain.in_(domain))

        stmt = (
            select(
                day,
                dmarc_records.c.policy_domain.label("domain"),
                func.sum(
                    case((dmarc_records.c.spf_aligned.is_(False), dmarc_records.c.message_count), else_=0)
                ).label("spf_fail"),
                func.sum(
                    case((dmarc_records.c.dkim_aligned.is_(False), dmarc_records.c.message_count), else_=0)
                ).label("dkim_fail"),
            )
            .where(and_(*conditions))
            .group_by(day, dmarc_records.c.policy_domain)
            .order_by(day)
        )
        with self.db.connect() as conn:
            return _rows(conn.execute(stmt))

    def records_for_day(self, domain: str, day: dt.date) -> List[Dict[str, Any]]:
        """Every DMARC record for one domain on one calendar day (UTC) — the
        drill-down behind a click on a daily chart point: which reporting
        org, which source, and exactly what failed.
        """
        start = dt.datetime(day.year, day.month, day.day)
        end = start + dt.timedelta(days=1)
        conditions = [
            dmarc_records.c.project_id == self.project_id,
            dmarc_records.c.policy_domain == domain,
            dmarc_records.c.date_begin >= start,
            dmarc_records.c.date_begin < end,
        ]
        stmt = (
            select(
                dmarc_records.c.org_name,
                dmarc_records.c.source_ip,
                dmarc_records.c.source_host,
                dmarc_records.c.source_esp,
                dmarc_records.c.spf_result,
                dmarc_records.c.spf_aligned,
                dmarc_records.c.dkim_result,
                dmarc_records.c.dkim_aligned,
                dmarc_records.c.evaluation,
                dmarc_records.c.evaluation_reason,
                dmarc_records.c.message_count,
            )
            .where(and_(*conditions))
            .order_by(desc(dmarc_records.c.message_count))
        )
        with self.db.connect() as conn:
            return _rows(conn.execute(stmt))

    def domain_summary_for_day(self, day: dt.date) -> List[Dict[str, Any]]:
        """Per-domain rollup for one calendar day, across every domain —
        answers "which domain caused this fleet-wide spike" without opening
        each domain one at a time.
        """
        start = dt.datetime(day.year, day.month, day.day)
        end = start + dt.timedelta(days=1)
        conditions = [
            dmarc_records.c.project_id == self.project_id,
            dmarc_records.c.date_begin >= start,
            dmarc_records.c.date_begin < end,
        ]
        stmt = (
            select(
                dmarc_records.c.policy_domain.label("domain"),
                func.sum(dmarc_records.c.message_count).label("messages"),
                func.sum(
                    case((dmarc_records.c.evaluation == "pass", dmarc_records.c.message_count), else_=0)
                ).label("compliant"),
                func.sum(
                    case((dmarc_records.c.evaluation == "failed", dmarc_records.c.message_count), else_=0)
                ).label("failed"),
                func.sum(
                    case((dmarc_records.c.spf_aligned.is_(False), dmarc_records.c.message_count), else_=0)
                ).label("spf_fail"),
                func.sum(
                    case((dmarc_records.c.dkim_aligned.is_(False), dmarc_records.c.message_count), else_=0)
                ).label("dkim_fail"),
            )
            .where(and_(*conditions))
            .group_by(dmarc_records.c.policy_domain)
            .order_by(desc("messages"))
        )
        with self.db.connect() as conn:
            return _rows(conn.execute(stmt))

    def compliance_by_domain(self, since: dt.datetime) -> List[Dict[str, Any]]:
        """Per-domain totals by evaluation outcome.

        Forwarded messages are returned as their own bucket so the caller can
        exclude them from the compliance rate rather than counting them as
        failures.
        """
        stmt = (
            select(
                dmarc_records.c.policy_domain.label("domain"),
                dmarc_records.c.evaluation,
                func.sum(dmarc_records.c.message_count).label("messages"),
            )
            .where(
                and_(
                    dmarc_records.c.project_id == self.project_id,
                    dmarc_records.c.date_begin >= since,
                )
            )
            .group_by(dmarc_records.c.policy_domain, dmarc_records.c.evaluation)
        )
        with self.db.connect() as conn:
            return _rows(conn.execute(stmt))

    def esp_breakdown(self, since: dt.datetime, domain: Optional[str] = None) -> List[Dict[str, Any]]:
        """Volume and outcome per RECEIVING ESP.

        Grouped by ``receiving_esp`` (derived from the reporting org_name),
        because the provider that has a problem with you is the one writing the
        report. Grouping by source IP would answer a different question.
        """
        conditions = [
            dmarc_records.c.project_id == self.project_id,
            dmarc_records.c.date_begin >= since,
        ]
        if domain:
            conditions.append(dmarc_records.c.policy_domain == domain)

        stmt = (
            select(
                dmarc_records.c.policy_domain.label("domain"),
                dmarc_records.c.receiving_esp.label("esp"),
                dmarc_records.c.evaluation,
                func.sum(dmarc_records.c.message_count).label("messages"),
                func.count(dmarc_records.c.id).label("record_count"),
            )
            .where(and_(*conditions))
            .group_by(
                dmarc_records.c.policy_domain,
                dmarc_records.c.receiving_esp,
                dmarc_records.c.evaluation,
            )
        )
        with self.db.connect() as conn:
            return _rows(conn.execute(stmt))

    def other_esp_org_breakdown(
        self, since: dt.datetime, domain: Optional[Union[str, Sequence[str]]] = None
    ) -> List[Dict[str, Any]]:
        """Which real reporting organisations are hiding inside the "Other"
        ESP bucket, and how much volume each sent.

        `esp_breakdown()` groups by the classified `receiving_esp` label, so
        every org_name that :func:`classify.esp.esp_from_org_name` doesn't
        recognise collapses into one "Other" row — a single unrecognised
        provider with real volume looks identical to ten one-off reporters
        with a single message each. This answers that distinction from the
        raw org_name, which is still stored per record even though the
        classified label is not.
        """
        conditions = [
            dmarc_records.c.project_id == self.project_id,
            dmarc_records.c.date_begin >= since,
            dmarc_records.c.receiving_esp == "Other",
        ]
        if isinstance(domain, str):
            conditions.append(dmarc_records.c.policy_domain == domain)
        elif domain:
            conditions.append(dmarc_records.c.policy_domain.in_(domain))

        stmt = (
            select(
                dmarc_records.c.org_name,
                func.sum(dmarc_records.c.message_count).label("messages"),
            )
            .where(and_(*conditions))
            .group_by(dmarc_records.c.org_name)
            .order_by(desc("messages"))
        )
        with self.db.connect() as conn:
            return _rows(conn.execute(stmt))

    def reporting_orgs(self, since: dt.datetime, domain: Optional[str] = None) -> List[Dict[str, Any]]:
        """Which organisations sent reports, and how many.

        A domain that stops being reported on by a major provider is itself a
        signal, so this is tracked separately from message volume.
        """
        conditions = [
            dmarc_reports.c.project_id == self.project_id,
            dmarc_reports.c.date_begin >= since,
        ]
        if domain:
            conditions.append(dmarc_reports.c.policy_domain == domain)

        stmt = (
            select(
                dmarc_reports.c.policy_domain.label("domain"),
                dmarc_reports.c.org_name,
                func.count(dmarc_reports.c.id).label("report_count"),
                func.max(dmarc_reports.c.date_end).label("latest"),
            )
            .where(and_(*conditions))
            .group_by(dmarc_reports.c.policy_domain, dmarc_reports.c.org_name)
            .order_by(desc("report_count"))
        )
        with self.db.connect() as conn:
            return _rows(conn.execute(stmt))

    def unknown_report_domains(self) -> List[Dict[str, Any]]:
        """Domains that show up in incoming rua reports but are not on the
        monitored list.

        Reports arrive with ``policy_published > domain`` set by whoever wrote
        the report, and that value is what the dashboard filters by. If a
        report arrives for a domain the user has not added — because of a
        typo when adding the domain, or a new domain nobody remembered to
        register here — the data is stored but never surfaces on any page.
        This query exposes that gap so the Settings screen can flag it.
        """
        monitored = select(domains_table.c.name).where(
            domains_table.c.project_id == self.project_id
        )
        stmt = (
            select(
                dmarc_reports.c.policy_domain.label("domain"),
                func.count(dmarc_reports.c.id).label("report_count"),
                func.max(dmarc_reports.c.date_end).label("latest"),
            )
            .where(
                and_(
                    dmarc_reports.c.project_id == self.project_id,
                    dmarc_reports.c.policy_domain.notin_(monitored),
                )
            )
            .group_by(dmarc_reports.c.policy_domain)
            .order_by(desc("latest"))
        )
        with self.db.connect() as conn:
            return _rows(conn.execute(stmt))

    def top_failing_sources(
        self, since: dt.datetime, domain: Optional[str] = None, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Highest-volume sources whose messages are failing outright."""
        conditions = [
            dmarc_records.c.project_id == self.project_id,
            dmarc_records.c.date_begin >= since,
            dmarc_records.c.evaluation == "failed",
        ]
        if domain:
            conditions.append(dmarc_records.c.policy_domain == domain)

        stmt = (
            select(
                dmarc_records.c.policy_domain.label("domain"),
                dmarc_records.c.source_ip,
                dmarc_records.c.source_host,
                dmarc_records.c.source_esp.label("esp"),
                dmarc_records.c.dkim_result,
                dmarc_records.c.spf_result,
                dmarc_records.c.disposition,
                func.sum(dmarc_records.c.message_count).label("messages"),
            )
            .where(and_(*conditions))
            .group_by(
                dmarc_records.c.policy_domain,
                dmarc_records.c.source_ip,
                dmarc_records.c.source_host,
                dmarc_records.c.source_esp,
                dmarc_records.c.dkim_result,
                dmarc_records.c.spf_result,
                dmarc_records.c.disposition,
            )
            .order_by(desc("messages"))
            .limit(limit)
        )
        with self.db.connect() as conn:
            return _rows(conn.execute(stmt))


# --- Module 2 -----------------------------------------------------------------


class DnsRepository(_BaseRepository):
    def insert_check(self, check: Dict[str, Any]) -> int:
        payload = dict(check, project_id=self.project_id)
        payload.setdefault("checked_at", dt.datetime.now(dt.timezone.utc))
        with self.db.connect() as conn:
            result = conn.execute(dns_checks.insert().values(**payload))
            return int(result.inserted_primary_key[0])

    def latest_per_domain(self) -> Dict[str, Dict[str, Any]]:
        """Most recent check for each domain, keyed by domain."""
        newest = (
            select(
                dns_checks.c.domain,
                func.max(dns_checks.c.checked_at).label("checked_at"),
            )
            .where(dns_checks.c.project_id == self.project_id)
            .group_by(dns_checks.c.domain)
            .subquery()
        )
        stmt = select(dns_checks).join(
            newest,
            and_(
                dns_checks.c.domain == newest.c.domain,
                dns_checks.c.checked_at == newest.c.checked_at,
            ),
        )
        with self.db.connect() as conn:
            return {row["domain"]: row for row in _rows(conn.execute(stmt))}

    def history(self, domain: str, limit: int = 60) -> List[Dict[str, Any]]:
        stmt = (
            select(dns_checks)
            .where(
                and_(
                    dns_checks.c.project_id == self.project_id,
                    dns_checks.c.domain == domain,
                )
            )
            .order_by(desc(dns_checks.c.checked_at))
            .limit(limit)
        )
        with self.db.connect() as conn:
            return _rows(conn.execute(stmt))


# --- Module 3 -----------------------------------------------------------------


class BounceRepository(_BaseRepository):
    def existing_message_ids(self, mailbox_name: str, message_ids: Iterable[str]) -> set:
        """Subset of the given Message-Ids already stored for this mailbox."""
        ids = [m for m in message_ids if m]
        if not ids:
            return set()
        stmt = select(bounces.c.message_id).where(
            and_(
                bounces.c.project_id == self.project_id,
                bounces.c.mailbox_name == mailbox_name,
                bounces.c.message_id.in_(ids),
            )
        )
        with self.db.connect() as conn:
            return {row[0] for row in conn.execute(stmt)}

    def insert_many(self, records: Sequence[Dict[str, Any]]) -> int:
        if not records:
            return 0
        now = dt.datetime.now(dt.timezone.utc)
        payload = [dict(r, project_id=self.project_id, ingested_at=r.get("ingested_at", now)) for r in records]
        with self.db.connect() as conn:
            conn.execute(bounces.insert(), payload)
        return len(payload)

    def counts_by_class(self, since: dt.datetime, domain: Optional[str] = None) -> List[Dict[str, Any]]:
        conditions = [
            bounces.c.project_id == self.project_id,
            bounces.c.received_at >= since,
        ]
        if domain:
            conditions.append(bounces.c.sending_domain == domain)

        stmt = (
            select(
                bounces.c.sending_domain.label("domain"),
                bounces.c.bounce_class,
                func.count(bounces.c.id).label("count"),
            )
            .where(and_(*conditions))
            .group_by(bounces.c.sending_domain, bounces.c.bounce_class)
        )
        with self.db.connect() as conn:
            return _rows(conn.execute(stmt))

    def counts_by_code(self, since: dt.datetime, domain: Optional[str] = None) -> List[Dict[str, Any]]:
        """Bounce counts broken out by enhanced status code."""
        conditions = [
            bounces.c.project_id == self.project_id,
            bounces.c.received_at >= since,
        ]
        if domain:
            conditions.append(bounces.c.sending_domain == domain)

        stmt = (
            select(
                bounces.c.sending_domain.label("domain"),
                bounces.c.status_code,
                bounces.c.bounce_class,
                func.count(bounces.c.id).label("count"),
            )
            .where(and_(*conditions))
            .group_by(bounces.c.sending_domain, bounces.c.status_code, bounces.c.bounce_class)
            .order_by(desc("count"))
        )
        with self.db.connect() as conn:
            return _rows(conn.execute(stmt))

    def daily_counts(
        self, since: dt.datetime, domain: Optional[Union[str, Sequence[str]]] = None
    ) -> List[Dict[str, Any]]:
        day = func.date(bounces.c.received_at).label("day")
        conditions = [
            bounces.c.project_id == self.project_id,
            bounces.c.received_at >= since,
        ]
        if isinstance(domain, str):
            conditions.append(bounces.c.sending_domain == domain)
        elif domain:
            conditions.append(bounces.c.sending_domain.in_(domain))

        stmt = (
            select(
                day,
                bounces.c.sending_domain.label("domain"),
                bounces.c.bounce_class,
                func.count(bounces.c.id).label("count"),
            )
            .where(and_(*conditions))
            .group_by(day, bounces.c.sending_domain, bounces.c.bounce_class)
            .order_by(day)
        )
        with self.db.connect() as conn:
            return _rows(conn.execute(stmt))

    def counts_by_class_for_day(self, day: dt.date, domain: Optional[str] = None) -> List[Dict[str, Any]]:
        """Bounce class counts for one calendar day (UTC) — the bounce half of
        a daily-chart drill-down, alongside DmarcRepository.records_for_day().
        """
        start = dt.datetime(day.year, day.month, day.day)
        end = start + dt.timedelta(days=1)
        conditions = [
            bounces.c.project_id == self.project_id,
            bounces.c.received_at >= start,
            bounces.c.received_at < end,
        ]
        if domain:
            conditions.append(bounces.c.sending_domain == domain)
        stmt = (
            select(
                bounces.c.sending_domain.label("domain"),
                bounces.c.bounce_class,
                func.count(bounces.c.id).label("count"),
            )
            .where(and_(*conditions))
            .group_by(bounces.c.sending_domain, bounces.c.bounce_class)
        )
        with self.db.connect() as conn:
            return _rows(conn.execute(stmt))

    def sender_blocks(self, since: dt.datetime, domain: Optional[str] = None) -> List[Dict[str, Any]]:
        """Sender-block events — the highest-priority signal in the tool."""
        conditions = [
            bounces.c.project_id == self.project_id,
            bounces.c.received_at >= since,
            bounces.c.bounce_class == "sender_block",
        ]
        if domain:
            conditions.append(bounces.c.sending_domain == domain)

        stmt = (
            select(
                bounces.c.sending_domain.label("domain"),
                bounces.c.status_code,
                bounces.c.recipient_domain,
                bounces.c.recipient_esp,
                bounces.c.diagnostic_code,
                bounces.c.bounce_reason,
                bounces.c.received_at,
            )
            .where(and_(*conditions))
            .order_by(desc(bounces.c.received_at))
        )
        with self.db.connect() as conn:
            return _rows(conn.execute(stmt))

    def recent(self, since: dt.datetime, domain: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        conditions = [
            bounces.c.project_id == self.project_id,
            bounces.c.received_at >= since,
        ]
        if domain:
            conditions.append(bounces.c.sending_domain == domain)

        stmt = (
            select(bounces)
            .where(and_(*conditions))
            .order_by(desc(bounces.c.received_at))
            .limit(limit)
        )
        with self.db.connect() as conn:
            return _rows(conn.execute(stmt))

    def summary_by_code(
        self, since: dt.datetime, domain: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """One row per (code, class, provider) with count and latest evidence.

        Used to fold the old ``sender_blocks`` and ``counts_by_code`` tables
        into a single "what's actually happening" table. Sorted by count DESC
        so the loudest problem is on top.
        """
        conditions = [
            bounces.c.project_id == self.project_id,
            bounces.c.received_at >= since,
        ]
        if domain:
            conditions.append(bounces.c.sending_domain == domain)

        stmt = (
            select(
                bounces.c.status_code,
                bounces.c.bounce_class,
                bounces.c.recipient_esp.label("esp"),
                func.count(bounces.c.id).label("count"),
                func.max(bounces.c.received_at).label("latest"),
                # SQLite tolerates non-aggregated columns in GROUP BY and picks
                # an arbitrary value — good enough for a "sample diagnostic".
                bounces.c.diagnostic_code.label("sample_diagnostic"),
            )
            .where(and_(*conditions))
            .group_by(bounces.c.status_code, bounces.c.bounce_class, bounces.c.recipient_esp)
            .order_by(desc("count"), desc("latest"))
        )
        with self.db.connect() as conn:
            return _rows(conn.execute(stmt))

    # Whitelist of user-selectable sort columns. The values name the tuple of
    # (primary, tiebreaker) SQL expressions used for the ORDER BY. Kept as a
    # small dict so the web layer can validate ``sort`` from the URL against a
    # closed set instead of trusting whatever comes in.
    _SORT_COLUMNS = {
        "received_at": (bounces.c.received_at,),
        # "Severity" order: sender_block worst, then hard, soft, unknown.
        # Encoded with a CASE so ASC/DESC toggles behave predictably —
        # ASC = most severe first, DESC = least severe first.
        "class": (
            case(
                (bounces.c.bounce_class == "sender_block", 0),
                (bounces.c.bounce_class == "hard", 1),
                (bounces.c.bounce_class == "soft", 2),
                else_=3,
            ),
            bounces.c.received_at,
        ),
        "status_code": (bounces.c.status_code, bounces.c.received_at),
        "recipient_domain": (bounces.c.recipient_domain, bounces.c.received_at),
    }

    def recent_paged(
        self,
        since: dt.datetime,
        domain: Optional[str] = None,
        page: int = 1,
        per_page: int = 10,
        sort: str = "received_at",
        order: str = "desc",
    ) -> Dict[str, Any]:
        """One page of recent bounces plus the total count for pager arithmetic.

        ``sort`` and ``order`` are validated against a closed whitelist here,
        so untrusted values from the URL cannot inject SQL. Unknown values
        silently fall back to the default (newest first).
        """
        conditions = [
            bounces.c.project_id == self.project_id,
            bounces.c.received_at >= since,
        ]
        if domain:
            conditions.append(bounces.c.sending_domain == domain)

        sort_exprs = self._SORT_COLUMNS.get(sort) or self._SORT_COLUMNS["received_at"]
        direction = desc if order.lower() != "asc" else (lambda x: x)
        order_by = [direction(expr) for expr in sort_exprs]

        total_stmt = select(func.count()).select_from(bounces).where(and_(*conditions))
        page_stmt = (
            select(bounces)
            .where(and_(*conditions))
            .order_by(*order_by)
            .limit(per_page)
            .offset(max(0, (page - 1)) * per_page)
        )
        with self.db.connect() as conn:
            total = int(conn.execute(total_stmt).scalar() or 0)
            rows = _rows(conn.execute(page_stmt))
        return {
            "rows": rows,
            "total": total,
            "page": max(1, page),
            "per_page": per_page,
            "sort": sort if sort in self._SORT_COLUMNS else "received_at",
            "order": "asc" if order.lower() == "asc" else "desc",
        }

    def latest_per_domain(self, since: dt.datetime) -> List[Dict[str, Any]]:
        """Timestamp of the most recent bounce per domain.

        Used as the evidence marker for rate-based flags, which have no single
        triggering event that an acknowledgement could otherwise be pinned to.
        """
        stmt = (
            select(
                bounces.c.sending_domain.label("domain"),
                func.max(bounces.c.received_at).label("latest"),
            )
            .where(
                and_(
                    bounces.c.project_id == self.project_id,
                    bounces.c.received_at >= since,
                )
            )
            .group_by(bounces.c.sending_domain)
        )
        with self.db.connect() as conn:
            return _rows(conn.execute(stmt))

    def unparsed_count(self, since: dt.datetime) -> int:
        """How many DSNs were stored as raw text because parsing failed."""
        stmt = select(func.count(bounces.c.id)).where(
            and_(
                bounces.c.project_id == self.project_id,
                bounces.c.received_at >= since,
                bounces.c.parse_ok.is_(False),
            )
        )
        with self.db.connect() as conn:
            return int(conn.execute(stmt).scalar() or 0)


# --- Module 4 -----------------------------------------------------------------


class BlacklistRepository(_BaseRepository):
    def insert_many(self, results: Sequence[Dict[str, Any]]) -> int:
        if not results:
            return 0
        now = dt.datetime.now(dt.timezone.utc)
        payload = [dict(r, project_id=self.project_id, checked_at=r.get("checked_at", now)) for r in results]
        with self.db.connect() as conn:
            conn.execute(blacklist_checks.insert(), payload)
        return len(payload)

    def latest_per_domain(self) -> Dict[str, List[Dict[str, Any]]]:
        """Most recent check round per domain, grouped by domain."""
        newest = (
            select(
                blacklist_checks.c.domain,
                func.max(blacklist_checks.c.checked_at).label("checked_at"),
            )
            .where(blacklist_checks.c.project_id == self.project_id)
            .group_by(blacklist_checks.c.domain)
            .subquery()
        )
        stmt = select(blacklist_checks).join(
            newest,
            and_(
                blacklist_checks.c.domain == newest.c.domain,
                blacklist_checks.c.checked_at == newest.c.checked_at,
            ),
        )
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        with self.db.connect() as conn:
            for row in _rows(conn.execute(stmt)):
                grouped.setdefault(row["domain"], []).append(row)
        return grouped

    def history_for_domain(self, domain: str, since: dt.datetime) -> List[Dict[str, Any]]:
        """Every check round for one domain since a cutoff.

        Unlike ``latest_per_domain`` (just the newest round), this is used to
        tell "listed in every check this week" apart from "flagged once and
        cleared" — the same IP being listed round after round is a much
        stronger signal than a single hit.
        """
        stmt = (
            select(blacklist_checks)
            .where(
                and_(
                    blacklist_checks.c.project_id == self.project_id,
                    blacklist_checks.c.domain == domain,
                    blacklist_checks.c.checked_at >= since,
                )
            )
            .order_by(blacklist_checks.c.checked_at)
        )
        with self.db.connect() as conn:
            return _rows(conn.execute(stmt))


# --- Ingestion health ---------------------------------------------------------


class IngestionRunRepository(_BaseRepository):
    def start(self, stream: str) -> int:
        with self.db.connect() as conn:
            result = conn.execute(
                ingestion_runs.insert().values(
                    project_id=self.project_id,
                    stream=stream,
                    started_at=dt.datetime.now(dt.timezone.utc),
                    status="running",
                )
            )
            return int(result.inserted_primary_key[0])

    def finish(
        self,
        run_id: int,
        status: str,
        items_seen: int = 0,
        items_ingested: int = 0,
        error: Optional[str] = None,
        detail: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self.db.connect() as conn:
            conn.execute(
                ingestion_runs.update()
                .where(ingestion_runs.c.id == run_id)
                .values(
                    finished_at=dt.datetime.now(dt.timezone.utc),
                    status=status,
                    items_seen=items_seen,
                    items_ingested=items_ingested,
                    error=error,
                    detail=detail,
                )
            )

    def last_run_per_stream(self) -> Dict[str, Dict[str, Any]]:
        """Latest run per stream regardless of outcome."""
        newest = (
            select(
                ingestion_runs.c.stream,
                func.max(ingestion_runs.c.started_at).label("started_at"),
            )
            .where(ingestion_runs.c.project_id == self.project_id)
            .group_by(ingestion_runs.c.stream)
            .subquery()
        )
        stmt = select(ingestion_runs).join(
            newest,
            and_(
                ingestion_runs.c.stream == newest.c.stream,
                ingestion_runs.c.started_at == newest.c.started_at,
            ),
        )
        with self.db.connect() as conn:
            return {row["stream"]: row for row in _rows(conn.execute(stmt))}

    def active_streams(self) -> List[str]:
        """Streams with a run currently in progress.

        Used to stop the dashboard's "check now" button from starting a second
        copy of a job that is already running.
        """
        stmt = select(ingestion_runs.c.stream).where(
            and_(
                ingestion_runs.c.project_id == self.project_id,
                ingestion_runs.c.status == "running",
            )
        )
        with self.db.connect() as conn:
            return sorted({row[0] for row in conn.execute(stmt)})

    def fail_stale_running(self, older_than_minutes: int = 30) -> int:
        """Mark long-abandoned 'running' rows as errors.

        A process killed mid-run leaves its row at 'running' forever, which
        would otherwise block that stream from ever being started again.
        Was 60 minutes — observed full-fleet runs top out around 30, so 60
        meant a genuinely dead run (a killed process, a hung connection)
        could block that stream for up to an hour before self-healing.
        30 keeps comfortable headroom above the slowest legitimate run
        while halving the worst-case blocked time.
        """
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=older_than_minutes)
        with self.db.connect() as conn:
            result = conn.execute(
                ingestion_runs.update()
                .where(
                    and_(
                        ingestion_runs.c.project_id == self.project_id,
                        ingestion_runs.c.status == "running",
                        ingestion_runs.c.started_at < cutoff,
                    )
                )
                .values(
                    status="error",
                    finished_at=dt.datetime.now(dt.timezone.utc),
                    error="Run did not finish — the process was interrupted.",
                )
            )
            return int(result.rowcount or 0)

    def last_success_per_stream(self) -> Dict[str, dt.datetime]:
        """When each stream last completed successfully.

        This drives the 48h staleness state — a stream that keeps erroring
        looks identical to one nobody is running, and both need surfacing.
        "partial" (some mailboxes/domains failed, others didn't) counts as a
        success for freshness purposes — data did land — but is still
        surfaced separately via ``latest_status`` in ingestion_health().
        """
        stmt = (
            select(
                ingestion_runs.c.stream,
                func.max(ingestion_runs.c.finished_at).label("finished_at"),
            )
            .where(
                and_(
                    ingestion_runs.c.project_id == self.project_id,
                    ingestion_runs.c.status.in_(("ok", "partial")),
                )
            )
            .group_by(ingestion_runs.c.stream)
        )
        with self.db.connect() as conn:
            return {row["stream"]: row["finished_at"] for row in _rows(conn.execute(stmt))}


# --- Configuration ------------------------------------------------------------


class DomainConfigRepository(_BaseRepository):
    """Sending domains, managed from the dashboard."""

    def list_all(self, include_disabled: bool = True) -> List[Dict[str, Any]]:
        conditions = [domains_table.c.project_id == self.project_id]
        if not include_disabled:
            conditions.append(domains_table.c.enabled.is_(True))
        stmt = select(domains_table).where(and_(*conditions)).order_by(domains_table.c.name)
        with self.db.connect() as conn:
            return _rows(conn.execute(stmt))

    def get(self, domain_id: int) -> Optional[Dict[str, Any]]:
        stmt = select(domains_table).where(
            and_(domains_table.c.id == domain_id, domains_table.c.project_id == self.project_id)
        )
        with self.db.connect() as conn:
            rows = _rows(conn.execute(stmt))
        return rows[0] if rows else None

    def get_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        stmt = select(domains_table).where(
            and_(domains_table.c.name == name, domains_table.c.project_id == self.project_id)
        )
        with self.db.connect() as conn:
            rows = _rows(conn.execute(stmt))
        return rows[0] if rows else None

    def create(
        self,
        name: str,
        dkim_selectors: Sequence[str],
        notes: str = "",
        enabled: bool = True,
    ) -> int:
        now = dt.datetime.now(dt.timezone.utc)
        with self.db.connect() as conn:
            result = conn.execute(
                domains_table.insert().values(
                    project_id=self.project_id,
                    name=name.strip().lower(),
                    dkim_selectors=list(dkim_selectors),
                    notes=notes,
                    enabled=enabled,
                    created_at=now,
                    updated_at=now,
                )
            )
            return int(result.inserted_primary_key[0])

    def update(self, domain_id: int, **fields: Any) -> None:
        allowed = {"name", "dkim_selectors", "notes", "enabled"}
        values = {k: v for k, v in fields.items() if k in allowed}
        if not values:
            return
        if "name" in values:
            values["name"] = values["name"].strip().lower()
        values["updated_at"] = dt.datetime.now(dt.timezone.utc)
        with self.db.connect() as conn:
            conn.execute(
                domains_table.update()
                .where(
                    and_(domains_table.c.id == domain_id, domains_table.c.project_id == self.project_id)
                )
                .values(**values)
            )

    def delete(self, domain_id: int) -> None:
        """Remove a domain and everything collected under its name.

        Deleting a domain here means deleting it: DMARC reports/records,
        bounces, DNS checks and blacklist checks for that domain name are
        wiped along with it, so nothing lingers to resurface later (e.g. in
        "unknown domains in reports").
        """
        row = self.get(domain_id)
        with self.db.connect() as conn:
            if row:
                name = row["name"]
                conn.execute(dmarc_records.delete().where(
                    and_(dmarc_records.c.project_id == self.project_id, dmarc_records.c.policy_domain == name)
                ))
                conn.execute(dmarc_reports.delete().where(
                    and_(dmarc_reports.c.project_id == self.project_id, dmarc_reports.c.policy_domain == name)
                ))
                conn.execute(bounces.delete().where(
                    and_(bounces.c.project_id == self.project_id, bounces.c.sending_domain == name)
                ))
                conn.execute(dns_checks.delete().where(
                    and_(dns_checks.c.project_id == self.project_id, dns_checks.c.domain == name)
                ))
                conn.execute(blacklist_checks.delete().where(
                    and_(blacklist_checks.c.project_id == self.project_id, blacklist_checks.c.domain == name)
                ))
            conn.execute(domain_folders.delete().where(
                and_(domain_folders.c.project_id == self.project_id, domain_folders.c.domain_id == domain_id)
            ))
            conn.execute(
                domains_table.delete().where(
                    and_(domains_table.c.id == domain_id, domains_table.c.project_id == self.project_id)
                )
            )

    def count(self) -> int:
        stmt = select(func.count(domains_table.c.id)).where(domains_table.c.project_id == self.project_id)
        with self.db.connect() as conn:
            return int(conn.execute(stmt).scalar() or 0)


class FolderRepository(_BaseRepository):
    """Prototype: organisational grouping of domains, many-to-many."""

    def list_all(self) -> List[Dict[str, Any]]:
        stmt = select(folders_table).where(folders_table.c.project_id == self.project_id).order_by(
            folders_table.c.name
        )
        with self.db.connect() as conn:
            return _rows(conn.execute(stmt))

    def get(self, folder_id: int) -> Optional[Dict[str, Any]]:
        stmt = select(folders_table).where(
            and_(folders_table.c.id == folder_id, folders_table.c.project_id == self.project_id)
        )
        with self.db.connect() as conn:
            rows = _rows(conn.execute(stmt))
        return rows[0] if rows else None

    def get_or_create_by_name(self, name: str) -> int:
        """Used by bulk import: a folder name that doesn't exist yet is
        created on the spot rather than rejecting the row."""
        name = name.strip()
        with self.db.connect() as conn:
            existing = conn.execute(
                select(folders_table.c.id).where(
                    and_(folders_table.c.project_id == self.project_id, folders_table.c.name == name)
                )
            ).first()
            if existing:
                return existing.id
            result = conn.execute(
                folders_table.insert().values(
                    project_id=self.project_id, name=name, created_at=dt.datetime.now(dt.timezone.utc)
                )
            )
            return int(result.inserted_primary_key[0])

    def create(self, name: str) -> int:
        with self.db.connect() as conn:
            result = conn.execute(
                folders_table.insert().values(
                    project_id=self.project_id,
                    name=name.strip(),
                    created_at=dt.datetime.now(dt.timezone.utc),
                )
            )
            return int(result.inserted_primary_key[0])

    def delete(self, folder_id: int) -> None:
        """Removing a folder never removes domains — only the assignment."""
        with self.db.connect() as conn:
            conn.execute(domain_folders.delete().where(
                and_(domain_folders.c.project_id == self.project_id, domain_folders.c.folder_id == folder_id)
            ))
            conn.execute(folders_table.delete().where(
                and_(folders_table.c.id == folder_id, folders_table.c.project_id == self.project_id)
            ))

    def folder_ids_for_domain(self, domain_id: int) -> List[int]:
        stmt = select(domain_folders.c.folder_id).where(
            and_(domain_folders.c.project_id == self.project_id, domain_folders.c.domain_id == domain_id)
        )
        with self.db.connect() as conn:
            return [row.folder_id for row in conn.execute(stmt)]

    def set_domain_folders(self, domain_id: int, folder_ids: Sequence[int]) -> None:
        """Replace a domain's folder assignments wholesale — simplest correct
        semantics for a checkbox-list form (empty selection means no folders,
        not 'leave as-is')."""
        with self.db.connect() as conn:
            conn.execute(domain_folders.delete().where(
                and_(domain_folders.c.project_id == self.project_id, domain_folders.c.domain_id == domain_id)
            ))
            for fid in set(folder_ids):
                conn.execute(domain_folders.insert().values(
                    project_id=self.project_id, domain_id=domain_id, folder_id=fid
                ))

    def domain_ids_in_folder(self, folder_id: int) -> List[int]:
        stmt = select(domain_folders.c.domain_id).where(
            and_(domain_folders.c.project_id == self.project_id, domain_folders.c.folder_id == folder_id)
        )
        with self.db.connect() as conn:
            return [row.domain_id for row in conn.execute(stmt)]

    def domain_counts(self) -> Dict[int, int]:
        """Domain count per folder, for the folder list page — one query
        instead of one per folder."""
        stmt = (
            select(domain_folders.c.folder_id, func.count(domain_folders.c.domain_id))
            .where(domain_folders.c.project_id == self.project_id)
            .group_by(domain_folders.c.folder_id)
        )
        with self.db.connect() as conn:
            return {row[0]: row[1] for row in conn.execute(stmt)}


class MailboxConfigRepository(_BaseRepository):
    """IMAP mailboxes, managed from the dashboard."""

    def list_all(self, kind: Optional[str] = None, include_disabled: bool = True) -> List[Dict[str, Any]]:
        conditions = [mailboxes_table.c.project_id == self.project_id]
        if kind:
            conditions.append(mailboxes_table.c.kind == kind)
        if not include_disabled:
            conditions.append(mailboxes_table.c.enabled.is_(True))
        stmt = select(mailboxes_table).where(and_(*conditions)).order_by(
            mailboxes_table.c.kind, mailboxes_table.c.name
        )
        with self.db.connect() as conn:
            return _rows(conn.execute(stmt))

    def get(self, mailbox_id: int) -> Optional[Dict[str, Any]]:
        stmt = select(mailboxes_table).where(
            and_(mailboxes_table.c.id == mailbox_id, mailboxes_table.c.project_id == self.project_id)
        )
        with self.db.connect() as conn:
            rows = _rows(conn.execute(stmt))
        return rows[0] if rows else None

    def create(self, **fields: Any) -> int:
        now = dt.datetime.now(dt.timezone.utc)
        payload = {
            "project_id": self.project_id,
            "created_at": now,
            "updated_at": now,
            **fields,
        }
        with self.db.connect() as conn:
            result = conn.execute(mailboxes_table.insert().values(**payload))
            return int(result.inserted_primary_key[0])

    def update(self, mailbox_id: int, **fields: Any) -> None:
        allowed = {
            "name", "kind", "host", "port", "ssl", "username", "password_encrypted",
            "password_env", "folder", "processed_folder", "domain", "enabled",
        }
        values = {k: v for k, v in fields.items() if k in allowed}
        if not values:
            return
        values["updated_at"] = dt.datetime.now(dt.timezone.utc)
        with self.db.connect() as conn:
            conn.execute(
                mailboxes_table.update()
                .where(
                    and_(
                        mailboxes_table.c.id == mailbox_id,
                        mailboxes_table.c.project_id == self.project_id,
                    )
                )
                .values(**values)
            )

    def record_test(self, mailbox_id: int, ok: bool, error: Optional[str] = None) -> None:
        """Store the outcome of a connection test."""
        with self.db.connect() as conn:
            conn.execute(
                mailboxes_table.update()
                .where(
                    and_(
                        mailboxes_table.c.id == mailbox_id,
                        mailboxes_table.c.project_id == self.project_id,
                    )
                )
                .values(
                    last_test_at=dt.datetime.now(dt.timezone.utc),
                    last_test_ok=ok,
                    last_test_error=error,
                )
            )

    def delete(self, mailbox_id: int) -> None:
        with self.db.connect() as conn:
            conn.execute(
                mailboxes_table.delete().where(
                    and_(
                        mailboxes_table.c.id == mailbox_id,
                        mailboxes_table.c.project_id == self.project_id,
                    )
                )
            )

    def count(self) -> int:
        stmt = select(func.count(mailboxes_table.c.id)).where(
            mailboxes_table.c.project_id == self.project_id
        )
        with self.db.connect() as conn:
            return int(conn.execute(stmt).scalar() or 0)


class DismissedFlagRepository(_BaseRepository):
    """Which flag fingerprints a user has chosen to hide, per domain."""

    def all_by_domain(self) -> Dict[str, set]:
        """Every dismissal, grouped by domain — one query for the whole fleet."""
        stmt = select(dismissed_flags.c.domain, dismissed_flags.c.fingerprint).where(
            dismissed_flags.c.project_id == self.project_id
        )
        out: Dict[str, set] = {}
        with self.db.connect() as conn:
            for row in conn.execute(stmt):
                out.setdefault(row.domain, set()).add(row.fingerprint)
        return out

    def for_domain(self, domain: str) -> set:
        stmt = select(dismissed_flags.c.fingerprint).where(
            and_(dismissed_flags.c.project_id == self.project_id, dismissed_flags.c.domain == domain)
        )
        with self.db.connect() as conn:
            return {row.fingerprint for row in conn.execute(stmt)}

    def dismiss(self, domain: str, fingerprint: str) -> None:
        with self.db.connect() as conn:
            exists = conn.execute(
                select(dismissed_flags.c.id).where(
                    and_(
                        dismissed_flags.c.project_id == self.project_id,
                        dismissed_flags.c.domain == domain,
                        dismissed_flags.c.fingerprint == fingerprint,
                    )
                )
            ).first()
            if exists:
                return
            conn.execute(
                dismissed_flags.insert().values(
                    project_id=self.project_id,
                    domain=domain,
                    fingerprint=fingerprint,
                    dismissed_at=dt.datetime.now(dt.timezone.utc),
                )
            )

    def restore(self, domain: str, fingerprint: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                dismissed_flags.delete().where(
                    and_(
                        dismissed_flags.c.project_id == self.project_id,
                        dismissed_flags.c.domain == domain,
                        dismissed_flags.c.fingerprint == fingerprint,
                    )
                )
            )


