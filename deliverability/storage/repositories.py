"""Repositories — the only place that builds queries.

Ingestion and dashboard code calls these methods with plain Python values and
gets plain dicts back. No SQLAlchemy objects cross this boundary.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import and_, desc, func, select

from .database import Database
from .schema import (
    blacklist_checks,
    bounces,
    dmarc_records,
    dmarc_reports,
    dns_checks,
    ingestion_runs,
)
from .schema import domains as domains_table
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

    def daily_volume(self, since: dt.datetime, domain: Optional[str] = None) -> List[Dict[str, Any]]:
        """Message volume per day per domain, split by evaluation outcome."""
        day = func.date(dmarc_records.c.date_begin).label("day")
        conditions = [
            dmarc_records.c.project_id == self.project_id,
            dmarc_records.c.date_begin >= since,
        ]
        if domain:
            conditions.append(dmarc_records.c.policy_domain == domain)

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

    def daily_counts(self, since: dt.datetime, domain: Optional[str] = None) -> List[Dict[str, Any]]:
        day = func.date(bounces.c.received_at).label("day")
        conditions = [
            bounces.c.project_id == self.project_id,
            bounces.c.received_at >= since,
        ]
        if domain:
            conditions.append(bounces.c.sending_domain == domain)

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

    def fail_stale_running(self, older_than_minutes: int = 60) -> int:
        """Mark long-abandoned 'running' rows as errors.

        A process killed mid-run leaves its row at 'running' forever, which
        would otherwise block that stream from ever being started again.
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
        """
        stmt = (
            select(
                ingestion_runs.c.stream,
                func.max(ingestion_runs.c.finished_at).label("finished_at"),
            )
            .where(
                and_(
                    ingestion_runs.c.project_id == self.project_id,
                    ingestion_runs.c.status == "ok",
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
        """Remove a domain from monitoring.

        Collected history (reports, bounces, DNS checks) is deliberately left
        in place — it is keyed by domain name, so re-adding the domain later
        picks its past straight back up.
        """
        with self.db.connect() as conn:
            conn.execute(
                domains_table.delete().where(
                    and_(domains_table.c.id == domain_id, domains_table.c.project_id == self.project_id)
                )
            )

    def count(self) -> int:
        stmt = select(func.count(domains_table.c.id)).where(domains_table.c.project_id == self.project_id)
        with self.db.connect() as conn:
            return int(conn.execute(stmt).scalar() or 0)


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


