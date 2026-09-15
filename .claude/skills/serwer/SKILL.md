---
name: serwer
description: Restarts the ruaReader dashboard (deliverability monitor) and reports back the local URL, in one step, with no back-and-forth. Use this whenever the user asks to start, restart, "odpal", or reload the server in this repo, or just asks for the dashboard link — e.g. "odpal serwer i daj linka", "restart the dashboard", "start the app", "daj linka", "uruchom serwer", "zrestartuj dashboard". Trigger this even if the user's message is just two or three words and doesn't mention the skill by name — that terse phrasing is exactly the recurring request this skill exists to shortcut. Don't manually run uvicorn or explain the restart steps instead of using this — the whole point is skipping that.
---

# Restart the ruaReader dashboard

The user restarts this dashboard constantly over the course of a session — after every code change, to pick up a fix, or just to get a fresh link. `scripts/restart_dashboard.sh` already does the whole sequence correctly (stop any running instance, self-heal any `ingestion_runs` row left at `status='running'` by that stop, start a fresh instance in the background, print the result) — see `CLAUDE.md`'s "Reliability patterns" section for why the self-heal step matters (killing the server mid-ingestion is exactly how a row gets orphaned and blocks that stream from running again).

## What to do

1. Run the script from the repo root:
   ```bash
   ./scripts/restart_dashboard.sh
   ```
   Pass a port as the first argument only if the user asked for a specific one (defaults to 8099).
2. Read the script's own output — it already reports the cleaned-run count and the dashboard URL, so there's no need to re-derive either by hand.
3. Reply to the user in Polish, short, in this shape (match whichever the cleanup count actually was):
   - `Serwer działa, zero osieroconych wpisów: <url>` when the cleaned count was 0.
   - `Serwer działa, sprzątnięte osierocone wpisy: <n>: <url>` when it was greater than 0.

Do not explain the steps you're taking or narrate the script's internals unless something in its output looks wrong (a Python traceback, uvicorn failing to bind, etc.) — in that case, show the relevant error output and stop there instead of guessing at a fix.
