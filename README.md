# Deliverability Monitor

Monitors the deliverability health of cold-outreach sending domains and answers
one question fast: **which domain can I safely send from today, and if one has a
problem, which provider has it?**

Four ingestion modules feed one local dashboard:

| Module | What it reads | Frequency |
| --- | --- | --- |
| 1 — RUA reader | DMARC aggregate reports over IMAP, parsed with `parsedmarc` | daily |
| 2 — DNS check | SPF (with lookup counting), DMARC, DKIM, MX per domain | daily |
| 3 — Bounce reader | NDRs from each sending mailbox, RFC 3464 and non-compliant | hourly |
| 4 — Blacklist check | Sending IPs against DNSBLs via `pydnsbl` | daily |

---

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env          # then fill in the credentials
```

Try it with synthetic data before wiring up real mailboxes:

```bash
.venv/bin/python scripts/seed_demo.py --reset
.venv/bin/python -m uvicorn deliverability.web.app:app --port 8099
```

Open <http://127.0.0.1:8099>. To clear the demo data later, delete `data/`.

---

## Configuration

Domains and mailboxes are managed in the dashboard, under **Settings**. Add a
domain, add a mailbox, press **Test connection** — a wrong password or a
mistyped host tells you so immediately instead of turning into a stale-data
warning hours later.

The YAML files below are still read **once**, to seed the database on a fresh
install, so an existing checkout keeps working and YAML remains a valid way to
bootstrap. After that, the database is the source of truth.

### `.env` — secrets and paths

```ini
PROJECT_ID=linkhouse
DATABASE_URL=sqlite:///data/deliverability.db
ARCHIVE_DIR=data/archive
RECIPIENT_HASH_SALT=<long random string, keep stable>
SECRET_KEY=<generate with: python -m deliverability.cli genkey>

# Only needed for mailboxes bootstrapped from YAML — ones added in the
# dashboard store their password encrypted in the database instead.
RUA_MAIN_PASSWORD=...
```

`RECIPIENT_HASH_SALT` salts the hashing of bounce recipient addresses.
`SECRET_KEY` encrypts mailbox passwords entered in the dashboard. Both must
stay stable: changing the salt makes old hashes incomparable, and changing the
key makes stored passwords unreadable, so they have to be re-entered.

### `config/mailboxes.yml` — seeding mailboxes (optional)

Two **lists**, so adding a mailbox never means changing code. Passwords are
referenced by environment-variable name, never written here.

```yaml
rua_mailboxes:            # Module 1 — where DMARC reports arrive
  - name: rua-main
    host: imap.gmail.com
    username: dmarc@example.com
    password_env: RUA_MAIN_PASSWORD
    folder: INBOX
    processed_folder: null   # set a folder name to file processed mail away
    enabled: true

bounce_mailboxes:         # Module 3 — one entry per sending mailbox
  - name: outreach-domain1
    domain: domain1.com      # ties bounces to the sending domain
    host: imap.gmail.com
    username: outreach@domain1.com
    password_env: BOUNCE_DOMAIN1_PASSWORD
    enabled: true
```

`rua_mailboxes` is a list because consolidating every domain's `rua=` onto one
mailbox in DNS may not be finished — point it at as many as you need.
`bounce_mailboxes` is a list by necessity: an NDR returns to the mailbox that
sent the message, so bounces cannot be consolidated at all.

To add a mailbox: append an entry, add its password variable to `.env`, done.

### `config/domains.yml` — the domains to monitor

```yaml
defaults:
  dkim_selectors: [google, selector1, selector2]

domains:
  - name: domain1.com
    dkim_selectors: [google]     # omit the key to inherit the defaults
    notes: "Google Workspace"
```

DKIM selectors must be listed explicitly. A DKIM record is only reachable as
`<selector>._domainkey.<domain>`, and there is no way to discover a selector
from DNS — take them from your sending platform's DNS setup screen. An explicit
empty list means "skip the DKIM check"; omitting the key inherits `defaults`.

---

## Running ingestion

```bash
.venv/bin/python -m deliverability.cli rua       # DMARC reports
.venv/bin/python -m deliverability.cli dns       # SPF / DMARC / DKIM / MX
.venv/bin/python -m deliverability.cli bounce    # bounces / NDRs
.venv/bin/python -m deliverability.cli dnsbl     # blacklist checks
.venv/bin/python -m deliverability.cli daily     # rua + dns + dnsbl together
.venv/bin/python -m deliverability.cli status    # current state, no network
.venv/bin/python -m deliverability.cli genkey    # generate SECRET_KEY
```

`status` takes `--lang pl` if you prefer the Polish wording in a terminal.

`status` prints what the dashboard shows without starting a server — useful from
a terminal or a cron mail.

### Scheduling

Bounces are the time-sensitive stream and are worth running hourly; everything
else is daily. Example `crontab -e` for this project:

```cron
0 * * * * cd /path/to/ruaReader && .venv/bin/python -m deliverability.cli bounce >> data/cron.log 2>&1
30 6 * * * cd /path/to/ruaReader && .venv/bin/python -m deliverability.cli daily  >> data/cron.log 2>&1
```

If a stream stops producing data for more than 48 hours, the dashboard shows it
as an explicit stale state rather than silently displaying old numbers.

---

## Running the dashboard

```bash
.venv/bin/python -m uvicorn deliverability.web.app:app --port 8099

# ...or, to also stop any already-running instance first and clear any
# ingestion run orphaned by that stop (see "Reliability lessons" below):
./scripts/restart_dashboard.sh [port]
```

**Overview** — domains ordered by urgency (never alphabetically), fleet totals, a
freshness strip per ingestion stream, the per-provider breakdown, and daily
trend charts.

**Domain page** — every flag with a plain-language explanation, per-provider
compliance, sender-block detail, current DNS records, and the highest-volume
failing sources. Two charts (daily volume by outcome, and daily bounce volume
by class) plot the class-of-interest as dashed lines against total messages
sent as bars on a separate axis, so a handful of real bounces don't visually
disappear against hundreds/thousands of sent messages sharing one scale —
clicking a day on either drills into `/domain/{domain}/day/{date}`. Bounce
detail ("Podsumowanie odbić") is a small set of category tabs (sender block /
hard / soft / unparsed) over one shared detail panel, not one long flat table
— pick a category to see its exact codes, providers, and diagnostic text.
Every chart legend item and sortable table header that has more to say
carries a small "ⓘ" and shows an explanation on hover.

Each IP in "Adresy IP i blacklisty" also shows its reverse-DNS (PTR) hostname
underneath, captured once at DNSBL-check time (never live on page load) —
often enough on its own to recognize which provider/host an unfamiliar IP
belongs to.

JSON endpoints, if you want the data elsewhere: `/api/volume`, `/api/bounces`,
`/api/esp`, `/api/health`.

**`/use-cases`** — a static onboarding page: for each major chart/table above,
a real worked example of what it showed once and what changed after acting
on it (not a generic feature tour). Useful to point a new team member at
before they touch the dashboard for the first time.

**Settings** — add, edit, enable/disable, and remove domains and mailboxes.
Passwords typed here are encrypted with `SECRET_KEY` before they touch the
database. **Test connection** logs into IMAP and checks the folder exists;
**Check DNS** confirms a domain resolves and reports which of SPF / DMARC /
rua / DKIM are missing before you commit to monitoring it.

**Check everything** (top of the overview) runs all four ingestion streams on
demand, on a background thread, and reloads when they finish. It refuses to
start a stream that is already running, so it is safe to press twice or to
press while cron is mid-run.

**Dismiss / restore** — on a domain page, any flag can be dismissed. It moves
into a collapsed "dismissed" list and stops counting toward urgency, so a
domain you are already dealing with drops down the list instead of
permanently occupying the top. A dismissal is keyed by the flag's stable
`fingerprint` (e.g. `sender_block:Other`), not a row id — restoring it is one
click, and if the *same* underlying condition produces that fingerprint again
on a later ingestion run, the flag simply reappears (this falls naturally out
of fingerprints being recomputed each run, not special "re-open" logic).
There is no note field; the flag's own explanation is the record of what it
was.

**Language and theme.** The dashboard is bilingual (Polish default, English via
the globe control in the topbar) — every flag, DNS warning, and ingestion
message is translated, not just the chrome around it. The choice is
remembered in a cookie. A light theme (matching Linkhouse's own product) is
the default; the moon/sun control switches to dark and remembers that choice
in the browser's local storage. Message templates for both languages live in
[deliverability/i18n.py](deliverability/i18n.py) — add a language by adding a
key there, not by touching the ingestion modules, which only ever produce a
message key and its parameters.

---

## How the numbers are decided

**Forwarding is a category, not a filter.** Forwarded mail routinely fails SPF —
the relay sends from an IP the domain never authorised — while DKIM survives.
When SPF fails, DKIM passes, and the source's reverse DNS matches a forwarder
pattern (`srs*`, `*forward*`, `redirect.*`, `improvmx*`, `fwd.*`), the record is
stored as `forwarded`, a third outcome alongside `pass` and `failed`. Compliance
rates exclude it from both numerator and denominator, because it reflects
someone else's relay rather than your configuration.

This depends on reverse DNS being resolvable. Run `rua` without `--offline` (the
default) so `parsedmarc` resolves it; with `--offline` forwarding cannot be
detected and that traffic will count as failure.

**"Which provider has a problem" means the reporting provider.** The per-ESP
breakdown groups by the organisation that *wrote* the report (`org_name`), not
by source IP. Google's report tells you what Google thinks of your mail. Google,
Microsoft, Yahoo, Seznam, WP/O2, Onet, and Interia are labelled individually;
the rest fall into `Other`.

**Sender blocks outrank everything — but not every sender block is shown as
equally urgent.** `5.1.8` and the whole `5.7.x` family are stored as
`sender_block`, separate from `hard`. A hard bounce says one address is
wrong; a sender block says the provider is refusing your domain. Permanent
failures whose text describes a block or blocklist are also classified this
way even when the code looks ordinary. From there, severity depends on *which*
provider and *how many times*: a block at a major provider (Google,
Microsoft, Yahoo, Apple, Proton, Seznam, WP/O2, Onet, Interia, Mail.ru, GMX)
is `critical` — the highest urgency weight, always the first row — but only
once it has **recurred** (2 or more rejections there); a single rejection at
a major provider, or any number at an unrecognized/minor one, is a calmer
`warning` with advice to watch for a repeat rather than pause sending. One
rejection is real signal, weaker than a pattern.

One specific exclusion worth knowing: Microsoft's `5.7.509` ("sending domain
does not pass DMARC verification and has a DMARC policy of reject") is *not*
counted as a sender block, even though it's in the 5.7.x family. It means the
receiver is enforcing this domain's own DMARC policy against a message that
failed DMARC in transit — usually forwarding-alignment collateral at the
recipient, or a real SPF/DKIM gap in one sending path — not a verdict on
reputation, so it's excluded the same way a delivery loop or a full mailbox
is (see the code-level exclusion list in `classify/bounce_codes.py`).

**Bounce parsing is deliberately tolerant.** It tries the RFC 3464
`message/delivery-status` part, then the `Diagnostic-Code`, then the message
body. If no status code can be found, the record is stored anyway with
`parse_ok = false` and its full text kept — an unparseable bounce is still
evidence. Those are surfaced on the dashboard rather than dropped.

**SPF lookups are counted against the RFC 7208 limit of 10**, following every
`include:` and `redirect=` recursively. Over the limit means receivers return
permerror and treat SPF as failed however correct the record looks, so it is
flagged critical; 8 or 9 is flagged as a warning, because adding one more sender
will break it.

**Indicators stay separate.** There is deliberately no single composite health
score — the urgency value orders the list and nothing else.

---

## Storage

SQLite through a thin SQLAlchemy Core layer. Business logic calls repositories
in `deliverability/storage/repositories.py` and never writes SQL, so moving to
Postgres means changing `DATABASE_URL` and reviewing that one package.

Every main table carries `project_id` (currently always `linkhouse`), so a
second project does not require a migration later.

Raw report XML is archived to `ARCHIVE_DIR` before normalisation. If the
classification rules change, the full history can be re-derived from the
archive rather than re-fetched from mailboxes.

Reports are deduplicated on `report_id + org_name + date_range`; bounces on
Message-Id per mailbox. Re-running ingestion over already-processed mail is
harmless.

Bounce recipients are stored as a salted SHA-256 hash. The domain part is kept
in clear on purpose — identifying which provider is rejecting you is the whole
diagnostic value, and it is not personally identifying on its own.

---

## Layout

```
config/          domains.yml, mailboxes.yml
deliverability/
  config.py      typed config loading; passwords resolved from env at use time
  health.py      urgency ranking, 48h freshness, human-readable flags
  cli.py         ingestion entry points
  classify/      forwarding, ESP labelling, bounce codes
  ingest/        rua.py, dns_check.py, bounce.py, blacklist.py, imap_client.py
  storage/       schema.py, database.py, repositories.py
  web/           FastAPI app, templates, vendored Chart.js
scripts/         seed_demo.py, reclassify_bounces.py, reclassify_esp.py,
                 restart_dashboard.sh
```

---

## Reliability lessons

Not defensive programming in the abstract — each of these was added after
reproducing the actual failure against real mailboxes on a memory-constrained
host. Worth knowing before changing `ingest/`, so the same failure doesn't
have to be rediscovered:

- **A parsed email header can come back as an `email.header.Header` object,
  not a plain string**, for certain header shapes — `x.get(...) or ""` only
  guards `None`, not this case, and it crashes `re.search()` if left
  uncoerced. Every header read in `ingest/bounce.py` and `imap_client.py`
  goes through `str(...)` for this reason.
- **IMAP fallback retries are capped at 3 consecutive failures**
  (`imap_client._fetch_batch_with_fallback`). Uncapped, a dead connection
  (as opposed to one bad message) turned into up to 50 sequential ~60s
  timeouts — a single bad mailbox hanging ingestion for most of an hour.
- **Bounce filtering happens at parse time, not after the whole mailbox is
  loaded** (`fetch_messages(..., predicate=looks_like_bounce)`). A bounce
  mailbox can echo back an entire original message — images, HTML,
  attachments — unlike a compact DMARC report, so holding the whole mailbox
  in memory before filtering was the single biggest contributor to OOM.
- **A failed mailbox is retried once on a brand-new IMAP connection**, not
  the same socket — fixes `SSL: BAD_WRITE_RETRY`-type errors from a
  connection held open across more work than before.
- **"Check everything" runs its 4 streams one at a time**, waiting for each
  to finish before starting the next, instead of firing all 4 at once. Firing
  them concurrently made peak memory the *sum* of all four streams running
  together — the primary cause of OOM on a full domain list.
- **A stuck `ingestion_runs` row (process killed mid-run) self-heals after
  30 minutes** so it doesn't block that stream forever
  (`IngestionRunRepository.fail_stale_running`). `scripts/restart_dashboard.sh`
  also calls this immediately on every restart, since killing the server
  while a background ingestion thread is running is exactly how a row gets
  orphaned.

---

## Notes and limitations

- **Python 3.9** works and is what this was verified against, but it is past end
  of life and some dependencies now warn about it. Python 3.11+ is worth moving
  to when convenient.
- **Spamhaus and some other DNSBLs refuse queries from public resolvers**
  (8.8.8.8 and similar) and return a non-answer. Rather than reporting that as
  "not listed", providers that failed to answer are recorded and shown as a
  caveat on the result. For complete DNSBL coverage, run against a local
  recursive resolver.
- **DNSBLs differ a lot in how much they mean.** Any listing is reported as
  critical, as intended, but the explanation distinguishes lists receivers
  actually act on (Spamhaus, Barracuda, SpamCop) from ones like UCEProtect
  level 2/3 that list whole netblocks or ASNs. A hit on the latter usually
  reflects your hosting provider's neighbours rather than your sending, and
  chasing a delisting there is normally wasted effort.
- **Chart.js is vendored** in `deliverability/web/static/`, so the dashboard
  works offline. Poppins is loaded from Google Fonts and falls back to system
  fonts if unavailable.
- The dashboard binds to localhost and has no authentication — it is a
  single-user local tool and should not be exposed to a network.

### Deliberately not built yet

TLS-RPT, ARF/FBL complaint feeds, LLM classification of replies, Sent-folder
analysis, Instantly API integration, a composite health score, and multi-tenancy
with user accounts.
