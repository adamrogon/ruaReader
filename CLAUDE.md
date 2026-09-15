# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A local, single-user FastAPI dashboard that answers "which sending domain is safe to send from today, and if one isn't, which provider has a problem with it." Four independent ingestion modules (DMARC aggregate reports, DNS record checks, bounce/NDR parsing, DNSBL blacklist checks) feed one SQLite database; the dashboard reads from that database only — it never talks to IMAP/DNS live on page load.

Full narrative documentation (config file formats, scheduling, the reasoning behind each classification rule) lives in [README.md](README.md) — read it before making changes to `classify/` or `health.py`, since several rules encode non-obvious tradeoffs explained there.

## Commands

```bash
# Setup
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in credentials; generate SECRET_KEY with `deliverability.cli genkey`

# Seed synthetic data instead of wiring up real mailboxes
.venv/bin/python scripts/seed_demo.py --reset

# Run the dashboard (binds to localhost only, no auth)
.venv/bin/python -m uvicorn deliverability.web.app:app --port 8099
# ...or, to also clear any ingestion run orphaned by a previous kill first:
./scripts/restart_dashboard.sh [port]

# Run one ingestion stream by hand (same code path cron/the "Check everything" button use)
.venv/bin/python -m deliverability.cli rua|dns|bounce|dnsbl|daily
.venv/bin/python -m deliverability.cli status --lang pl   # prints dashboard state, no network calls
```

There is no test suite and no linter configured in this repo. Verify changes by running the relevant `deliverability.cli` command or by starting the dashboard and checking the affected page/flag in the browser.

## Architecture

### The four modules are independent and only meet at storage

`deliverability/ingest/{rua,dns_check,bounce,blacklist}.py` each own their own external I/O (IMAP or DNS), their own classification pass, and their own row in `ingestion_runs` for freshness tracking. None of them import from each other. `deliverability/jobs.py` runs any of them on a daemon thread when triggered from the dashboard ("Check everything" button) — guarded against double-starts via an in-process lock plus a DB check against a run already in flight (so an on-demand click and a cron job can't race).

Each module's `run()` computes an overall status of `"ok"`, `"partial"`, or `"error"` from its own per-mailbox/per-domain failures — never treat "not literally everything failed" as `"ok"`; a partial failure must stay visible (see the `partial` handling in `health.ingestion_health()`).

### Storage is a repository layer, not an ORM

`deliverability/storage/schema.py` defines SQLAlchemy Core tables; `deliverability/storage/repositories.py` is the *only* place that builds queries. Business logic (ingestion, `health.py`, the web routes) calls repository methods with plain Python values and gets plain dicts back — no SQLAlchemy objects cross that boundary. Every table carries `project_id` (always `"linkhouse"` today) so multi-tenancy later doesn't need a migration.

When a classification rule changes, existing rows are **not** retroactively correct — write/extend a `scripts/reclassify_*.py` script (see `reclassify_bounces.py`, `reclassify_esp.py`) that re-derives the classified column from the raw stored fields and rewrites only the rows that actually changed. Every such script is safe to re-run.

### `health.py` turns four data streams into one ranked list, not a score

`build_domain_status()` assembles a `DomainStatus` per domain from DNS/bounce/blacklist/DMARC rows. Each problem becomes a `Flag` (severity, translation key + params, an explicit `urgency_weight`, and an explicit `fingerprint` string). `urgency` is the sum of active flags' weights and is a **sort key only** — there is deliberately no single composite health score (see README's "Indicators stay separate").

Flags can be dismissed per-domain from the dashboard; a dismissal is keyed by the flag's `fingerprint` (e.g. `"sender_block:Other"`, `"blacklist"`, `"dns:flag.dns.no_spf.title"`), stored in the `dismissed_flags` table, and filtered out before urgency is recomputed — so a dismissed blacklist hit stops making the domain look critical. Fingerprints are set explicitly at each `Flag(...)` call site in `build_domain_status()`, not derived generically, because `sender_block` is the one flag type that legitimately repeats per-ESP within a domain while every other flag type is single-instance.

### i18n: messages are keys + params, resolved once, at render time

Ingestion and classification code never produces finished English/Polish sentences — it returns a translation key plus a params dict (see `Flag.title_key`/`title_params` in `health.py`, or `message_key` on a DNS warning). `deliverability/i18n.py` holds a flat `MESSAGES` dict (`{"pl": ..., "en": ...}` per key) and a `translate(key, lang, **params)` function. `Nested(key, **params)` composes a sub-translation inside a larger message's params — it's a plain dict with a sentinel, not a class instance, specifically so it survives a round-trip through a JSON DB column (a warning's `message_params` gets stored in `dns_checks.warnings` as JSON before it's ever translated). Add a language by adding a key in `i18n.py`; never add translated strings inside `ingest/` or `classify/`.

**Recurring pitfall**: Polish curly quotes (`„…”`) embedded in a string literal break `ast.parse()` intermittently depending on quoting — avoid embedding quote characters in Polish message literals in `i18n.py` at all; write around them instead of escaping.

### Classification lives in `deliverability/classify/`, is pure, and is designed to be re-run

- `forwarding.py` — DMARC record → `pass`/`failed`/`forwarded`. Forwarding is a **third outcome**, not a filter: SPF-fail + DKIM-pass + a forwarder-shaped reverse-DNS hostname is stored as `forwarded` and excluded from compliance math entirely, because it reflects someone else's relay, not this domain's config.
- `esp.py` — org_name/MTA-hostname/email-domain → a canonical ESP label, via ordered regex pattern tables (`_ORG_PATTERNS`, `_SOURCE_PATTERNS`) plus `esp_from_mx()` as a live-DNS fallback (in-process cached). `MAJOR_ESPS` in `health.py` (Google, Microsoft, Yahoo, Apple, Proton, Seznam, WP/O2, Onet, Interia, Mail.ru, GMX) gates whether a sender-block flag is `critical` or a quieter `warning` — a block from an unrecognized/minor provider is shown but doesn't imply "stop sending." Add a new provider pattern when the same unrecognized org_name shows up repeatedly in real data (check via `DmarcRepository.other_esp_org_breakdown()`), not preemptively. A second axis stacks on top of this in `build_domain_status()`'s `_weight_for()`: a sender block at a *major* ESP is only `critical` once it has recurred (2+ rejections there); a first-and-only rejection at a major ESP gets its own `warning`-severity copy (`flag.sender_block.title_single_major`/`message_single_major`) telling the reader to watch for a repeat rather than pause sending — one rejection is real signal, but weaker than a pattern.
- `bounce_codes.py` — SMTP enhanced status code + diagnostic text → `hard`/`soft`/`sender_block`/`unknown`. `5.1.8` and the `5.7.x` family are `sender_block` by default (a reputation/policy refusal, ranked above everything else) *unless* the diagnostic text matches `_NOT_SENDER_BLOCK_PHRASES` (delivery loop, unknown user, full mailbox, a whitelist-of-known-senders scheme) — cold outreach hits these regularly and they are not reputation blocks. One entry in that list is easy to miss and worth calling out: Microsoft's `5.7.509` ("sending domain does not pass DMARC verification and has a DMARC policy of reject") reads like a reputation block but isn't one — it's the receiver enforcing *this domain's own* DMARC policy against a message that failed DMARC somewhere in transit (typically forwarding-alignment collateral at the recipient), so text matching `does not pass DMARC` / `DMARC policy of reject` / `failed DMARC` is excluded the same way. When adding a new exclusion phrase or a new `_BLOCK_PHRASES` term, make sure the two checks stay mutually consistent (a message excluded by the first must not be caught by the second) and prefer `\bword\b` boundaries over bare substrings — a loose `spam` pattern once matched `spammers` and silently undid an exclusion. Changing this classifier retroactively misclassifies already-stored rows — rerun `scripts/reclassify_bounces.py` after any change here, per the repository-layer note above.

### Reliability patterns in `ingest/` exist because of specific, reproduced incidents — keep them when touching this code

These aren't defensive-programming-in-general; each one was added after actually reproducing the failure on real mailboxes on a memory-constrained host, so don't strip them out as "unnecessary complexity" without re-reading why they're there:

- **Every header read from a parsed email is coerced through `str(...)`.** `email.message.Message.get()` can return an `email.header.Header` object instead of a plain string for certain header shapes — `x.get(...) or ""` only guards `None`, not this truthy-but-wrong-type case, and an uncaught one crashes `re.search()` deep in `looks_like_bounce()`/`parse_bounce()`/`iter_attachments()`. Apply `str(...)` at every new header read, not just the one that happened to crash first.
- **`imap_client._fetch_batch_with_fallback()` caps consecutive per-UID retries** at `_MAX_CONSECUTIVE_FALLBACK_FAILURES = 3`. Without a cap, a genuinely dead connection (vs. one bad message) turned into up to 50 sequential ~60s timeouts — a single bad mailbox hanging ingestion for the better part of an hour.
- **`fetch_messages()`'s `predicate` argument filters at parse time, not after.** `bounce.py` passes `looks_like_bounce` here so a non-bounce message is dropped before it's added to the result list. Materializing a whole bounce mailbox (which can echo back the entire original message — images, HTML, attachments — unlike a compact DMARC XML report) before filtering was the single largest memory contributor to ingestion OOM-ing.
- **`rua.py`/`bounce.py`'s `run()` retries a failed mailbox once, on a fresh `IMAPClient` connection**, not a retry on the same socket — `open_mailbox()` opens its own connection per call, so wrapping the whole `ingest_mailbox()` call (not just an inner batch-fetch) gets a genuinely new socket. Fixes `SSL: BAD_WRITE_RETRY`-type errors from a connection held open across more work than before (e.g. scanning both an inbox and its Junk folder).
- **The "Check everything" button fires its 4 streams sequentially**, not concurrently — see the JS in `overview.html`/`folders.html` (`waitUntilFinished()` polling `/api/run/status` between each `POST`). Firing all 4 at once made peak memory the *sum* of all four streams simultaneously; this was the primary OOM cause on a full domain list. `jobs.py`'s `start()` itself only launches one stream — the sequencing is a frontend-level decision, not a backend guarantee, so a caller hitting the POST endpoints directly can still fire them concurrently.
- **`IngestionRunRepository.fail_stale_running(older_than_minutes=30)`** self-heals a run stuck at `status='running'` (e.g. the process was `kill`ed mid-run) — without it, `active_streams()`'s guard blocks that stream from ever running again. `scripts/restart_dashboard.sh` calls this with `older_than_minutes=0` on every restart as a matter of habit, since killing the server while a background ingestion thread is active is exactly how a row gets orphaned in the first place.

### Domain/mailbox config lives in the DB, YAML seeds it once

`config/domains.yml` and `config/mailboxes.yml` are read once, only to seed the `domains`/`mailboxes` tables on a fresh (empty) database; after that the dashboard's Settings page is the source of truth and editing the YAML has no effect. Deleting a domain from Settings cascades: it also deletes that domain's rows in `dmarc_reports`, `dmarc_records`, `bounces`, `dns_checks`, and `blacklist_checks` (`DomainConfigRepository.delete()`) — deletion means deletion, not "leave orphaned history for later."

A domain's identity has to agree across three places for its data to end up in one row on the dashboard: the `domains` table entry, the `mailboxes` row of kind `rua` (no domain field — rua reports self-identify their `policy_domain` from the XML), and the `mailboxes` row of kind `bounce` (whose `domain` field explicitly ties NDRs back to a sending domain, since a bounce has no other reliable way to know which domain sent the original mail). The Settings UI enforces this by making the bounce mailbox's domain field a `<select>` over monitored domains rather than free text.

### Web layer

FastAPI app in `deliverability/web/app.py`, Jinja2 templates in `deliverability/web/templates/`. Routes read already-computed data via repositories/`health.py` and never perform ingestion inline. Form mutations (dismiss/restore a flag, add/delete a domain or mailbox) are plain POST-and-redirect; only the interactive on-page checks (test mailbox connection, test domain DNS, trigger a stream) go through a JSON `fetch()` so the button can show an inline result without a full page reload. `esp_logo()` (a Jinja global registered in `app.py`) maps a canonical ESP name to a locally-bundled favicon PNG in `static/logos/` — these were fetched once from an external favicon service and committed, never fetched live at request time.

`/use-cases` is a static, self-contained onboarding page (`USE_CASES` list + `_USE_CASE_MOCKUPS` dict in `app.py`, one template) — no DB queries, illustrative fake data explicitly captioned as such (`use_cases.mockup_disclaimer`). It exists to teach new team members what each chart/table means and what to do about it; update it when the underlying UI it documents changes shape, not on every unrelated change.

**Hover-tooltip convention**: a real `<th>`/badge gets `class="th-hint" data-hint="..."` (CSS `::after` draws a small "ⓘ"; JS in `base.html` delegates one `mousemove` listener on `<body>` for the shared `#legend-tip` div — chosen over a plain `title=""` attribute because that turned out to render inconsistently, help-cursor with no visible text, across real browsers). A Chart.js legend has no real DOM to hang `data-hint` off, so its labels get a `legendHintHandlers(hints)` object (also in `base.html`) merged into `plugins.legend`: it appends a `" ⓘ"` mark to the label text via a custom `generateLabels`, and strips that same mark back off before doing the `hints[label]` lookup on hover — if you add a new chart legend and want it hoverable, reuse `legendHintHandlers()`, don't hand-roll another mechanism.

**Bounce-category UI (`domain.html`'s "Podsumowanie odbić")** is tabs over one shared detail panel below (`_group_bounce_summary()` in `app.py` folds the flat per-code rows by `bounce_class`), not a CSS grid of `<details>` elements — that was tried first and broke two ways: opening one `<details>` stretched the whole grid row's height so closed siblings visibly stretched into empty boxes too, and the opened table stayed constrained to one grid column's width (~200–260px) with nowhere to show the diagnostic text. If you touch this again and are tempted to reach for a grid-of-cards pattern for similar "pick one, see detail below" UI, don't — a fixed-height row of buttons plus one shared panel avoids both failure modes.
