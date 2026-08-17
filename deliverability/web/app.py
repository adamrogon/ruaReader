"""Dashboard application.

Deliberately a small server-rendered app rather than an SPA: templates for the
pages, a handful of JSON endpoints for the charts.

The overview is built to answer one question quickly — "does any domain have a
problem today, and with which provider" — so it opens on the urgency-ordered
domain list with the per-ESP breakdown one click away.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import jobs
from ..classify.esp import ESP_DISPLAY_ORDER
from ..config import Mailbox, Settings, load_domains
from ..health import STALENESS_HOURS, domain_statuses, ingestion_health, localize_ingestion_health
from ..i18n import DEFAULT_LANG, SUPPORTED_LANGS, resolve_lang, translate
from ..secrets import SecretsError
from ..secrets import encrypt as encrypt_secret
from ..secrets import is_configured as secret_key_configured
from ..storage import (
    AcknowledgementRepository,
    BounceRepository,
    DmarcRepository,
    DnsRepository,
    DomainConfigRepository,
    MailboxConfigRepository,
    get_database,
)
from ..validate import test_domain, test_mailbox

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Deliverability Monitor", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

DEFAULT_WINDOW_DAYS = 7
LANG_COOKIE = "lang"


def _context() -> Dict[str, Any]:
    settings = Settings.from_env()
    return {"settings": settings, "database": get_database(settings)}


def _resolve_request_lang(request: Request) -> str:
    """?lang= wins, falling back to the cookie set by a previous visit."""
    requested = request.query_params.get("lang") or request.cookies.get(LANG_COOKIE)
    return resolve_lang(requested)


def _render(request: Request, template: str, lang: str, extra: Dict[str, Any]) -> Response:
    """TemplateResponse plus the i18n context every page needs, and a cookie
    so the chosen language survives a visit that doesn't carry ?lang=."""
    other_lang = next(candidate for candidate in SUPPORTED_LANGS if candidate != lang)
    context = {
        "lang": lang,
        "other_lang": other_lang,
        "t": lambda key, **kw: translate(key, lang, **kw),
        **extra,
    }
    response = templates.TemplateResponse(request, template, context)
    response.set_cookie(LANG_COOKIE, lang, max_age=60 * 60 * 24 * 365, samesite="lax")
    return response


def _since(days: int) -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)


def _day_key(value: Any) -> str:
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    return str(value)[:10]


def _date_axis(days: int) -> List[str]:
    """A continuous date axis, so gaps in reporting are visible as gaps."""
    today = dt.datetime.now(dt.timezone.utc).date()
    return [(today - dt.timedelta(days=offset)).isoformat() for offset in range(days - 1, -1, -1)]


@app.get("/")
def overview(request: Request, days: int = Query(DEFAULT_WINDOW_DAYS, ge=1, le=90)):
    lang = _resolve_request_lang(request)
    ctx = _context()
    raw_statuses = domain_statuses(ctx["settings"], ctx["database"], window_days=days)
    # Resolved to the active language once here; nothing downstream needs to
    # know a second language exists.
    statuses = [s.localize(lang) for s in raw_statuses]
    health = localize_ingestion_health(ingestion_health(ctx["database"], ctx["settings"]), lang)

    totals = {
        "domains": len(statuses),
        "critical": sum(1 for s in statuses if s["severity"] == "critical"),
        "warning": sum(1 for s in statuses if s["severity"] == "warning"),
        "ok": sum(1 for s in statuses if s["severity"] in ("ok", "info")),
        "sender_blocks": sum(s["metrics"].get("bounces_sender_block", 0) for s in statuses),
        "blacklisted": sum(1 for s in statuses if s["metrics"].get("blacklisted_ips", 0)),
        "messages": sum(s["metrics"].get("messages", 0) for s in statuses),
    }

    # Fleet-wide per-ESP totals — the "is it Google or Yahoo" question asked
    # across every domain at once.
    esp_totals: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"esp": "", "pass": 0, "failed": 0, "forwarded": 0, "domains": set()}
    )
    for status in statuses:
        for row in status["esp_rows"]:
            entry = esp_totals[row["esp"]]
            entry["esp"] = row["esp"]
            entry["pass"] += row["pass"]
            entry["failed"] += row["failed"]
            entry["forwarded"] += row["forwarded"]
            if row["failed"]:
                entry["domains"].add(status["domain"])

    esp_summary = []
    for entry in esp_totals.values():
        evaluated = entry["pass"] + entry["failed"]
        esp_summary.append(
            {
                "esp": entry["esp"],
                "pass": entry["pass"],
                "failed": entry["failed"],
                "forwarded": entry["forwarded"],
                "total": evaluated + entry["forwarded"],
                "evaluated": evaluated,
                "compliance": (entry["pass"] / evaluated) if evaluated else None,
                "affected_domains": sorted(entry["domains"]),
            }
        )
    esp_summary.sort(key=lambda e: (ESP_DISPLAY_ORDER.get(e["esp"], 50), -e["total"]))

    return _render(
        request,
        "overview.html",
        lang,
        {
            "statuses": statuses,
            "health": health,
            "totals": totals,
            "esp_summary": esp_summary,
            "days": days,
            "staleness_hours": STALENESS_HOURS,
        },
    )


@app.get("/domain/{domain}")
def domain_detail(request: Request, domain: str, days: int = Query(DEFAULT_WINDOW_DAYS, ge=1, le=90)):
    lang = _resolve_request_lang(request)
    ctx = _context()
    settings, database = ctx["settings"], ctx["database"]

    configured = {d.name: d for d in load_domains()}
    if domain not in configured:
        raise HTTPException(status_code=404, detail=f"{domain} is not in config/domains.yml")

    raw_statuses = domain_statuses(settings, database, domains=[configured[domain]], window_days=days)
    status = raw_statuses[0].localize(lang)

    dns_row = DnsRepository(database, settings.project_id).latest_per_domain().get(domain)
    bounce_repo = BounceRepository(database, settings.project_id)
    since = _since(days)

    return _render(
        request,
        "domain.html",
        lang,
        {
            "status": status,
            "domain": domain,
            "dns": dns_row,
            "bounce_codes": bounce_repo.counts_by_code(since, domain),
            "recent_bounces": bounce_repo.recent(since, domain, limit=40),
            "sender_blocks": bounce_repo.sender_blocks(since, domain),
            "top_failing": DmarcRepository(database, settings.project_id).top_failing_sources(
                since, domain, limit=12
            ),
            "days": days,
            "notes": configured[domain].notes,
            "selectors": configured[domain].dkim_selectors,
        },
    )


@app.get("/api/volume")
def api_volume(domain: Optional[str] = None, days: int = Query(14, ge=1, le=90)) -> JSONResponse:
    """Daily DMARC message volume split by outcome."""
    ctx = _context()
    repo = DmarcRepository(ctx["database"], ctx["settings"].project_id)
    rows = repo.daily_volume(_since(days), domain)

    axis = _date_axis(days)
    series = {key: {day: 0 for day in axis} for key in ("pass", "forwarded", "failed")}
    for row in rows:
        day = _day_key(row["day"])
        if day in series[row["evaluation"]]:
            series[row["evaluation"]][day] += row["messages"] or 0

    compliance = []
    for day in axis:
        passed, failed = series["pass"][day], series["failed"][day]
        evaluated = passed + failed
        compliance.append(round(passed / evaluated * 100, 1) if evaluated else None)

    return JSONResponse(
        {
            "labels": axis,
            "passed": [series["pass"][d] for d in axis],
            "forwarded": [series["forwarded"][d] for d in axis],
            "failed": [series["failed"][d] for d in axis],
            "compliance": compliance,
        }
    )


@app.get("/api/bounces")
def api_bounces(domain: Optional[str] = None, days: int = Query(14, ge=1, le=90)) -> JSONResponse:
    """Daily bounce counts split by class."""
    ctx = _context()
    repo = BounceRepository(ctx["database"], ctx["settings"].project_id)
    rows = repo.daily_counts(_since(days), domain)

    axis = _date_axis(days)
    classes = ("sender_block", "hard", "soft", "unknown")
    series = {key: {day: 0 for day in axis} for key in classes}
    for row in rows:
        day = _day_key(row["day"])
        klass = row["bounce_class"]
        if klass in series and day in series[klass]:
            series[klass][day] += row["count"] or 0

    return JSONResponse(
        {"labels": axis, **{key: [series[key][d] for d in axis] for key in classes}}
    )


@app.get("/api/esp")
def api_esp(domain: Optional[str] = None, days: int = Query(7, ge=1, le=90)) -> JSONResponse:
    """Per-receiving-ESP outcome totals."""
    ctx = _context()
    repo = DmarcRepository(ctx["database"], ctx["settings"].project_id)
    rows = repo.esp_breakdown(_since(days), domain)

    by_esp: Dict[str, Dict[str, int]] = defaultdict(lambda: {"pass": 0, "failed": 0, "forwarded": 0})
    for row in rows:
        by_esp[row["esp"] or "Unknown"][row["evaluation"]] += row["messages"] or 0

    ordered = sorted(by_esp.items(), key=lambda kv: ESP_DISPLAY_ORDER.get(kv[0], 50))
    return JSONResponse(
        {
            "labels": [esp for esp, _ in ordered],
            "passed": [vals["pass"] for _, vals in ordered],
            "failed": [vals["failed"] for _, vals in ordered],
            "forwarded": [vals["forwarded"] for _, vals in ordered],
        }
    )


# --- Settings -----------------------------------------------------------------


def _selectors_from_form(raw: str) -> List[str]:
    """Parse the comma-separated selector field.

    An empty field means "do not check DKIM", which is different from leaving
    the domain's selectors at their defaults — so it is preserved as an empty
    list rather than silently substituted.
    """
    return [s.strip() for s in (raw or "").replace("\n", ",").split(",") if s.strip()]


@app.get("/settings")
def settings_page(request: Request, notice: Optional[str] = None, error: Optional[str] = None):
    lang = _resolve_request_lang(request)
    ctx = _context()
    settings, database = ctx["settings"], ctx["database"]

    domain_repo = DomainConfigRepository(database, settings.project_id)
    mailbox_repo = MailboxConfigRepository(database, settings.project_id)

    mailboxes = mailbox_repo.list_all()
    return _render(
        request,
        "settings.html",
        lang,
        {
            "domains": domain_repo.list_all(),
            "rua_mailboxes": [m for m in mailboxes if m["kind"] == "rua"],
            "bounce_mailboxes": [m for m in mailboxes if m["kind"] == "bounce"],
            "secret_key_ok": secret_key_configured(),
            "notice": notice,
            "error": error,
            "days": DEFAULT_WINDOW_DAYS,
            "page": "settings",
        },
    )


def _settings_redirect(lang: str, notice: Optional[str] = None, error: Optional[str] = None) -> RedirectResponse:
    params = [f"lang={lang}"]
    if notice:
        params.append(f"notice={notice}")
    if error:
        params.append(f"error={error}")
    # 303 so the browser re-issues as GET and a refresh does not resubmit.
    return RedirectResponse(f"/settings?{'&'.join(params)}", status_code=303)


@app.post("/settings/domains")
def save_domain(
    request: Request,
    domain_id: Optional[int] = Form(None),
    name: str = Form(...),
    dkim_selectors: str = Form(""),
    notes: str = Form(""),
    enabled: Optional[str] = Form(None),
):
    lang = _resolve_request_lang(request)
    ctx = _context()
    repo = DomainConfigRepository(ctx["database"], ctx["settings"].project_id)

    clean_name = (name or "").strip().lower()
    if not clean_name:
        return _settings_redirect(lang, error="required")

    selectors = _selectors_from_form(dkim_selectors)
    is_enabled = enabled is not None

    if domain_id:
        repo.update(
            domain_id, name=clean_name, dkim_selectors=selectors, notes=notes, enabled=is_enabled
        )
    else:
        if repo.get_by_name(clean_name):
            return _settings_redirect(lang, error="domain_exists")
        repo.create(name=clean_name, dkim_selectors=selectors, notes=notes, enabled=is_enabled)

    return _settings_redirect(lang, notice="domain_saved")


@app.post("/settings/domains/{domain_id}/delete")
def delete_domain(request: Request, domain_id: int):
    lang = _resolve_request_lang(request)
    ctx = _context()
    DomainConfigRepository(ctx["database"], ctx["settings"].project_id).delete(domain_id)
    return _settings_redirect(lang, notice="domain_deleted")


@app.post("/settings/mailboxes")
def save_mailbox(
    request: Request,
    mailbox_id: Optional[int] = Form(None),
    name: str = Form(...),
    kind: str = Form("rua"),
    host: str = Form(...),
    port: int = Form(993),
    ssl: Optional[str] = Form(None),
    username: str = Form(...),
    password: str = Form(""),
    folder: str = Form("INBOX"),
    processed_folder: str = Form(""),
    domain: str = Form(""),
    enabled: Optional[str] = Form(None),
):
    lang = _resolve_request_lang(request)
    ctx = _context()
    repo = MailboxConfigRepository(ctx["database"], ctx["settings"].project_id)

    clean_name = (name or "").strip()
    if not clean_name or not host.strip() or not username.strip():
        return _settings_redirect(lang, error="required")
    if kind == "bounce" and not domain.strip():
        return _settings_redirect(lang, error="required")

    fields: Dict[str, Any] = {
        "name": clean_name,
        "kind": kind if kind in ("rua", "bounce") else "rua",
        "host": host.strip(),
        "port": int(port),
        "ssl": ssl is not None,
        "username": username.strip(),
        "folder": (folder or "INBOX").strip(),
        "processed_folder": processed_folder.strip() or None,
        "domain": domain.strip().lower() or None,
        "enabled": enabled is not None,
    }

    # An empty password field on an edit means "keep what is stored", so it is
    # only written when the user actually typed something.
    if password:
        try:
            fields["password_encrypted"] = encrypt_secret(password)
            # A freshly typed password supersedes any .env reference.
            fields["password_env"] = None
        except SecretsError:
            return _settings_redirect(lang, error="no_secret_key")

    if mailbox_id:
        repo.update(mailbox_id, **fields)
    else:
        existing = [m for m in repo.list_all() if m["name"] == clean_name]
        if existing:
            return _settings_redirect(lang, error="mailbox_exists")
        repo.create(**fields)

    return _settings_redirect(lang, notice="mailbox_saved")


@app.post("/settings/mailboxes/{mailbox_id}/delete")
def delete_mailbox(request: Request, mailbox_id: int):
    lang = _resolve_request_lang(request)
    ctx = _context()
    MailboxConfigRepository(ctx["database"], ctx["settings"].project_id).delete(mailbox_id)
    return _settings_redirect(lang, notice="mailbox_deleted")


@app.post("/api/test/mailbox")
def api_test_mailbox(
    request: Request,
    mailbox_id: Optional[int] = Form(None),
    # Defaults rather than Form(...): FastAPI treats an empty form field as a
    # missing one and answers 422, which would surface as a blank error in the
    # UI instead of "fill in the required fields".
    host: str = Form(""),
    port: int = Form(993),
    ssl: Optional[str] = Form(None),
    username: str = Form(""),
    password: str = Form(""),
    folder: str = Form("INBOX"),
) -> JSONResponse:
    """Test IMAP credentials from the form, before anything is saved."""
    lang = _resolve_request_lang(request)
    ctx = _context()
    settings, database = ctx["settings"], ctx["database"]
    repo = MailboxConfigRepository(database, settings.project_id)

    if not host.strip() or not username.strip():
        return JSONResponse({"ok": False, "message": translate("test.fields_missing", lang)})

    stored_encrypted = None
    stored_env = None
    if mailbox_id and not password:
        # Editing an existing mailbox without retyping the password: test the
        # one already stored rather than refusing.
        row = repo.get(mailbox_id)
        if row:
            stored_encrypted = row.get("password_encrypted")
            stored_env = row.get("password_env")

    candidate = Mailbox(
        name="test",
        host=host.strip(),
        port=int(port),
        ssl=ssl is not None,
        username=username.strip(),
        folder=(folder or "INBOX").strip(),
        password_encrypted=stored_encrypted,
        password_env=stored_env,
    )

    result = test_mailbox(candidate, password=password or None)
    if mailbox_id:
        repo.record_test(
            mailbox_id,
            ok=result["ok"],
            error=None if result["ok"] else translate(result["message_key"], "en", **result["params"]),
        )

    return JSONResponse(
        {"ok": result["ok"], "message": translate(result["message_key"], lang, **result["params"])}
    )


@app.post("/api/test/domain")
def api_test_domain(
    request: Request, name: str = Form(""), dkim_selectors: str = Form("")
) -> JSONResponse:
    """Check a domain resolves and has the records the tool depends on."""
    lang = _resolve_request_lang(request)
    if not name.strip():
        return JSONResponse({"ok": False, "message": translate("test.fields_missing", lang)})

    result = test_domain(name, _selectors_from_form(dkim_selectors))
    return JSONResponse(
        {
            "ok": result["ok"],
            "warning": result.get("warning", False),
            "message": translate(result["message_key"], lang, **result["params"]),
        }
    )


# --- Run now ------------------------------------------------------------------


@app.post("/api/run/{stream}")
def api_run_stream(request: Request, stream: str) -> JSONResponse:
    """Trigger one ingestion stream on demand."""
    lang = _resolve_request_lang(request)
    ctx = _context()
    result = jobs.start(stream, ctx["settings"], ctx["database"])

    if result["started"]:
        message = translate("action.started", lang, stream=translate(f"stream.{stream}", lang))
    elif result["reason"] == "already_running":
        message = translate("action.already_running", lang, stream=translate(f"stream.{stream}", lang))
    else:
        message = result["reason"] or ""

    return JSONResponse({**result, "message": message})


@app.get("/api/run/status")
def api_run_status() -> JSONResponse:
    """Which streams are mid-run, for the UI to poll after pressing the button."""
    ctx = _context()
    return JSONResponse({"running": jobs.running_streams(ctx["settings"], ctx["database"])})


# --- Acknowledgements ---------------------------------------------------------


@app.post("/api/flags/{domain}/ack")
def api_ack_flag(
    request: Request,
    domain: str,
    fingerprint: str = Form(...),
    note: str = Form(""),
    evidence_at: str = Form(""),
) -> JSONResponse:
    """Mark a flag as handled.

    ``evidence_at`` is echoed back from the rendered flag so the
    acknowledgement is pinned to the evidence the user actually looked at —
    anything newer reopens it. See health._apply_acknowledgements.
    """
    ctx = _context()
    repo = AcknowledgementRepository(ctx["database"], ctx["settings"].project_id)

    parsed_evidence: Optional[dt.datetime] = None
    if evidence_at:
        try:
            parsed_evidence = dt.datetime.fromisoformat(evidence_at)
            if parsed_evidence.tzinfo is None:
                parsed_evidence = parsed_evidence.replace(tzinfo=dt.timezone.utc)
        except ValueError:
            parsed_evidence = None

    repo.acknowledge(domain, fingerprint, note=note.strip() or None, evidence_at=parsed_evidence)
    return JSONResponse({"ok": True})


@app.post("/api/flags/{domain}/unack")
def api_unack_flag(request: Request, domain: str, fingerprint: str = Form(...)) -> JSONResponse:
    """Undo an acknowledgement."""
    ctx = _context()
    AcknowledgementRepository(ctx["database"], ctx["settings"].project_id).clear(domain, fingerprint)
    return JSONResponse({"ok": True})


@app.get("/api/health")
def api_health(lang: str = DEFAULT_LANG) -> JSONResponse:
    """Ingestion freshness, for scripting or an external monitor.

    Defaults to Polish (the dashboard's default) but any consumer can ask for
    English with ?lang=en.
    """
    ctx = _context()
    rows = localize_ingestion_health(ingestion_health(ctx["database"], ctx["settings"]), resolve_lang(lang))
    return JSONResponse(
        [
            {
                **{k: v for k, v in row.items() if k not in ("message_params",)},
                "last_success": row["last_success"].isoformat() if row["last_success"] else None,
            }
            for row in rows
        ]
    )
