"""Re-run the sent-source classifier's DB-derivable signals over stored rows.

Ingestion classifies each Sent-folder message once, at read-time, using the
full message (headers + HTML body — see classify/sent_source.py) and stores
only the verdict, plus the subject and Message-Id. When a signal regex
turns out to be wrong, the stored rows are silently wrong too — nothing in
the app fixes them on its own.

This only re-checks the two signals derivable from what's actually stored
(Message-Id, subject) and only ever moves a row OUT of 'unrecognized' — it
never touches a row already classified 'extender'/'instantly', since doing
that correctly would require the raw body/Received headers this table
never kept. Safe to re-run: a row that still doesn't match either signal is
left untouched.

Background: the Message-Id check used a bare `$` anchor against
"...@swift.generated", but a Message-Id header value always arrives wrapped
in angle brackets ("<...@swift.generated>"), so the check never matched and
every such message silently landed in 'unrecognized' instead of 'extender'.
Fixed in classify/sent_source.py on 2026-09-30; this script catches up the
rows ingested before the fix.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, update

from deliverability.classify.sent_source import (  # noqa: E402
    SOURCE_EXTENDER,
    SOURCE_INSTANTLY,
    SOURCE_UNRECOGNIZED,
    _EXTENDER_MESSAGE_ID_RE,
    _INSTANTLY_SUBJECT_CODE_RE,
)
from deliverability.config import Settings  # noqa: E402
from deliverability.storage import get_database  # noqa: E402
from deliverability.storage.schema import sent_messages  # noqa: E402


def main() -> None:
    settings = Settings.from_env()
    database = get_database(settings)

    scanned = 0
    changed = 0

    with database.connect() as conn:
        rows = list(
            conn.execute(
                select(sent_messages.c.id, sent_messages.c.message_id, sent_messages.c.subject)
                .where(
                    sent_messages.c.project_id == settings.project_id,
                    sent_messages.c.source == SOURCE_UNRECOGNIZED,
                )
            )
        )

        for row in rows:
            scanned += 1
            r = row._mapping

            new_source, new_reason = None, None
            if _EXTENDER_MESSAGE_ID_RE.search(r["message_id"] or ""):
                new_source, new_reason = SOURCE_EXTENDER, "message_id"
            elif _INSTANTLY_SUBJECT_CODE_RE.search(r["subject"] or ""):
                new_source, new_reason = SOURCE_INSTANTLY, "subject_code"

            if new_source:
                conn.execute(
                    update(sent_messages)
                    .where(sent_messages.c.id == r["id"])
                    .values(source=new_source, match_reason=new_reason)
                )
                changed += 1

    print(f"scanned {scanned} unrecognized rows, reclassified {changed}")


if __name__ == "__main__":
    main()
