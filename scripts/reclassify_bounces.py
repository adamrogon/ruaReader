"""Re-run the current classifiers over every bounce already in the database.

Ingestion classifies each bounce once, at read-time, and stores the result.
When the rules change (a code once treated as a sender block turns out to
be commonly a delivery loop, an ESP mapping learns a new provider), the
existing rows are silently wrong — nothing in the app fixes them on its own.

This script goes back through everything and recomputes ``bounce_class``,
``bounce_reason`` and ``recipient_esp`` from the stored raw fields. It is
safe to re-run: every row is rewritten from its own inputs, and rows the
classifiers still decide the same way about are simply written back
unchanged.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, update

from deliverability.classify.bounce_codes import classify_bounce  # noqa: E402
from deliverability.classify.esp import esp_from_email_domain, esp_from_mta, esp_from_mx  # noqa: E402
from deliverability.config import Settings  # noqa: E402
from deliverability.storage import get_database  # noqa: E402
from deliverability.storage.schema import bounces  # noqa: E402


def main() -> None:
    settings = Settings.from_env()
    database = get_database(settings)

    changed = 0
    scanned = 0

    with database.connect() as conn:
        rows = list(
            conn.execute(
                select(
                    bounces.c.id,
                    bounces.c.status_code,
                    bounces.c.bounce_class,
                    bounces.c.bounce_reason,
                    bounces.c.diagnostic_code,
                    bounces.c.raw_text,
                    bounces.c.recipient_domain,
                    bounces.c.recipient_esp,
                    bounces.c.remote_mta,
                    bounces.c.reporting_mta,
                ).where(bounces.c.project_id == settings.project_id)
            )
        )

        for row in rows:
            scanned += 1
            r = row._mapping

            new_class, new_reason = classify_bounce(
                r["status_code"], r["diagnostic_code"], r["raw_text"]
            )
            # Same three-step fallback as ingestion: rejecting MTA → address
            # domain string → live MX lookup. Cached across rows in-process.
            new_esp = esp_from_mta(r["remote_mta"] or r["reporting_mta"])
            if new_esp in ("Unknown", "Other"):
                from_domain = esp_from_email_domain(r["recipient_domain"])
                if from_domain not in ("Unknown", "Other"):
                    new_esp = from_domain
            if new_esp in ("Unknown", "Other") and r["recipient_domain"]:
                mx_esp = esp_from_mx(r["recipient_domain"])
                if mx_esp not in ("Unknown", "Other"):
                    new_esp = mx_esp

            updates = {}
            if new_class != r["bounce_class"]:
                updates["bounce_class"] = new_class
            if new_reason != r["bounce_reason"]:
                updates["bounce_reason"] = new_reason
            if new_esp != r["recipient_esp"]:
                updates["recipient_esp"] = new_esp

            if updates:
                conn.execute(update(bounces).where(bounces.c.id == r["id"]).values(**updates))
                changed += 1

    print(f"scanned {scanned} bounces, updated {changed}")


if __name__ == "__main__":
    main()
