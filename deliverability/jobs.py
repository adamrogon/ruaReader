"""Running ingestion on demand from the dashboard.

The scheduled path is cron calling ``deliverability.cli``. This adds a "check
now" button, which needs the same work to happen without blocking the HTTP
request — a DNS sweep over thirty domains takes far longer than a page load
should.

So each run goes onto a daemon thread and the request returns immediately; the
UI then polls for the result. Two guards stop the button from causing damage:
an in-process lock against double-clicks, and a database check against a run
already in flight (which also covers a cron job running at the same time).
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, Optional

from .config import Settings
from .storage import Database, IngestionRunRepository, get_database

logger = logging.getLogger(__name__)

# Streams that can be triggered from the UI, mapped to their runner.
STREAMS = ("rua", "dns", "bounce", "dnsbl")

_lock = threading.Lock()
_in_flight: Dict[str, threading.Thread] = {}


def _runner_for(stream: str) -> Callable[..., Any]:
    """Import the runner lazily — these pull in parsedmarc and dnspython."""
    if stream == "rua":
        from .ingest import rua

        return rua.run
    if stream == "dns":
        from .ingest import dns_check

        return dns_check.run
    if stream == "bounce":
        from .ingest import bounce

        return bounce.run
    if stream == "dnsbl":
        from .ingest import blacklist

        return blacklist.run
    raise ValueError(f"Unknown stream: {stream!r}")


def _run_and_forget(stream: str, settings: Settings, database: Database) -> None:
    try:
        runner = _runner_for(stream)
        result = runner(settings, database)
        logger.info("On-demand %s run finished: %s", stream, result.get("status"))
    except Exception:  # noqa: BLE001
        # The runners already record their own failures in ingestion_runs; this
        # is the last-resort net so a crash cannot kill the thread silently.
        logger.exception("On-demand %s run crashed", stream)
    finally:
        with _lock:
            _in_flight.pop(stream, None)


def start(
    stream: str,
    settings: Optional[Settings] = None,
    database: Optional[Database] = None,
) -> Dict[str, Any]:
    """Kick off one ingestion stream in the background.

    Returns ``{"started": bool, "stream": str, "reason": str|None}``. A refusal
    is a normal answer, not an error — it means that work is already happening.
    """
    if stream not in STREAMS:
        return {"started": False, "stream": stream, "reason": "unknown_stream"}

    settings = settings or Settings.from_env()
    database = database or get_database(settings)

    runs = IngestionRunRepository(database, settings.project_id)
    # Clear rows left at 'running' by an interrupted process, which would
    # otherwise block this stream forever.
    runs.fail_stale_running()

    with _lock:
        if stream in _in_flight and _in_flight[stream].is_alive():
            return {"started": False, "stream": stream, "reason": "already_running"}

        if stream in runs.active_streams():
            return {"started": False, "stream": stream, "reason": "already_running"}

        thread = threading.Thread(
            target=_run_and_forget,
            args=(stream, settings, database),
            name=f"ingest-{stream}",
            daemon=True,
        )
        _in_flight[stream] = thread
        thread.start()

    return {"started": True, "stream": stream, "reason": None}


def running_streams(settings: Optional[Settings] = None, database: Optional[Database] = None) -> list:
    """Streams currently mid-run, for the UI to poll."""
    settings = settings or Settings.from_env()
    database = database or get_database(settings)
    return IngestionRunRepository(database, settings.project_id).active_streams()
