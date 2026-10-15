"""Slack alerts: a sending domain went quiet, or can't be checked.

MVP without Extender's API, so there is no planned campaign volume to compare
against. "Too little" therefore means: in the last WINDOW_HOURS, a domain sent
less than ALERT_RATIO of what it usually sends in the same hours of the same
weekday (median of the previous BASELINE_WEEKS weeks). Comparing like-for-like
weekday and hours is what keeps weekends and nights — when Extender doesn't
send at all — from alerting every time.

Volume counts everything from the Sent folder except Instantly warm-up:
Extender campaigns (first mails and follow-ups) *and* 'unrecognized'. Counting
only 'extender' would turn a change in Extender's email format (2026-10-14:
every message suddenly 'unrecognized') into a false "silent" alert on every
domain at once; that case gets its own, separate classifier_drift alert.

Each condition is announced once when it starts, reminded every
REMIND_AFTER_HOURS while it lasts, and announced once more when it clears —
state lives in the alert_state table. Nothing is evaluated while the
Sent-folder import itself is stale (stale alert instead), and a domain whose
mailbox couldn't be read gets a mailbox_error alert instead of a silence one,
since its counts aren't trustworthy.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import statistics
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from .config import Settings, load_domains, load_sent_mailboxes
from .health import domain_statuses
from .i18n import translate
from .storage import AlertStateRepository, Database, IngestionRunRepository, SentRepository, get_database

logger = logging.getLogger(__name__)

WINDOW_HOURS = 12
BASELINE_WEEKS = 4
# Fewer usable weeks than this and the "usual" figure is too thin to judge.
MIN_BASELINE_WEEKS = 2
# A domain that usually sends fewer than this in the window is never alerted
# on — avoids noise on tiny or off-hours windows.
MIN_EXPECTED = 20
ALERT_RATIO = 0.10
# Hysteresis: once alerted, a domain counts as recovered only at this share of
# its usual volume, so hovering around 10% doesn't flap open/closed hourly.
RECOVER_RATIO = 0.30
STALE_AFTER_HOURS = 3
REMIND_AFTER_HOURS = 24
DRIFT_MIN_MESSAGES = 50
DRIFT_SHARE = 0.5
MANY_SILENT = 5

FLEET = "*"


def _aware(value: Any) -> Optional[dt.datetime]:
    """SQLite hands back naive datetimes; they are UTC."""
    if not isinstance(value, dt.datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


def _local(value: dt.datetime) -> str:
    try:
        from zoneinfo import ZoneInfo

        value = value.astimezone(ZoneInfo("Europe/Warsaw"))
    except Exception:  # noqa: BLE001 — no tz database: UTC is still correct, just less friendly
        value = value.astimezone(dt.timezone.utc)
    return value.strftime("%d.%m %H:%M")


def _split(rows: List[Dict[str, Any]]) -> Tuple[Dict[str, int], Dict[str, int], Dict[str, int]]:
    """(non-Instantly, Instantly, unrecognized) counts per domain."""
    volume: Dict[str, int] = {}
    instantly: Dict[str, int] = {}
    unrecognized: Dict[str, int] = {}
    for row in rows:
        domain, count = row["domain"], row["count"] or 0
        if row["source"] == "instantly":
            instantly[domain] = instantly.get(domain, 0) + count
        else:
            volume[domain] = volume.get(domain, 0) + count
            if row["source"] == "unrecognized":
                unrecognized[domain] = unrecognized.get(domain, 0) + count
    return volume, instantly, unrecognized


def post_to_slack(text: str, webhook_url: str) -> None:
    request = urllib.request.Request(
        webhook_url,
        data=json.dumps({"text": text}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        body = response.read().decode("utf-8", errors="replace")
        if response.status != 200 or body.strip() != "ok":
            raise RuntimeError(f"Slack webhook answered {response.status}: {body[:200]}")


def evaluate(
    settings: Settings,
    database: Database,
    now: Optional[dt.datetime] = None,
    lang: str = "pl",
    window_hours: int = WINDOW_HOURS,
) -> List[Dict[str, Any]]:
    """Work out which alerts to send right now, without sending anything.

    Returns events: ``{"domain", "kind", "action": open|remind|resolve,
    "text", "state": fields to save once the message is delivered}``.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    states = AlertStateRepository(database, settings.project_id).all()
    runs = IngestionRunRepository(database, settings.project_id)
    sent_repo = SentRepository(database, settings.project_id)
    dashboard = os.environ.get("DASHBOARD_URL", "").rstrip("/")
    events: List[Dict[str, Any]] = []

    def t(key: str, **params: Any) -> str:
        return translate(key, lang, **params)

    def link(path: str) -> str:
        return t("alert.link", url=f"{dashboard}{path}") if dashboard else ""

    def transition(domain: str, kind: str, firing: bool, open_text: str, remind_text: str,
                   resolve_text: str, detail: Optional[Dict[str, Any]] = None) -> None:
        state = states.get((domain, kind)) or {}
        active = bool(state.get("active"))
        if firing and not active:
            events.append({"domain": domain, "kind": kind, "action": "open", "text": open_text,
                           "state": {"active": True, "opened_at": now, "last_notified_at": now, "detail": detail}})
        elif firing and active:
            last = _aware(state.get("last_notified_at"))
            if last is None or now - last >= dt.timedelta(hours=REMIND_AFTER_HOURS):
                events.append({"domain": domain, "kind": kind, "action": "remind", "text": remind_text,
                               "state": {"last_notified_at": now, "detail": detail}})
        elif not firing and active:
            events.append({"domain": domain, "kind": kind, "action": "resolve", "text": resolve_text,
                           "state": {"active": False, "last_notified_at": now, "detail": detail}})

    def since_text(domain: str, kind: str) -> str:
        opened = _aware((states.get((domain, kind)) or {}).get("opened_at"))
        return _local(opened) if opened else "?"

    # --- Is there fresh data at all? ----------------------------------------
    last_ok = runs.last_success_run("sent")
    finished = _aware(last_ok["finished_at"]) if last_ok else None
    stale = finished is None or now - finished > dt.timedelta(hours=STALE_AFTER_HOURS)
    if finished is None:
        stale_text = t("alert.stale.never")
    else:
        stale_text = t("alert.stale.open", hours=int((now - finished).total_seconds() // 3600), when=_local(finished))
    transition(FLEET, "stale", stale, stale_text, stale_text, t("alert.stale.resolved"))
    if stale:
        # Without fresh rows every domain looks silent — judging them now would
        # be exactly the false alarm this module must not raise.
        return events

    # Data is complete up to when the last successful import started.
    window_end = _aware(last_ok["started_at"])
    window_start = window_end - dt.timedelta(hours=window_hours)

    monitored = {d.name for d in load_domains(database, settings)}
    mailboxes = [m for m in load_sent_mailboxes(database, settings) if m.domain in monitored]
    mailbox_by_name = {m.name: m for m in mailboxes}
    domains = sorted({m.domain for m in mailboxes})

    # --- Mailboxes the last import couldn't read ------------------------------
    detail = last_ok.get("detail") or {}
    broken: Dict[str, Tuple[str, str]] = {}
    for name, stats in detail.items():
        if isinstance(stats, dict) and stats.get("error") and name in mailbox_by_name:
            broken[mailbox_by_name[name].domain] = (name, str(stats["error"])[:300])
    for domain in domains:
        mailbox_name, error = broken.get(domain, ("", ""))
        open_text = t("alert.mailbox.open", domain=domain, mailbox=mailbox_name, error=error)
        if any(token in error.lower() for token in ("authenticationfailed", "invalid credentials", "authentication")):
            open_text += "\n" + t("alert.mailbox.auth_hint")
        transition(
            domain, "mailbox_error", domain in broken,
            "\n".join(filter(None, [open_text, link("/settings")])),
            t("alert.mailbox.remind", domain=domain, since=since_text(domain, "mailbox_error"), error=error),
            t("alert.mailbox.resolved", domain=domain),
            {"mailbox": mailbox_name, "error": error} if error else None,
        )

    # --- Volume vs. the same window in previous weeks -------------------------
    volume, instantly, unrecognized = _split(sent_repo.counts_between(window_start, window_end))
    span = sent_repo.sent_span_per_domain()
    baselines: Dict[str, List[int]] = {d: [] for d in domains}
    instantly_baseline: Dict[str, List[int]] = {d: [] for d in domains}
    for week in range(1, BASELINE_WEEKS + 1):
        shift = dt.timedelta(days=7 * week)
        start, end = window_start - shift, window_end - shift
        week_volume, week_instantly, _ = _split(sent_repo.counts_between(start, end))
        for domain in domains:
            first = _aware((span.get(domain) or {}).get("first"))
            # A week before this domain's first stored send isn't silence,
            # it's before monitoring started — leave it out of the median.
            if first is None or first > start:
                continue
            baselines[domain].append(week_volume.get(domain, 0))
            instantly_baseline[domain].append(week_instantly.get(domain, 0))

    hours = window_hours
    silent_now: List[str] = []
    contexts: Dict[str, str] = {}
    for domain in domains:
        if domain in broken:
            continue  # counts unreliable; the mailbox_error alert covers it
        history = baselines[domain]
        if len(history) < MIN_BASELINE_WEEKS:
            continue
        expected = statistics.median(history)
        actual = volume.get(domain, 0)
        state = states.get((domain, "silence")) or {}
        if state.get("active"):
            firing = expected >= MIN_EXPECTED and actual < RECOVER_RATIO * expected
        else:
            firing = expected >= MIN_EXPECTED and actual < ALERT_RATIO * expected
        if firing:
            silent_now.append(domain)

        expected_text = int(round(expected))
        lines = [
            t("alert.silence.numbers", hours=hours, actual=actual, expected=expected_text, weeks=len(history)),
        ]
        last = _aware((span.get(domain) or {}).get("last"))
        if last:
            lines.append(t("alert.silence.last_sent", when=_local(last), ago=int((now - last).total_seconds() // 3600)))
        if instantly.get(domain, 0) > 0:
            lines.append(t("alert.silence.hint_mailbox_ok", instantly=instantly[domain]))
        elif instantly_baseline[domain] and statistics.median(instantly_baseline[domain]) > 0:
            lines.append(t("alert.silence.hint_mailbox_dead"))
        else:
            lines.append(t("alert.silence.hint_generic"))
        contexts[domain] = "\n".join(lines)

        heading_key = "alert.silence.open_zero" if actual == 0 else "alert.silence.open_low"
        transition(
            domain, "silence", firing,
            t(heading_key, domain=domain, hours=hours),
            t("alert.silence.remind", domain=domain, since=since_text(domain, "silence")),
            t("alert.silence.resolved", domain=domain, hours=hours, actual=actual, expected=expected_text),
            {"actual": actual, "expected": expected, "window_end": window_end.isoformat()},
        )

    # Body under each opened/reminded silence heading: numbers, interpretation,
    # anything else ruaReader already knows about the domain, and a link.
    flagged = [e for e in events if e["kind"] == "silence" and e["action"] in ("open", "remind")]
    if flagged:
        flagged_domains = {e["domain"] for e in flagged}
        # Same 7-day view as the dashboard: a 1-day window would almost always
        # say "no DMARC data", since aggregate reports arrive 24-72h late.
        statuses = domain_statuses(
            settings, database,
            domains=[d for d in load_domains(database, settings) if d.name in flagged_domains],
            window_days=7,
        )
        headlines = {s.domain: s.localize(lang) for s in statuses}
        for event in flagged:
            domain = event["domain"]
            extra = [contexts.get(domain, "")]
            view = headlines.get(domain)
            if view and view["severity"] in ("critical", "warning"):
                extra.append(t("alert.silence.context", headline=view["headline"]))
            extra.append(link(f"/domain/{domain}"))
            event["text"] = "\n".join(filter(None, [event["text"], *extra]))

    # --- Fleet-wide: did a tool change its email format? ----------------------
    total = sum(volume.values())
    unrec_total = sum(unrecognized.values())
    share = (unrec_total / total) if total else 0.0
    pct = int(round(share * 100))
    transition(
        FLEET, "classifier_drift", total >= DRIFT_MIN_MESSAGES and share >= DRIFT_SHARE,
        t("alert.drift.open", pct=pct, hours=hours), t("alert.drift.open", pct=pct, hours=hours),
        t("alert.drift.resolved", pct=pct),
    )

    if len(silent_now) >= MANY_SILENT and any(e["kind"] == "silence" and e["action"] == "open" for e in events):
        events.insert(0, {"domain": FLEET, "kind": "note", "action": "note",
                          "text": t("alert.many_silent", n=len(silent_now)), "state": None})
    return events


def run(
    settings: Optional[Settings] = None,
    database: Optional[Database] = None,
    dry_run: bool = False,
    lang: str = "pl",
    window_hours: int = WINDOW_HOURS,
) -> Dict[str, Any]:
    """Evaluate and, unless ``dry_run``, post one combined Slack message.

    State is only saved after Slack accepted the message, so a failed post is
    retried on the next run instead of being silently marked as delivered.
    """
    settings = settings or Settings.from_env()
    database = database or get_database(settings)
    events = evaluate(settings, database, lang=lang, window_hours=window_hours)
    real = [e for e in events if e["state"] is not None]
    if not real:
        return {"status": "ok", "sent": False, "events": []}

    now = dt.datetime.now(dt.timezone.utc)
    text = "\n\n".join([translate("alert.header", lang, when=_local(now))] + [e["text"] for e in events])
    summary = [{"domain": e["domain"], "kind": e["kind"], "action": e["action"]} for e in real]

    if dry_run:
        return {"status": "ok", "sent": False, "dry_run": True, "events": summary, "text": text}

    webhook = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if not webhook:
        return {"status": "error", "sent": False, "error": "SLACK_WEBHOOK_URL is not set", "events": summary}

    post_to_slack(text, webhook)
    repo = AlertStateRepository(database, settings.project_id)
    for event in real:
        repo.save(event["domain"], event["kind"], **event["state"])
    return {"status": "ok", "sent": True, "events": summary}
