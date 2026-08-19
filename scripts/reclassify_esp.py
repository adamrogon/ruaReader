"""Re-run the ESP classifier over every DMARC record already in the database.

Same idea as reclassify_bounces.py, for the other half of the classification
surface: `dmarc_records.receiving_esp` is derived from the reporting org_name
at ingestion time and stored, not recomputed on read. When
classify/esp.py learns a new provider pattern (e.g. Mimecast, GoDaddy),
existing rows stay labelled "Other" until this is run.

Safe to re-run: every row is rewritten from its own stored org_name, and
rows the classifier still decides the same way about are written back
unchanged.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, update

from deliverability.classify.esp import esp_from_org_name  # noqa: E402
from deliverability.config import Settings  # noqa: E402
from deliverability.storage import get_database  # noqa: E402
from deliverability.storage.schema import dmarc_records  # noqa: E402


def main() -> None:
    settings = Settings.from_env()
    database = get_database(settings)

    changed = 0
    scanned = 0

    with database.connect() as conn:
        rows = list(
            conn.execute(
                select(dmarc_records.c.id, dmarc_records.c.org_name, dmarc_records.c.receiving_esp).where(
                    dmarc_records.c.project_id == settings.project_id
                )
            )
        )

        for row in rows:
            scanned += 1
            r = row._mapping
            new_esp = esp_from_org_name(r["org_name"])
            if new_esp != r["receiving_esp"]:
                conn.execute(
                    update(dmarc_records).where(dmarc_records.c.id == r["id"]).values(receiving_esp=new_esp)
                )
                changed += 1

    print(f"scanned {scanned} records, updated {changed}")


if __name__ == "__main__":
    main()
