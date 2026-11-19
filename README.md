# Workspace migration utility

Command-line tool to copy mail and calendars between two Google Workspace
accounts, operated by an AI agent with human approval of irreversible steps,
and validated by a test suite with HTML reports.

**Status:** design draft, not approved. Nothing is built yet.

See [docs/design.md](docs/design.md) for intent, scope, privacy rules,
commands, test plan and open questions.

## Planned commands

- `check` — validate the mapping file and access; no data moves
- `copy --batch NAME [--mail] [--calendar]` — copy; safe to re-run
- `status [--batch NAME]` — per-user counts
- `verify --batch NAME` — run validation tests, write the HTML report

## What must never be committed

Service-account keys, the mapping and inventory CSVs, the SQLite ledger and
generated reports. `.gitignore` blocks these by pattern; keys live outside
the project folder anyway.
