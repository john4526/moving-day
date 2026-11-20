# Operator setup

This document is for Jonathon (the administrator) and for the AI agent that
runs the tool. It covers setup, daily use and teardown. Every step is
numbered. Words in `code style` are typed exactly as shown.

## Who does what

- **Jonathon does all the setup and security steps by hand.** This includes
  the two new Mac users, the sudoers line, file permissions, service accounts and
  delegation. These change the Mac's security settings and the Google
  accounts' access. **An agent must not make any of these changes**, even if
  asked to, and even if a step seems to be failing.
- **The agent only runs the four `movingday` commands** (see "Running the
  tool") and reports what they print.

There are three Mac users:

| Mac user | Who uses it | What for |
|---|---|---|
| Jonathon's own account (administrator) | Jonathon | Setup, and reading reports. Nothing else. |
| `wsagent` (standard, not administrator) | The AI agent | Running the four `movingday` commands, and nothing else. |
| `movingday` (standard, not administrator) | The tool itself | Owns the keys, mapping, ledger and reports. Nobody signs in as it. |

**The agent must not run under Jonathon's account.** Jonathon's account is an
administrator and can read everything, so an agent running there would not
be kept from the keys or the data.

## Before anything else: the scan

Before any migration, run one read-only scan across every account. It plans
the rehearsal, the mock and the cutover. Complete steps
1 to 6 first (including the sudoers line in step 3 and the file owners and
modes in step 6, or the tool refuses with `untrusted_file`). The config file
needs every field, so use placeholder values for the destination key file,
`dest_admin` and `dest_domain`. With `--source-only` the destination key file
is never opened, and the destination does not need to exist yet.

- **Credentials:** the source service account only (read-only scopes). The
  destination key file is not opened.
- **Mapping file:** the destination addresses may be placeholders (for
  example `u001@placeholder.example`), because the scan never uses them.
- **Command** (the agent runs it like every other command):

  ```
  cd /
  sudo -u movingday /Users/movingday/venv/bin/movingday check --source-only --config /Users/movingday/data/config.toml
  ```

It prints one JSON line: per user the message count, the number of own,
delegated and team calendars, the number of contacts and other contacts, and
Drive counts (below), plus a `summary`. It writes nothing and copies
nothing. Run it again later without `--source-only` to also see what is
already in each destination mailbox.

| Number | What it decides |
|---|---|
| Largest mailboxes (`largest_mailbox`, each `src_messages`) | Who must be in the mock, and how long the freeze must be. |
| `total_messages` and the speed seen in the rehearsal | Which mail copy method to use. |
| `contacts`, `other_contacts`, `total_contacts` | How big the contacts copy is. Other contacts (addresses Gmail saved for autocomplete) are counted for information and not copied. |
| `drive_files`, `drive_folders`, `drive_bytes`, `total_drive_bytes`, `largest_drive_bytes` | How long the Drive pre-copy takes, and whether anyone is near Google's 750 GB per user per day upload limit. `drive_files` counts every owned file, including the types below. `drive_bytes` leaves out Google Docs, Sheets and Slides (Google gives them no size), so the real upload is larger: treat the 750 GB planning figure as a floor. Before the mock, compare each person's `drive_bytes` with their destination storage allowance. |
| `drive_unsupported`, `drive_forms` | Files Google cannot export (Forms counted separately). They are not copied; tell their owners. |
| `drive_shared_from_outside` | Files a person can open that someone outside the mapping file owns. Not copied; for information. |
| `drive_in_others_folders` | Files a person owns inside someone else's folder. Copy the folder owners' batch first, or re-run once at the end. |
| `delegated_calendars`, `team_calendars` (and the `accounts_with_` counts) | Whom to test and brief. People with team calendars need a batch order, because the first one copied takes the calendar. |
| Near-empty accounts | Put them in an early batch. |

## 1. Create the service accounts (administrators of each account)

A service account is a robot login that the tool uses instead of a person.
"Domain-wide delegation" lets that robot act as any user in the account. You
need one service account for the source and one for the destination.

Do these steps once for each account, signed in as a super administrator of
that account. For the source this is the parent organisation's administrator.

1. In the Google Cloud console, create a project (or pick an existing one).
2. In that project, enable these APIs: Gmail API, Google Calendar API,
   People API and Google Drive API. For the destination only, also enable Admin SDK API.
3. Create a service account. Create a JSON key for it and download the file.
   Treat the key like a password.
4. Copy the service account's numeric Client ID.
5. In the Google Admin console, go to Security, then Access and data
   control, then API controls, then Domain-wide delegation. Add a new entry
   with that Client ID and the scopes below, pasted as one comma-separated
   line. Use exactly these scopes and no others.

**Source account scopes** (read only):

```
https://www.googleapis.com/auth/gmail.readonly,https://www.googleapis.com/auth/calendar.readonly,https://www.googleapis.com/auth/calendar.acls.readonly,https://www.googleapis.com/auth/contacts.readonly,https://www.googleapis.com/auth/contacts.other.readonly,https://www.googleapis.com/auth/drive.readonly
```

**Destination account scopes:**

```
https://www.googleapis.com/auth/gmail.insert,https://www.googleapis.com/auth/gmail.labels,https://www.googleapis.com/auth/gmail.metadata,https://www.googleapis.com/auth/calendar,https://www.googleapis.com/auth/admin.directory.user.readonly,https://www.googleapis.com/auth/admin.directory.group.readonly,https://www.googleapis.com/auth/contacts,https://www.googleapis.com/auth/drive.file
```

Notes:

- The source grant cannot be limited to our users. Technically it can read
  every mailbox, and every Drive file, in the parent organisation.
- For Drive, the source grant adds only `drive.readonly`. If the source
  entry already exists, add that one scope to it. Revoke the source grant as
  soon as the migration is signed off (see Teardown).
- All source APIs share one scope list. Until the parent organisation adds
  `drive.readonly`, every copy fails, mail, calendars and contacts included,
  not only Drive.
- The destination uses `drive.file`: the tool sees only the files it created.
  If the rehearsal shows that is not enough, the fallback is the full
  `https://www.googleapis.com/auth/drive` scope on the destination, recorded
  as a decision, with `movingday/runtime.py` changed to match.
- If Google refuses `calendar.acls.readonly` on the source, stop. The
  fallback is the full `https://www.googleapis.com/auth/calendar` scope on
  the source. This must be a recorded decision, and the code's scope list in
  `movingday/runtime.py` must be changed to match. See the rehearsal
  checklist.
- The destination also needs an administrator address in the config
  (`dest_admin`). The tool uses it for directory lookups.

## 2. Create the two Mac users (Jonathon, by hand)

The tool runs as its own Mac user, `movingday`. The agent runs as a second
Mac user, `wsagent`. `wsagent` can run the tool as `movingday`, but cannot
open `movingday`'s files. That is how the agent is kept from seeing keys,
mapping files or reports.

**These are changes to the Mac's security settings. Jonathon makes them. An
agent must not.**

1. Create a standard (not administrator) user named `movingday`. Use System
   Settings, then Users & Groups. Give it a long random password and do not
   save it anywhere the agent can read. Its home folder is
   `/Users/movingday`.
   Create a second standard (not administrator) user named `wsagent` the
   same way. The agent runs under this user. Never tell the agent either
   password, and never make `wsagent` an administrator.
2. In a Terminal, make its home folder private:

   ```
   sudo chmod 700 /Users/movingday
   ```

3. Create the tool's folders as that user and set mode 700 on each:

   ```
   sudo -u movingday mkdir -p /Users/movingday/keys /Users/movingday/data /Users/movingday/reports
   sudo -u movingday chmod 700 /Users/movingday/keys /Users/movingday/data /Users/movingday/reports
   ```

4. Install the tool in a virtual environment (a private copy of Python and
   the tool's libraries) at `/Users/movingday/venv`. Copy the project folder
   to a place `movingday` can read (for example a temporary folder). The
   temporary copy must not contain keys, CSV files, the ledger or reports,
   and must be deleted once the install is finished. Then:

   ```
   sudo -u movingday python3 -m venv /Users/movingday/venv
   sudo -u movingday /Users/movingday/venv/bin/pip install /path/to/project
   ```

   The tool needs Python 3.11 or newer.
5. Put the files in place, each owned by `movingday`, using `sudo`:
   - the two service-account key files in `/Users/movingday/keys/`
   - the mapping file as `/Users/movingday/data/mapping.csv`
   - the inventory file as `/Users/movingday/data/inventory.csv`
   - the config file as `/Users/movingday/data/config.toml`
   - the ledger (progress database) is created by the tool at
     `/Users/movingday/data/ledger.sqlite`
   - reports are written to `/Users/movingday/reports/`
6. Make sure every file is mode 600 and every folder mode 700, and nothing is
   readable by other users:

   ```
   sudo chown -R movingday /Users/movingday
   sudo chmod -R go-rwx /Users/movingday
   ls -ld /Users/movingday /Users/movingday/*
   sudo ls -l /Users/movingday/data
   ```

   The third command should show `drwx------` for every folder. The last
   one shows the owner of each data file: every file must be owned by
   `movingday`. A file owned by another user (for example one you copied in
   as yourself) is refused with `untrusted_file`; the `chown` above fixes it.
   The config file and the mapping file must be owned by `movingday` and
   must not be writable by anyone else. The tool checks this every time it
   starts and refuses with the reason `untrusted_file` (exit 2) otherwise.
   This stops anyone pointing `--config` at a file of their own.
7. Keep the Mac awake for the whole copy (System Settings, then Lock Screen
   or Battery, or `caffeinate`).

## 3. Let the agent's user run only the tool (Jonathon, by hand)

This sudoers line lets the `wsagent` Mac user run exactly one program, as
`movingday`, and nothing else. The agent runs under `wsagent`.

For the file protection to hold, `wsagent` must **not** be an administrator
and must **not** have passwordless sudo for anything else. It must never be
given Jonathon's password, the `wsagent` password or the `movingday`
password. If it can become root or the `movingday` user in any other way, it
can read the keys and the data.

Edit it with `visudo`, which checks the syntax before saving. A typo in a
sudoers file can lock you out of `sudo`.

```
sudo visudo -f /etc/sudoers.d/movingday
```

Put this single line in the file:

```
wsagent ALL=(movingday) NOPASSWD: /Users/movingday/venv/bin/movingday
```

Save and exit. Then test it, signed in as `wsagent`:

```
cd /
sudo -u movingday /Users/movingday/venv/bin/movingday check --config /Users/movingday/data/config.toml
```

An agent must not edit `/etc/sudoers.d/` or any sudoers file.

## 4. The config file

TOML file at `/Users/movingday/data/config.toml`. Every field is shown.
Replace the placeholders.

```toml
source_key_file = "/Users/movingday/keys/source-key.json"
dest_key_file   = "/Users/movingday/keys/dest-key.json"
mapping_file    = "/Users/movingday/data/mapping.csv"
inventory_file  = "/Users/movingday/data/inventory.csv"
ledger_path     = "/Users/movingday/data/ledger.sqlite"
report_dir      = "/Users/movingday/reports"   # must contain no spaces
dest_admin      = "admin@mig-b.example.org"    # admin address for directory lookups
dest_domain     = "mig-b.example.org"          # the temporary destination domain
parallel_users  = 4                            # optional, default 4
max_existing    = 5                            # optional, default 5
```

- `report_dir` must contain no spaces. The tool refuses to start otherwise.
- `max_existing` is how many messages a destination mailbox may already hold
  (Google's welcome mail). More than this and `copy` refuses to run, unless
  that user's key is listed after `--allow-existing` (see "Earlier
  transfers" in section 7).
- `sudo` removes environment variables, so always pass `--config` on the
  command line rather than relying on `MOVINGDAY_CONFIG`.

## 5. The mapping file

CSV at the `mapping_file` path. The header must contain the four column names
below. Extra columns are ignored. The key is the letter `u` followed by
exactly three digits (`u001`, `u002`, ...). It is the user's permanent name
inside the tool. Keys, source addresses and destination addresses must each be unique.
Batch names may use letters, digits, dot, dash and underscore, up to 40
characters, for example `mock` or `all`. Anything else stops the tool with
the reason `bad_batch`.

```
key,source,destination,batch
u001,alex@example.org,alex@mig-b.example.org,mock
u002,sam@example.org,sam@mig-b.example.org,mock
```

If a row is wrong, the tool stops and prints a reason (for example
`duplicate_key`, `bad_address`, `empty_batch`, `bad_batch`) and the row
number.

### Moving from the mock to the cutover

The ledger remembers what has already been copied for each user. Decide per
volunteer what happens to their mock copy:

- **Destination account deleted and recreated:** the old ledger rows would
  make the tool skip mail that is no longer there. Use a fresh
  `ledger_path` (or a new config file with a new `ledger_path`) for the
  cutover.
- **Mock copy kept and topped up:** stay on the old ledger. Put these users
  in their own batch, and verify that batch with `--expect-changes` (their
  source mailbox changed between the mock and the cutover).

## 6. The inventory file

CSV at the `inventory_file` path, written by the parent organisation's
administrator. The header must be `type,address,parent` (letter case does
not matter). Types are `user`, `group`, `member` and
`alias`. Use source-side addresses; the tool swaps the domain for
`dest_domain` when it checks the destination. The check matches by the part
before the `@`, so it only works when a person's source and destination
addresses share that part (`alex@` on both sides). A wrong header or a row
with any other type does not stop `verify`: the inventory check fails
(`"passed": false`) and the report shows the reason (`missing_column` or
`bad_type`).

List only members on our own domain in `member` rows. Members from the
parent organisation or outside will never exist on the destination and would
show as missing.

- `user`: `parent` is empty.
- `group`: `parent` is empty.
- `member`: `address` is the member, `parent` is the group.
- `alias`: `address` is the alias, `parent` is the user it belongs to.

```
type,address,parent
user,alex@example.org,
member,alex@example.org,team@example.org
```

Group aliases are not checked by the tool. Check those by hand.

## 7. Running the tool

All commands take `--config PATH`. The agent runs them in this form only,
from a folder the `movingday` user can read (run `cd /` first; the agent's
own home folder is private to `wsagent`):

```
cd /
sudo -u movingday /Users/movingday/venv/bin/movingday COMMAND --config /Users/movingday/data/config.toml [options]
```

| Command | What it does |
|---|---|
| `check [--source-only]` | Checks the mapping and access for all users. Prints each user's source message count, calendar counts, contact and other-contact counts, Drive counts and destination message count (the last not with `--source-only`), and a summary. Moves no data. |
| `copy --batch NAME [--mail] [--calendar] [--contacts] [--drive] [--allow-existing KEY ...] [--allow-trash KEY ...]` | Copies. With none of `--mail`, `--calendar`, `--contacts` and `--drive` it copies all four. Safe to re-run. Refuses if a destination mailbox already holds more than `max_existing` messages it did not put there, unless that user's key is listed after `--allow-existing`. A user whose Drive trashing was held (`too_many_gone`) has it released for that run only if their key is listed after `--allow-trash`. |
| `status [--batch NAME]` | Prints copied, failed and pending counts per user. |
| `verify --batch NAME [--expect-changes]` | Runs the validation checks and writes the HTML report. Use `--expect-changes` during the mock, when mail still arrives in the old account (and Drive files still change). |

`--help` is switched off. Every command prints one line of JSON and nothing
else.

### Rules for running

- Run only one `movingday` process per ledger file at a time. `copy` takes a
  lock and refuses with `already_running` if another copy is running. Do not
  run `verify` for a batch while `copy` is running on it.
- **A copy can take days.** The agent starts `copy` in the background (so it
  keeps running when the agent's turn ends) and checks progress with
  `status` from time to time. `status` only reads the ledger and is safe
  while `copy` runs.
- **Stopping a copy:** Ctrl-C does not stop a running copy promptly; the
  users already being copied finish first. To stop it, kill the `movingday`
  process. Resuming later is safe: run the same `copy` again and it carries
  on where it stopped.
- **Exit codes:**
  - `0`: success.
  - `1`: finished with failures, interrupted, or an unexpected error. A bad
    or missing config file (for example a `report_dir` with a space) gives
    exit 1 with a reason such as `internal_ValueError` or
    `internal_FileNotFoundError`.
  - `2`: refused to run (bad arguments, no config option, bad mapping,
    config or mapping file not owned by `movingday` or writable by others
    (`untrusted_file`), unknown batch, destination not empty, already
    running).
- **On any exit code other than 0 the agent stops and asks Jonathon.** For
  `verify`, `"passed": false` or `failed_checks` above 0 also means stop. The
  agent does not retry or work around it.
- **`too_many_gone` (Drive trashing held).** The source listed nothing for
  that person, or more than half of their copies would go to the trash, so
  nothing was trashed. Jonathon checks the source account: if the files
  were really deleted or given away, he tells the agent to re-run that
  batch with `--allow-trash KEY` for that person. It lifts the hold for
  that run only; trashed copies can be restored from the destination trash
  for 30 days. The agent never adds `--allow-trash` on its own.

### Earlier transfers

If mail was already transferred to a destination mailbox by another tool,
list that user's key after `--allow-existing`. On the first run for that
user, `copy` matches each earlier copy to its source message by
Message-ID, sets its labels and read/starred state to the source's, and
records it as done (`adopted` in the output). Only messages with no earlier
copy are copied. Nothing earlier is deleted.

- Messages with no Message-ID header can't be matched and are copied
  again; `verify` reports any resulting duplicates.
- Earlier copies of mail since deleted on the source stay on the
  destination. They count as extra mail already there, which `verify`
  allows.
- Adopted copies no longer count as "already there", so `verify` catches
  any further unexpected mail.
- Check `adopted` against the user's `dst_existing` from `check`: if
  `adopted` is near 0, the earlier tool did not keep Message-IDs. Stop and
  ask Jonathon before copying more.

### What the output means

Output uses user keys (`u001`), never addresses. Some fields you may see:

- `outside_shares`: shares to people, groups or domains outside our own
  (for example parent-organisation staff). They are kept as they are and
  counted. Shares to our own domain, and to groups on it, are moved to
  `dest_domain`.

Contacts: with `--contacts` (or no part flag) each user's saved contacts
and their labels are copied (`contacts_found`, `contacts_copied`,
`contacts_updated`, `contacts_failed`, `labels_created`). A contact edited on
the source is updated on the destination; a contact removed on the source is
counted in `removed_on_source` and never deleted. A failed contact makes that
user's `ok` false; run `copy` again to retry. Photos are not copied.

Drive: with `--drive` (or no part flag) every file and folder each user owns
in My Drive is copied, with its sharing (notifications off). Shared drives are
not copied. Output keys start with `drive_`: `drive_folders`,
`drive_files_copied`, `drive_files_updated` (files updated: content or name,
description, starred), `drive_files_moved`, `drive_files_trashed` (copies
moved to the destination trash because the source file was deleted or given
away; restorable for 30 days), `drive_files_failed`, `drive_kept_as_office`,
`drive_unsupported`, `drive_forms`, `drive_unplaced`, `drive_shares_added`,
`drive_shares_removed`, `drive_shares_failed` and `drive_outside_shares`. A
failed file makes that user's `ok` false; run `copy` again to retry.
Unsupported types, Forms and files kept as Office files never do.

Drive schedule:

- **Week before cutover: pre-copy Drive.** Run `copy --batch NAME --drive`
  for each batch while people still work, as often as useful. The source is
  never changed, so this is safe.
- **During the freeze: Drive top-up.** Run `copy --batch NAME --drive` once
  more (or as part of the full `copy`). It copies only what changed since the
  last run, then `verify`. The "Drive in step" check catches anything changed
  after the top-up (for example by parent-organisation staff, who are not
  locked out).

Calendars: each user's primary calendar is copied, and each team (group)
calendar they own or manage is copied once, by the first user who reaches
it. Other people's calendars that a user can manage (for example an
assistant's view of the director's calendar) and room or resource calendars
are skipped and counted.

These counts are not in the JSON output but are shown in the report's
"carried over notes" row for each user, for information: split threads,
messages with no Date header (copied, but showing a different date),
calendar entries of unsupported types not copied, outside shares, shares
that failed to copy, shared calendars skipped, other contacts (not copied),
contacts removed on the source, and for Drive: unsupported files and Forms not
copied, files kept as Office files, unplaced files, outside and failed shares,
copies moved to the destination trash, and the fixed note that comments,
suggestions and version history are not carried over. A failed share is also
caught by the `verify` sharing check, which compares the source's sharing
rules with the destination's.

### Where the report is

`verify` prints only the report's file name (for example
`mock-20261120-093000.html`). The file is in the `report_dir` from the
config, here `/Users/movingday/reports/`. Jonathon opens it (use `sudo` to
copy it somewhere he can read). The report shows the mapped user addresses,
and for every check and every user the condition, the expected result and
the actual result. It never contains mail content.

### Models and token limits

The agent only types commands; `movingday` does the copying. If the agent's
person runs out of tokens, nothing is lost: a background `copy` keeps
running, re-running it resumes, and a person can type the same commands by
hand. The risk is lost time during the freeze, not lost data.

- **Model.** Running commands and reading JSON: Sonnet at low effort.
  Checking on a running copy: Haiku, or a person typing `status`. Diagnosing
  a failure or a failed `verify` is Jonathon's job, in his own session, not
  the agent's.
- **Don't poll.** Start `copy` in the background, then check `status` about
  once an hour at most, or end the turn and come back later.
- **New session per phase:** scan, Drive pre-copy, freeze copy, verify.
  Carry only the last `status` line into the next session.
- **Keep `wsagent`'s Claude bare.** No plugins or connectors, just the
  shell. Each connected tool adds to the cost of every turn.
- **Time the freeze around limits.** Start the freeze copy just after the
  operator's usage limit resets. For the cutover week, consider a higher
  plan, or an API key with a spending cap as a backup.
- **Fallback card.** The operator keeps the exact `status` and `verify`
  lines from this section on paper, so a token limit means a pause, not a
  stall.

## 8. Standing instructions for the agent

Paste this block into the agent's instructions.

```
You run Moving Day (`movingday`), the migration tool. Follow these rules exactly.

1. Run only commands of this form, after "cd /":
   sudo -u movingday /Users/movingday/venv/bin/movingday COMMAND --config /Users/movingday/data/config.toml [options]
   COMMAND is one of: check, copy, status, verify. Nothing else.
   Start "copy" in the background and check on it with "status".
2. Never read, list, copy or open any file under /Users/movingday.
3. Never edit sudoers, file permissions, users or any Mac security setting.
   Never ask for, use or store any password. Your Mac user is not an
   administrator and must stay that way.
4. Never run a second movingday process while one is running.
5. Exit code 0 is success. On any other exit code, stop and ask Jonathon.
   For verify, also stop and ask if "passed" is false or "failed_checks" is
   above 0. Do not retry and do not work around it.
6. Report to Jonathon only what the tool prints (JSON with user keys such as
   u001). Never guess at addresses or content.
7. Do not run "copy" or "verify" for a real batch until Jonathon says so.
```

## 9. People-only steps

Jonathon or the account administrators do these. The agent never does.

- Granting and revoking domain-wide delegation.
- Go or no-go after reading the report.
- Applying the lockout in the source account (random password reset and
  sign-out of all sessions, for our users only; the accounts stay active).
- Releasing and claiming the domain, DNS changes and mail routing.
- Releasing the mail queue.
- Sign-off.
- Removing anything from the source.

## 10. Teardown (after sign-off)

Do this in order, by hand.

1. In the source account's Admin console, remove the domain-wide delegation
   entry for the source service account. Do the same for the destination.
2. In the Google Cloud console, delete both service-account keys, then the
   service accounts (or the projects).
3. Delete the key files:

   ```
   sudo rm -P /Users/movingday/keys/*
   ```

4. Keep or archive the HTML reports as the sign-off record, if wanted. Copy
   them out of `/Users/movingday/reports/` first.
5. Delete the sudoers line:

   ```
   sudo rm /etc/sudoers.d/movingday
   ```

6. Delete the `movingday` Mac user, including its home folder (System
   Settings, then Users & Groups, then delete the user and its folder). This
   removes the mapping, inventory, ledger and config. Delete the `wsagent`
   Mac user too.
