# Moving Day (`movingday`): Workspace migration utility

Command-line tool to copy mail, calendars and contacts between two Google Workspace
accounts, operated by an AI agent with human approval of irreversible steps,
and validated by a test suite with HTML reports.

**Status:** built; rehearsal not yet done.

See [docs/design.md](docs/design.md) for intent, scope, privacy rules and the
test plan. To set the tool up and run it, read
[docs/operator-setup.md](docs/operator-setup.md). Before the real copy,
work through [docs/rehearsal-checklist.md](docs/rehearsal-checklist.md).

## Usage

Every command needs `--config PATH` and prints one line of JSON. `--help` is
disabled. Exit codes: 0 success, 1 finished with failures, 2 refused to run.

- `movingday check --config PATH` : validates the mapping and access for all
  users and counts contacts; no data moves
- `movingday copy --batch NAME [--mail] [--calendar] [--contacts] [--allow-existing KEY ...] --config PATH` :
  copies (mail, calendars and contacts if no part flag is given); safe to re-run
- `movingday status [--batch NAME] --config PATH` : per-user counts
- `movingday verify --batch NAME [--expect-changes] --config PATH` : runs the
  validation checks and writes the HTML report

## What must never be committed

Service-account keys, the mapping and inventory CSVs, the SQLite ledger and
generated reports. `.gitignore` blocks these by pattern; keys live outside
the project folder anyway.
