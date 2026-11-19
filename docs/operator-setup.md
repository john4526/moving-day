# Operator setup

This document is for Jonathon (the administrator) and for the AI agent that
runs the tool. It covers setup, daily use and teardown. Every step is
numbered. Words in `code style` are typed exactly as shown.

## Who does what

- **Jonathon does all the setup and security steps by hand.** This includes
  the new Mac user, the sudoers line, file permissions, service accounts and
  delegation. These change the Mac's security settings and the Google
  accounts' access. **An agent must not make any of these changes**, even if
  asked to, and even if a step seems to be failing.
- **The agent only runs the four `wsmigrate` commands** (see "Running the
  tool") and reports what they print.

## 1. Create the service accounts (administrators of each account)

A service account is a robot login that the tool uses instead of a person.
"Domain-wide delegation" lets that robot act as any user in the account. You
need one service account for the source and one for the destination.

Do these steps once for each account, signed in as a super administrator of
that account. For the source this is the parent organisation's administrator.

1. In the Google Cloud console, create a project (or pick an existing one).
2. In that project, enable these APIs: Gmail API and Google Calendar API.
   For the destination only, also enable Admin SDK API.
3. Create a service account. Create a JSON key for it and download the file.
   Treat the key like a password.
4. Copy the service account's numeric Client ID.
5. In the Google Admin console, go to Security, then Access and data
   control, then API controls, then Domain-wide delegation. Add a new entry
   with that Client ID and the scopes below, pasted as one comma-separated
   line. Use exactly these scopes and no others.

**Source account scopes** (read only):

```
https://www.googleapis.com/auth/gmail.readonly,https://www.googleapis.com/auth/calendar.readonly,https://www.googleapis.com/auth/calendar.acls.readonly
```

**Destination account scopes:**

```
https://www.googleapis.com/auth/gmail.insert,https://www.googleapis.com/auth/gmail.labels,https://www.googleapis.com/auth/gmail.metadata,https://www.googleapis.com/auth/calendar,https://www.googleapis.com/auth/admin.directory.user.readonly,https://www.googleapis.com/auth/admin.directory.group.readonly
```

Notes:

- The source grant cannot be limited to our users. Technically it can read
  every mailbox in the parent organisation. Revoke it as soon as the
  migration is signed off (see Teardown).
- If Google refuses `calendar.acls.readonly` on the source, stop. The
  fallback is the full `https://www.googleapis.com/auth/calendar` scope on
  the source. This must be a recorded decision, and the code's scope list in
  `wsmigrate/runtime.py` must be changed to match. See the rehearsal
  checklist.
- The destination also needs an administrator address in the config
  (`dest_admin`). The tool uses it for directory lookups.

## 2. Create the separate Mac user (Jonathon, by hand)

The tool runs as its own Mac user, `wsmigrate`. The agent runs as Jonathon's
user and can run the tool as `wsmigrate`, but cannot open `wsmigrate`'s
files. That is how the agent is kept from seeing keys, mapping files or
reports.

**These are changes to the Mac's security settings. Jonathon makes them. An
agent must not.**

1. Create a standard (not administrator) user named `wsmigrate`. Use System
   Settings, then Users & Groups. Give it a long random password and do not
   save it anywhere the agent can read. Its home folder is
   `/Users/wsmigrate`.
2. In a Terminal, make its home folder private:

   ```
   sudo chmod 700 /Users/wsmigrate
   ```

3. Create the tool's folders as that user and set mode 700 on each:

   ```
   sudo -u wsmigrate mkdir -p /Users/wsmigrate/keys /Users/wsmigrate/data /Users/wsmigrate/reports
   sudo -u wsmigrate chmod 700 /Users/wsmigrate/keys /Users/wsmigrate/data /Users/wsmigrate/reports
   ```

4. Install the tool in a virtual environment (a private copy of Python and
   the tool's libraries) at `/Users/wsmigrate/venv`. Copy the project folder
   to a place `wsmigrate` can read (for example a temporary folder), then:

   ```
   sudo -u wsmigrate python3 -m venv /Users/wsmigrate/venv
   sudo -u wsmigrate /Users/wsmigrate/venv/bin/pip install /path/to/project
   ```

   The tool needs Python 3.11 or newer.
5. Put the files in place, each owned by `wsmigrate`, using `sudo`:
   - the two service-account key files in `/Users/wsmigrate/keys/`
   - the mapping file as `/Users/wsmigrate/data/mapping.csv`
   - the inventory file as `/Users/wsmigrate/data/inventory.csv`
   - the config file as `/Users/wsmigrate/data/config.toml`
   - the ledger (progress database) is created by the tool at
     `/Users/wsmigrate/data/ledger.sqlite`
   - reports are written to `/Users/wsmigrate/reports/`
6. Make sure every file is mode 600 and every folder mode 700, and nothing is
   readable by other users:

   ```
   sudo chmod -R go-rwx /Users/wsmigrate
   ls -ld /Users/wsmigrate /Users/wsmigrate/*
   ```

   The last command should show `drwx------` for every folder.
7. Keep the Mac awake for the whole copy (System Settings, then Lock Screen
   or Battery, or `caffeinate`).

## 3. Let the operator's user run only the tool (Jonathon, by hand)

This sudoers line lets Jonathon's Mac user run exactly one program, as
`wsmigrate`, and nothing else. Replace `jonathon` with the short name of
Jonathon's Mac user. The agent runs under this user.

Edit it with `visudo`, which checks the syntax before saving. A typo in a
sudoers file can lock you out of `sudo`.

```
sudo visudo -f /etc/sudoers.d/wsmigrate
```

Put this single line in the file:

```
jonathon ALL=(wsmigrate) NOPASSWD: /Users/wsmigrate/venv/bin/wsmigrate
```

Save and exit. Then test it:

```
sudo -u wsmigrate /Users/wsmigrate/venv/bin/wsmigrate check --config /Users/wsmigrate/data/config.toml
```

An agent must not edit `/etc/sudoers.d/` or any sudoers file.

## 4. The config file

TOML file at `/Users/wsmigrate/data/config.toml`. Every field is shown.
Replace the placeholders.

```toml
source_key_file = "/Users/wsmigrate/keys/source-key.json"
dest_key_file   = "/Users/wsmigrate/keys/dest-key.json"
mapping_file    = "/Users/wsmigrate/data/mapping.csv"
inventory_file  = "/Users/wsmigrate/data/inventory.csv"
ledger_path     = "/Users/wsmigrate/data/ledger.sqlite"
report_dir      = "/Users/wsmigrate/reports"   # must contain no spaces
dest_admin      = "admin@mig-b.example.org"    # admin address for directory lookups
dest_domain     = "mig-b.example.org"          # the temporary destination domain
parallel_users  = 4                            # optional, default 4
max_existing    = 5                            # optional, default 5
```

- `report_dir` must contain no spaces. The tool refuses to start otherwise.
- `max_existing` is how many messages a destination mailbox may already hold
  (Google's welcome mail). More than this and `copy` refuses to run.
- `sudo` removes environment variables, so always pass `--config` on the
  command line rather than relying on `WSMIGRATE_CONFIG`.

## 5. The mapping file

CSV at the `mapping_file` path. The header must be exactly these four column
names. The key (`u001`, `u002`, ...) is the user's permanent name inside the
tool. Keys, source addresses and destination addresses must each be unique.
Batch names are free text, for example `mock` or `all`.

```
key,source,destination,batch
u001,alex@example.org,alex@mig-b.example.org,mock
u002,sam@example.org,sam@mig-b.example.org,mock
```

If a row is wrong, the tool stops and prints a reason (for example
`duplicate_key`, `bad_address`, `empty_batch`) and the row number.

## 6. The inventory file

CSV at the `inventory_file` path, written by the parent organisation's
administrator. The header must be exactly `type,address,parent`. Anything
else stops `verify` with a reason. Types are `user`, `group`, `member` and
`alias`. Use source-side addresses; the tool swaps the domain for
`dest_domain` when it checks the destination.

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

All commands take `--config PATH`. The agent runs them in this form only:

```
sudo -u wsmigrate /Users/wsmigrate/venv/bin/wsmigrate COMMAND --config /Users/wsmigrate/data/config.toml [options]
```

| Command | What it does |
|---|---|
| `check` | Checks the mapping and access for all users. Prints each user's source message count and destination message count. Moves no data. |
| `copy --batch NAME [--mail] [--calendar] [--allow-existing KEY ...]` | Copies. With neither `--mail` nor `--calendar` it copies both. Safe to re-run. Refuses if a destination mailbox already holds more than `max_existing` messages it did not put there, unless that user's key is listed after `--allow-existing`. |
| `status [--batch NAME]` | Prints copied, failed and pending counts per user. |
| `verify --batch NAME [--expect-changes]` | Runs the validation checks and writes the HTML report. Use `--expect-changes` during the mock, when mail still arrives in the old account. |

`--help` is switched off. Every command prints one line of JSON and nothing
else.

### Rules for running

- Run only one `wsmigrate` process per ledger file at a time. `copy` takes a
  lock and refuses with `already_running` if another copy is running. Do not
  run `verify` for a batch while `copy` is running on it.
- **Exit codes:**
  - `0`: success.
  - `1`: finished, but with failures (or interrupted).
  - `2`: refused to run (bad arguments, no config, bad mapping, unknown
    batch, destination not empty, already running).
- **On exit code 2, or any failed check, the agent stops and asks
  Jonathon.** It does not retry or work around it.

### What the output means

Output uses user keys (`u001`), never addresses. Some fields you may see:

- `acl_failed`: calendar shares that failed to copy in this run. The
  sharing check in `verify` catches them.
- `outside_shares`: shares to people who are not in the mapping file (for
  example parent-organisation staff). They are kept as they are and counted.
- `no_date_header`: messages with no Date header. They are copied but will
  show a different date.

### Where the report is

`verify` prints only the report's file name (for example
`mock-20261120-093000.html`). The file is in the `report_dir` from the
config, here `/Users/wsmigrate/reports/`. Jonathon opens it (use `sudo` to
copy it somewhere he can read). The report shows the mapped user addresses,
and for every check and every user the condition, the expected result and
the actual result. It never contains mail content.

## 8. Standing instructions for the agent

Paste this block into the agent's instructions.

```
You run the wsmigrate migration tool. Follow these rules exactly.

1. Run only commands of this form:
   sudo -u wsmigrate /Users/wsmigrate/venv/bin/wsmigrate COMMAND --config /Users/wsmigrate/data/config.toml [options]
   COMMAND is one of: check, copy, status, verify. Nothing else.
2. Never read, list, copy or open any file under /Users/wsmigrate.
3. Never edit sudoers, file permissions, users or any Mac security setting.
4. Never run a second wsmigrate process while one is running.
5. Exit code 0 is success, 1 is finished with failures, 2 is refused to run.
   On exit code 2, or on any failed check or "ok": false, stop and ask
   Jonathon. Do not retry and do not work around it.
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
   sudo rm -P /Users/wsmigrate/keys/*
   ```

4. Keep or archive the HTML reports as the sign-off record, if wanted. Copy
   them out of `/Users/wsmigrate/reports/` first.
5. Delete the sudoers line:

   ```
   sudo rm /etc/sudoers.d/wsmigrate
   ```

6. Delete the `wsmigrate` Mac user, including its home folder (System
   Settings, then Users & Groups, then delete the user and its folder). This
   removes the mapping, inventory, ledger and config.
