"""Table definitions (SQLAlchemy Core).

Every primary table carries ``project_id``. There is one value today
("linkhouse"), but adding the column now is free and backfilling it later is
not.

JSON columns are used where the shape is genuinely variable (MX record lists,
per-selector DKIM results, warning lists). Anything that gets filtered,
grouped, or sorted in the dashboard is a real column.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)

metadata = MetaData()


# --- Module 1: DMARC aggregate (rua) reports ---------------------------------

dmarc_reports = Table(
    "dmarc_reports",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("project_id", String(64), nullable=False, index=True),
    # Dedupe key is (project_id, report_id, org_name, date_begin, date_end).
    # report_id alone is not unique across reporting organisations.
    Column("report_id", String(255), nullable=False),
    Column("org_name", String(255), nullable=False),
    Column("org_email", String(255)),
    Column("date_begin", DateTime, nullable=False),
    Column("date_end", DateTime, nullable=False),
    Column("policy_domain", String(255), nullable=False, index=True),
    Column("policy_p", String(32)),
    Column("policy_sp", String(32)),
    Column("policy_pct", Integer),
    Column("policy_adkim", String(8)),
    Column("policy_aspf", String(8)),
    # Path to the archived raw XML, kept so scoring logic can be re-run over
    # history if the classification rules change.
    Column("raw_xml_path", Text),
    Column("source_mailbox", String(128)),
    Column("ingested_at", DateTime, nullable=False),
    UniqueConstraint(
        "project_id",
        "report_id",
        "org_name",
        "date_begin",
        "date_end",
        name="uq_dmarc_report_identity",
    ),
    Index("ix_dmarc_reports_domain_range", "policy_domain", "date_begin"),
)


dmarc_records = Table(
    "dmarc_records",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("project_id", String(64), nullable=False, index=True),
    Column("report_id_fk", Integer, ForeignKey("dmarc_reports.id"), nullable=False, index=True),
    Column("policy_domain", String(255), nullable=False, index=True),
    Column("date_begin", DateTime, nullable=False, index=True),
    Column("source_ip", String(64), nullable=False, index=True),
    # Reverse DNS of source_ip when the report provides it — this is what the
    # forwarder-pattern match runs against.
    Column("source_host", String(255)),
    # Classification of the SENDING source (own infra, a known forwarder, ...).
    Column("source_esp", String(64), index=True),
    # Classification of the RECEIVING provider that produced the report,
    # derived from the report's org_name and denormalised onto the record.
    # This is the axis the dashboard groups by: "is Google unhappy, or Yahoo?"
    # — the answer comes from who wrote the report, not from the source IP.
    Column("receiving_esp", String(64), index=True),
    Column("org_name", String(255), index=True),
    Column("message_count", Integer, nullable=False, default=0),
    Column("disposition", String(32)),
    Column("dkim_aligned", Boolean),
    Column("spf_aligned", Boolean),
    Column("dkim_result", String(32)),
    Column("spf_result", String(32)),
    Column("header_from", String(255)),
    Column("envelope_from", String(255)),
    # 'pass' | 'failed' | 'forwarded' — 'forwarded' is a first-class outcome,
    # not a UI-level filter over failures. See classify/forwarding.py.
    Column("evaluation", String(16), nullable=False, index=True),
    Column("evaluation_reason", Text),
    Index("ix_dmarc_records_domain_esp_date", "policy_domain", "receiving_esp", "date_begin"),
)


# --- Module 2: DNS verification ----------------------------------------------

dns_checks = Table(
    "dns_checks",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("project_id", String(64), nullable=False, index=True),
    Column("domain", String(255), nullable=False, index=True),
    Column("checked_at", DateTime, nullable=False, index=True),
    # SPF
    Column("spf_present", Boolean, nullable=False, default=False),
    Column("spf_record", Text),
    Column("spf_valid", Boolean),
    Column("spf_lookup_count", Integer),
    Column("spf_lookup_limit", Integer, default=10),
    Column("spf_includes", JSON),
    Column("spf_error", Text),
    # DMARC
    Column("dmarc_present", Boolean, nullable=False, default=False),
    Column("dmarc_record", Text),
    Column("dmarc_policy", String(32)),
    Column("dmarc_subdomain_policy", String(32)),
    Column("dmarc_pct", Integer),
    Column("dmarc_rua", JSON),
    Column("dmarc_error", Text),
    # DKIM — one entry per configured selector.
    Column("dkim_results", JSON),
    # MX
    Column("mx_records", JSON),
    Column("mx_error", Text),
    # Derived, human-readable warnings produced at check time.
    Column("warnings", JSON),
    Index("ix_dns_checks_domain_time", "domain", "checked_at"),
)


# --- Module 3: bounces / NDRs -------------------------------------------------

bounces = Table(
    "bounces",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("project_id", String(64), nullable=False, index=True),
    Column("sending_domain", String(255), nullable=False, index=True),
    Column("mailbox_name", String(128), nullable=False),
    # Message-Id of the NDR itself, used for dedupe across polls.
    Column("message_id", String(512), nullable=False),
    Column("received_at", DateTime, nullable=False, index=True),
    Column("status_code", String(16), index=True),
    Column("smtp_code", String(8)),
    # 'hard' | 'soft' | 'sender_block' | 'unknown'
    Column("bounce_class", String(32), nullable=False, index=True),
    Column("bounce_reason", Text),
    Column("diagnostic_code", Text),
    Column("reporting_mta", String(255)),
    Column("remote_mta", String(255)),
    # Recipient is stored hashed. The domain part is kept in clear because
    # "which provider is rejecting us" is the whole diagnostic question.
    Column("recipient_hash", String(64), index=True),
    Column("recipient_domain", String(255), index=True),
    Column("recipient_esp", String(64), index=True),
    # Set when the DSN could not be parsed into structured fields — the full
    # text is kept so nothing is silently dropped.
    Column("parse_ok", Boolean, nullable=False, default=True),
    Column("raw_text", Text),
    Column("subject", Text),
    Column("ingested_at", DateTime, nullable=False),
    UniqueConstraint("project_id", "message_id", "mailbox_name", name="uq_bounce_identity"),
    Index("ix_bounces_domain_class_time", "sending_domain", "bounce_class", "received_at"),
)


# --- Module 4: DNSBL ----------------------------------------------------------

blacklist_checks = Table(
    "blacklist_checks",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("project_id", String(64), nullable=False, index=True),
    Column("domain", String(255), nullable=False, index=True),
    Column("ip", String(64), nullable=False, index=True),
    Column("ip_source", String(64)),  # 'spf' | 'mx' — where the IP came from
    Column("checked_at", DateTime, nullable=False, index=True),
    Column("listed", Boolean, nullable=False, default=False),
    Column("listed_by", JSON),  # names of the DNSBLs listing this IP
    Column("providers_checked", Integer),
    Column("detail_text", Text),
    Column("error", Text),
    Index("ix_blacklist_domain_time", "domain", "checked_at"),
)


# --- Ingestion health ---------------------------------------------------------

ingestion_runs = Table(
    "ingestion_runs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("project_id", String(64), nullable=False, index=True),
    # 'rua' | 'bounce' | 'dns' | 'dnsbl'
    Column("stream", String(32), nullable=False, index=True),
    Column("started_at", DateTime, nullable=False),
    Column("finished_at", DateTime),
    # 'running' | 'ok' | 'error'
    Column("status", String(32), nullable=False),
    Column("items_seen", Integer, default=0),
    Column("items_ingested", Integer, default=0),
    Column("error", Text),
    Column("detail", JSON),
    Index("ix_ingestion_runs_stream_time", "stream", "started_at"),
)


# --- Configuration held in the database --------------------------------------
#
# Domains and mailboxes started life in config/*.yml. They moved here so they
# can be managed from the dashboard; the YAML files are still read once, to
# seed these tables on first run, and remain a valid way to bootstrap.

domains = Table(
    "domains",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("project_id", String(64), nullable=False, index=True),
    Column("name", String(255), nullable=False),
    # DKIM selectors cannot be discovered from DNS, so they are configured
    # explicitly. An empty list means "do not check DKIM for this domain".
    Column("dkim_selectors", JSON),
    Column("notes", Text),
    Column("enabled", Boolean, nullable=False, default=True),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    UniqueConstraint("project_id", "name", name="uq_domain_name"),
)


mailboxes = Table(
    "mailboxes",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("project_id", String(64), nullable=False, index=True),
    Column("name", String(128), nullable=False),
    # 'rua'    — receives DMARC aggregate reports (Module 1)
    # 'bounce' — a sending mailbox that receives NDRs (Module 3)
    Column("kind", String(16), nullable=False, index=True),
    Column("host", String(255), nullable=False),
    Column("port", Integer, nullable=False, default=993),
    Column("ssl", Boolean, nullable=False, default=True),
    Column("username", String(255), nullable=False),
    # Fernet ciphertext — see deliverability/secrets.py. The key lives in .env,
    # never here, so this column on its own does not disclose the password.
    Column("password_encrypted", Text),
    # Legacy path: mailboxes seeded from YAML may still name an env var
    # instead of carrying an encrypted password.
    Column("password_env", String(128)),
    Column("folder", String(255), nullable=False, default="INBOX"),
    Column("processed_folder", String(255)),
    # Only meaningful for kind='bounce': which sending domain the NDRs belong to.
    Column("domain", String(255)),
    Column("enabled", Boolean, nullable=False, default=True),
    # Result of the last "test connection" run, shown next to the mailbox.
    Column("last_test_at", DateTime),
    Column("last_test_ok", Boolean),
    Column("last_test_error", Text),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    UniqueConstraint("project_id", "name", name="uq_mailbox_name"),
)



# Flags a user has chosen to stop seeing on a domain's "what needs attention"
# list. Identified by a stable fingerprint per flag type (see health.py), not
# by row id — a dismissal is "hide this kind of thing on this domain", so it
# keeps applying as long as the same fingerprint keeps recurring.
dismissed_flags = Table(
    "dismissed_flags",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("project_id", String(64), nullable=False, index=True),
    Column("domain", String(255), nullable=False, index=True),
    Column("fingerprint", String(255), nullable=False),
    Column("dismissed_at", DateTime, nullable=False),
    UniqueConstraint("project_id", "domain", "fingerprint", name="uq_dismissed_flag"),
)


# Prototype: organisational grouping of domains ("Media", "Sales"...), separate
# from a mailbox's IMAP folder (mailboxes.folder above — same English word,
# unrelated concept). A domain can belong to several folders at once, hence a
# join table rather than a folder_id column on domains.
folders = Table(
    "folders",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("project_id", String(64), nullable=False, index=True),
    Column("name", String(255), nullable=False),
    Column("created_at", DateTime, nullable=False),
    UniqueConstraint("project_id", "name", name="uq_folder_name"),
)

domain_folders = Table(
    "domain_folders",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("project_id", String(64), nullable=False, index=True),
    Column("domain_id", Integer, ForeignKey("domains.id"), nullable=False),
    Column("folder_id", Integer, ForeignKey("folders.id"), nullable=False),
    UniqueConstraint("project_id", "domain_id", "folder_id", name="uq_domain_folder"),
)


ALL_TABLES = (
    dmarc_reports,
    dmarc_records,
    dns_checks,
    bounces,
    blacklist_checks,
    ingestion_runs,
    domains,
    mailboxes,
    dismissed_flags,
    folders,
    domain_folders,
)
