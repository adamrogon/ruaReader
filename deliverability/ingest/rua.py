"""Module 1 — DMARC aggregate (rua) report ingestion.

Reads report mail over IMAP, extracts the XML from `.zip`/`.gz`/raw `.xml`
attachments, and parses it with ``parsedmarc`` used as a library. The library is
used for parsing only; extraction, archiving, classification, and storage are
handled here so the post-parse data stays under our control.

Every report is archived as raw XML before it is normalised, so classification
rules can be changed later and replayed over the full history.
"""

from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import logging
import re
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import parsedmarc

from ..classify.esp import esp_from_org_name, esp_from_source
from ..classify.forwarding import classify_evaluation
from ..config import Mailbox, Settings, load_rua_mailboxes
from ..storage import Database, DmarcRepository, IngestionRunRepository, get_database
from .imap_client import fetch_messages, iter_attachments, move_to_folder, open_mailbox

logger = logging.getLogger(__name__)

STREAM = "rua"

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _parse_timestamp(value: Any) -> dt.datetime:
    """parsedmarc returns 'YYYY-MM-DD HH:MM:SS' strings; normalise to UTC."""
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return dt.datetime.strptime(value, fmt).replace(tzinfo=dt.timezone.utc)
            except ValueError:
                continue
    raise ValueError(f"Unrecognised timestamp: {value!r}")


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def extract_xml_documents(filename: str, payload: bytes) -> List[str]:
    """Get XML text out of an attachment payload.

    Handles zip (possibly containing several reports), gzip, and bare XML.
    ``parsedmarc.extract_report`` is tried first and covers most real mail; the
    manual paths below catch archives it declines.
    """
    documents: List[str] = []

    try:
        extracted = parsedmarc.extract_report(payload)
        if extracted and extracted.lstrip().startswith("<"):
            return [extracted]
    except Exception:  # noqa: BLE001 — fall through to the explicit handlers
        logger.debug("extract_report declined %s", filename, exc_info=True)

    lowered = (filename or "").lower()

    if payload[:2] == b"PK" or lowered.endswith(".zip"):
        try:
            with zipfile.ZipFile(BytesIO(payload)) as archive:
                for member in archive.namelist():
                    if member.endswith("/"):
                        continue
                    text = archive.read(member).decode("utf-8", errors="replace")
                    if text.lstrip().startswith("<"):
                        documents.append(text)
        except Exception:  # noqa: BLE001
            logger.debug("Not a readable zip: %s", filename, exc_info=True)

    elif payload[:2] == b"\x1f\x8b" or lowered.endswith((".gz", ".gzip")):
        try:
            documents.append(gzip.decompress(payload).decode("utf-8", errors="replace"))
        except Exception:  # noqa: BLE001
            logger.debug("Not readable gzip: %s", filename, exc_info=True)

    elif lowered.endswith(".xml") or payload.lstrip()[:1] == b"<":
        documents.append(payload.decode("utf-8", errors="replace"))

    return [d for d in documents if d.lstrip().startswith("<")]


def archive_xml(settings: Settings, policy_domain: str, org_name: str, report_id: str, xml: str) -> str:
    """Write the raw XML to the archive and return its path.

    Kept verbatim so a future change to the scoring rules can be replayed over
    everything already collected.
    """
    safe_domain = _SAFE_NAME.sub("_", policy_domain or "unknown")
    safe_org = _SAFE_NAME.sub("_", org_name or "unknown")
    safe_report = _SAFE_NAME.sub("_", report_id or "unknown")[:120]

    # A short digest keeps filenames unique when a reporter reuses report ids.
    digest = hashlib.sha256(xml.encode("utf-8")).hexdigest()[:12]
    directory = settings.archive_dir / safe_domain / safe_org
    directory.mkdir(parents=True, exist_ok=True)

    path = directory / f"{safe_report}_{digest}.xml"
    if not path.exists():
        path.write_text(xml, encoding="utf-8")
    return str(path)


def normalise_report(parsed: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, Any]], str]:
    """Turn a parsedmarc aggregate report into rows for storage.

    Returns ``(report_row, record_rows, receiving_esp)``.
    """
    meta = parsed.get("report_metadata", {}) or {}
    policy = parsed.get("policy_published", {}) or {}

    org_name = (meta.get("org_name") or "unknown").strip()
    receiving_esp = esp_from_org_name(org_name)

    date_begin = _parse_timestamp(meta.get("begin_date"))
    date_end = _parse_timestamp(meta.get("end_date"))

    report_row: Dict[str, Any] = {
        "report_id": str(meta.get("report_id") or "").strip(),
        "org_name": org_name,
        "org_email": meta.get("org_email"),
        "date_begin": date_begin,
        "date_end": date_end,
        "policy_domain": (policy.get("domain") or "").strip().lower(),
        "policy_p": policy.get("p"),
        "policy_sp": policy.get("sp"),
        "policy_pct": _as_int(policy.get("pct")),
        "policy_adkim": policy.get("adkim"),
        "policy_aspf": policy.get("aspf"),
    }

    record_rows: List[Dict[str, Any]] = []
    for record in parsed.get("records", []) or []:
        source = record.get("source", {}) or {}
        alignment = record.get("alignment", {}) or {}
        evaluated = record.get("policy_evaluated", {}) or {}
        identifiers = record.get("identifiers", {}) or {}
        auth = record.get("auth_results", {}) or {}

        source_host = source.get("reverse_dns") or source.get("name")

        # policy_evaluated carries the DMARC verdict; auth_results carries the
        # raw mechanism results. The raw results are what the forwarding rule
        # needs, so prefer them and fall back to the evaluated ones.
        dkim_entries = auth.get("dkim") or []
        spf_entries = auth.get("spf") or []
        dkim_result = (dkim_entries[0].get("result") if dkim_entries else None) or evaluated.get("dkim")
        spf_result = (spf_entries[0].get("result") if spf_entries else None) or evaluated.get("spf")

        evaluation, reason = classify_evaluation(
            dkim_result=dkim_result,
            spf_result=spf_result,
            source_host=source_host,
            dkim_aligned=alignment.get("dkim"),
            spf_aligned=alignment.get("spf"),
        )

        record_rows.append(
            {
                "source_ip": source.get("ip_address") or "",
                "source_host": source_host,
                "source_esp": esp_from_source(source_host, source.get("ip_address")),
                "message_count": _as_int(record.get("count")) or 0,
                "disposition": evaluated.get("disposition"),
                "dkim_aligned": alignment.get("dkim"),
                "spf_aligned": alignment.get("spf"),
                "dkim_result": dkim_result,
                "spf_result": spf_result,
                "header_from": identifiers.get("header_from"),
                "envelope_from": identifiers.get("envelope_from"),
                "evaluation": evaluation,
                "evaluation_reason": reason,
            }
        )

    return report_row, record_rows, receiving_esp


def ingest_mailbox(
    mailbox: Mailbox,
    repository: DmarcRepository,
    settings: Settings,
    since_days: int = 14,
    offline: bool = False,
) -> Dict[str, int]:
    """Pull and store every new aggregate report from one mailbox."""
    stats = {"messages": 0, "reports_found": 0, "reports_stored": 0, "duplicates": 0, "errors": 0}
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=since_days)).date()

    with open_mailbox(mailbox) as client:
        messages = fetch_messages(client, since=since)
        stats["messages"] = len(messages)

        for uid, message in messages:
            stored_any = False
            for filename, payload in iter_attachments(message):
                for xml in extract_xml_documents(filename, payload):
                    stats["reports_found"] += 1
                    try:
                        # Cheap, DNS-free parse first, purely to read the
                        # identity fields (report_id/org_name/dates) needed
                        # for the dedup check below. Without processed_folder
                        # configured, a mailbox gets rescanned in full on
                        # every run, and the vast majority of reports on any
                        # given run are ones already stored — paying for a
                        # live reverse-DNS lookup per source IP (offline=False)
                        # on every one of those, every time, is what made
                        # this stream take 60-100s for just two mailboxes.
                        # report_id/org_name/date_begin/date_end/policy_domain
                        # come straight from report_metadata/policy_published
                        # in the XML, not from anything DNS-derived, so this
                        # probe is exact — not an approximation.
                        probe = parsedmarc.parse_aggregate_report_xml(xml, offline=True)
                        probe_row, _, _ = normalise_report(probe)

                        if not probe_row["report_id"] or not probe_row["policy_domain"]:
                            logger.warning("Skipping report with no id/domain in %s", mailbox.name)
                            stats["errors"] += 1
                            continue

                        if repository.report_exists(
                            probe_row["report_id"],
                            probe_row["org_name"],
                            probe_row["date_begin"],
                            probe_row["date_end"],
                        ):
                            stats["duplicates"] += 1
                            stored_any = True
                            continue

                        # Only genuinely new reports pay for the full,
                        # reverse-DNS-resolving parse the forwarder
                        # classification depends on.
                        parsed = probe if offline else parsedmarc.parse_aggregate_report_xml(xml, offline=offline)
                        report_row, record_rows, receiving_esp = normalise_report(parsed)

                        report_row["raw_xml_path"] = archive_xml(
                            settings,
                            report_row["policy_domain"],
                            report_row["org_name"],
                            report_row["report_id"],
                            xml,
                        )
                        report_row["source_mailbox"] = mailbox.name

                        repository.insert_report(report_row, record_rows, receiving_esp)
                        stats["reports_stored"] += 1
                        stored_any = True
                    except Exception:  # noqa: BLE001
                        stats["errors"] += 1
                        logger.exception("Failed to process a report from %s", mailbox.name)

            if stored_any and mailbox.processed_folder:
                move_to_folder(client, uid, mailbox.processed_folder)

    return stats


def run(
    settings: Optional[Settings] = None,
    database: Optional[Database] = None,
    mailboxes: Optional[Sequence[Mailbox]] = None,
    since_days: int = 14,
    offline: bool = False,
) -> Dict[str, Any]:
    """Ingest every configured rua mailbox, recording the run for healthchecks."""
    settings = settings or Settings.from_env()
    database = database or get_database(settings)
    mailboxes = mailboxes if mailboxes is not None else load_rua_mailboxes()

    repository = DmarcRepository(database, settings.project_id)
    runs = IngestionRunRepository(database, settings.project_id)
    run_id = runs.start(STREAM)

    totals = {"messages": 0, "reports_found": 0, "reports_stored": 0, "duplicates": 0, "errors": 0}
    per_mailbox: Dict[str, Any] = {}
    failures: List[str] = []

    for mailbox in mailboxes:
        try:
            stats = ingest_mailbox(mailbox, repository, settings, since_days=since_days, offline=offline)
            per_mailbox[mailbox.name] = stats
            for key, value in stats.items():
                totals[key] = totals.get(key, 0) + value
        except Exception as exc:  # noqa: BLE001
            # One unreachable mailbox must not stop the others.
            logger.exception("rua ingestion failed for mailbox %s", mailbox.name)
            failures.append(f"{mailbox.name}: {exc}")
            per_mailbox[mailbox.name] = {"error": str(exc)}

    # "partial" matters as much as "error" once there is more than a handful
    # of mailboxes: one broken mailbox among thirty must not read as "ok"
    # just because the other twenty-nine succeeded.
    if not failures:
        status = "ok"
    elif len(failures) == len(mailboxes):
        status = "error"
    else:
        status = "partial"
    runs.finish(
        run_id,
        status=status,
        items_seen=totals["reports_found"],
        items_ingested=totals["reports_stored"],
        error="; ".join(failures) or None,
        detail=per_mailbox,
    )
    return {"status": status, "totals": totals, "mailboxes": per_mailbox, "errors": failures}
