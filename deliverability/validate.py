"""Connection and configuration checks used by the Settings screen.

The point of these is to fail *while the user is still looking at the form*.
Without them, a wrong password or a typo'd host only shows up hours later as a
stale-data warning, and the tool looks broken rather than misconfigured.

Errors are returned as message keys plus params, like everything else the UI
displays, so they can be shown in either language. IMAP server text is passed
through verbatim as a parameter — it is diagnostic detail, not prose.
"""

from __future__ import annotations

import logging
import socket
import ssl
from typing import Any, Dict, Optional, Sequence

import dns.exception
import dns.resolver
from imapclient import IMAPClient
from imapclient.exceptions import IMAPClientError, LoginError

from .config import Mailbox

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT = 15


def test_mailbox(mailbox: Mailbox, password: Optional[str] = None) -> Dict[str, Any]:
    """Try to log in and open the configured folder.

    ``password`` lets the caller test a value typed into the form that has not
    been saved yet; when omitted the mailbox's stored password is used.

    Returns ``{"ok": bool, "message_key": str, "params": {...}}``. Never raises
    — a failed test is a normal outcome that the form needs to display.
    """
    try:
        secret = password if password is not None else mailbox.password
    except Exception as exc:  # noqa: BLE001 — covers ConfigError and SecretsError
        return {"ok": False, "message_key": "test.no_password", "params": {"error": str(exc)}}

    if not secret:
        return {"ok": False, "message_key": "test.no_password", "params": {"error": ""}}

    client = None
    try:
        client = IMAPClient(mailbox.host, port=mailbox.port, ssl=mailbox.ssl, timeout=CONNECT_TIMEOUT)
        client.login(mailbox.username, secret)

        # Logging in is not enough — the folder has to exist, and a wrong
        # folder name is a common and otherwise silent misconfiguration.
        if not client.folder_exists(mailbox.folder):
            available = []
            try:
                available = sorted(name for _, _, name in client.list_folders())[:12]
            except IMAPClientError:
                pass
            return {
                "ok": False,
                "message_key": "test.folder_missing",
                "params": {"folder": mailbox.folder, "available": ", ".join(available) or "—"},
            }

        client.select_folder(mailbox.folder, readonly=True)
        count = len(client.search(["ALL"]))
        return {
            "ok": True,
            "message_key": "test.ok",
            "params": {"folder": mailbox.folder, "count": count},
        }

    except LoginError as exc:
        return {"ok": False, "message_key": "test.login_failed", "params": {"error": str(exc)}}
    except socket.gaierror:
        return {"ok": False, "message_key": "test.host_unknown", "params": {"host": mailbox.host}}
    except (socket.timeout, TimeoutError):
        return {
            "ok": False,
            "message_key": "test.timeout",
            "params": {"host": mailbox.host, "port": mailbox.port},
        }
    except ssl.SSLError as exc:
        return {"ok": False, "message_key": "test.ssl_error", "params": {"error": str(exc)}}
    except (ConnectionRefusedError, OSError) as exc:
        return {
            "ok": False,
            "message_key": "test.connection_failed",
            "params": {"host": mailbox.host, "port": mailbox.port, "error": str(exc)},
        }
    except IMAPClientError as exc:
        return {"ok": False, "message_key": "test.imap_error", "params": {"error": str(exc)}}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error testing mailbox %s", mailbox.name)
        return {"ok": False, "message_key": "test.unexpected", "params": {"error": str(exc)}}
    finally:
        if client is not None:
            try:
                client.logout()
            except Exception:  # noqa: BLE001
                pass


def test_domain(name: str, dkim_selectors: Sequence[str] = ()) -> Dict[str, Any]:
    """Sanity-check a domain before it is added to monitoring.

    Deliberately not a full health check — that is Module 2's job, and it runs
    on a schedule. This only answers "does this domain exist and is it set up
    to report to us at all", which is what makes an entry worth saving.
    """
    resolver = dns.resolver.Resolver()
    resolver.timeout = 5
    resolver.lifetime = 5

    findings = []
    domain = name.strip().lower()

    if not domain or "." not in domain or " " in domain:
        return {"ok": False, "message_key": "test.domain_malformed", "params": {"domain": name}}

    # Does the domain resolve at all?
    try:
        resolver.resolve(domain, "SOA")
    except dns.resolver.NXDOMAIN:
        return {"ok": False, "message_key": "test.domain_nxdomain", "params": {"domain": domain}}
    except dns.resolver.NoAnswer:
        pass  # No SOA at this exact label is fine; it may be a subdomain.
    except (dns.resolver.NoNameservers, dns.exception.Timeout) as exc:
        return {
            "ok": False,
            "message_key": "test.domain_dns_error",
            "params": {"domain": domain, "error": str(exc)},
        }

    def _txt(qname: str) -> list:
        try:
            return ["".join(c.decode("utf-8", "replace") for c in r.strings) for r in resolver.resolve(qname, "TXT")]
        except Exception:  # noqa: BLE001
            return []

    has_spf = any(r.lower().startswith("v=spf1") for r in _txt(domain))
    dmarc_records = [r for r in _txt(f"_dmarc.{domain}") if r.lower().startswith("v=dmarc1")]
    has_dmarc = bool(dmarc_records)
    has_rua = any("rua=" in r.lower() for r in dmarc_records)

    found_selectors = [s for s in dkim_selectors if _txt(f"{s}._domainkey.{domain}")]

    if not has_dmarc:
        # Without DMARC there are no aggregate reports, so the domain would sit
        # in the dashboard permanently empty. Worth saying up front.
        findings.append("dmarc")
    elif not has_rua:
        findings.append("rua")
    if not has_spf:
        findings.append("spf")
    if dkim_selectors and not found_selectors:
        findings.append("dkim")

    if not findings:
        return {
            "ok": True,
            "message_key": "test.domain_ok",
            "params": {"domain": domain, "selectors": ", ".join(found_selectors) or "—"},
        }

    # A domain with gaps is still worth monitoring — that is rather the point —
    # so this is a warning, not a refusal to save.
    return {
        "ok": True,
        "warning": True,
        "message_key": "test.domain_gaps",
        "params": {"domain": domain, "gaps": ", ".join(findings)},
    }
