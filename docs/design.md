# Migration utility: design (draft for approval)

Date: 18 November 2026. Status: approved by Jonathon Kline on 18 November 2026. Nothing is built yet.

## Intent

Copy mail and calendars for about 70 users from the source Google Workspace
account to the destination account, without paying a per-user migration
licence and without a third party handling stored data. An AI agent operates
the tool; people approve the irreversible steps. A test suite with HTML
reports is the evidence for sign-off.

The $747 mail queuing service is still bought. It covers new inbound mail
during the cutover weekend and is outside this tool.

## Goals

- Copy every in-scope message, with its labels, read state, dates and threads.
- Copy every in-scope calendar: events, recurring series, attendees, and
  sharing settings.
- Safe to re-run: a second run copies only what is missing and never
  creates duplicates.
- Resumable: an interrupted run continues where it stopped.
- The agent never sees mail or calendar content.
- A per-user HTML validation report a non-technical approver can read.

## Non-goals

- Drive files, shared drives, contacts (decided: mail and calendar only).
- Creating users, groups and aliases (done by hand in the Admin console; the
  test suite checks they exist).
- Gmail settings: filters, signatures, forwarding, send-as. Users redo these.
  *My call; say if signatures or filters must move.*
- Spam, Trash, Chat history and unsent drafts are not copied, and are left
  out of the count checks. *My call; users are told to send or save drafts
  elsewhere before cutover.*
- DNS changes, releasing or claiming the domain, mail routing.

## Anti-goals (failure even if it "works")

- One person's mail landing in another person's mailbox.
- Message or event content appearing in agent output, logs or reports.
- The tool deleting or changing anything in the source account.
- Invitations or notifications being re-sent to attendees during the copy.

## Constraints

- The parent organisation controls the source account. Access there needs
  their super administrator to grant domain-wide delegation to a service
  account. **That grant cannot be limited to our users: technically it can
  read every mailbox in the parent's account.** Assumed granted (your
  instruction, 18 Nov); not yet confirmed with the parent organisation.
- The destination users sit on a temporary domain until cutover. After
  cutover the source users are renamed. The tool therefore tracks users by
  a fixed key from the mapping file, not by email address.
- Budget: $0 tool licences. Small running cost if a cloud machine is used.

## Approaches considered

- **A. Google's free tool for mail, a script for calendars.** Least code and
  least access. Rejected because Google's tool is operated by hand in the
  Admin console and an agent cannot drive it.
- **B. One self-built tool for mail and calendars (chosen).** An agent can
  run it end to end, and no third party handles stored data. Costs: it needs
  read access to message bodies, and more code to get right.
- **C. Buy MigrationWiz, build only the tests.** Least effort, but $1,225 at
  70 users (at the budget ceiling with queuing) and a third party processes
  all stored data.

## Schedule (decided 18 Nov: queue first, then copy)

1. Route inbound mail to the queuing service.
2. Freeze: users stop using the old account (no reading, filing, sending or
   calendar changes). Queuing only stops new inbound mail; without the
   freeze, changes made during the copy would be missed.
   The freeze is enforced by a lockout, done by the parent organisation's
   administrator for our users only: reset each password to a random value
   and sign out all sessions. The accounts stay active, so the tool can
   still read them. Suspending the accounts or switching off Gmail is not
   used, because both are expected to block the tool as well. *To confirm
   in the rehearsal: the copy works on a locked account, and phones and
   mail apps are cut off.* If the parent uses single sign-on, the lock is
   applied in their sign-in system instead.
3. Copy all mail and calendars to the destination on its temporary domain.
4. Run the validation tests; a person reviews the report and gives go/no-go.
5. People move the domain and repoint mail routing to the new account.
6. People release the queue.

Because nothing changes in the source during the copy, the destination
matches it exactly and no second "catch-up" pass for changed mail is needed.

**Consequence:** staff receive no new mail from step 1 until step 6, except
addresses forwarded live by the queuing service. That outage lasts as long
as the copy. Users are copied in parallel, so the largest mailbox sets the
duration. At the assumed speed of about one message per second, a mailbox of
100,000 messages takes more than a day. The rehearsal measures real speed
and mailbox sizes before the weekend is fixed.

**Fallback if the copy will not fit the window** (your decision at that
point): copy mail older than 30 days in the week before, since old mail
rarely changes, and copy only the last 30 days during the freeze.

## Stages before the real cutover

1. **Rehearsal** on two trial Workspace accounts and a spare domain, with
   made-up data. First time the tool touches Google. Includes a practice
   domain move.
   The "spare domain" throughout this document can be subdomains of a
   domain whose DNS we can edit (for example `mig-a.`, `mig-b.` and
   `mig-move.`), so nothing needs buying. To confirm when setting up: Google
   accepts a subdomain for a trial account's own address, and whoever
   controls DNS will add the records.
2. **Mock on a subset of real users** (your decision, 18 Nov). `check`
   measures mailbox sizes for all users without copying. A small group of
   volunteers, including the largest mailbox, is locked out and copied, and
   the validation report is produced. This gives real speed, proves the
   lockout, and exercises the report. Mail keeps arriving in the old
   account during the mock, so the "freeze held" check is reported as
   expected-to-differ, not as a failure.
3. **Queue test.** Mail routing is set per domain, not per user, so the
   queue cannot be switched on for the subset alone on the live domain.
   It is tested on the spare domain during the rehearsal if the vendor
   allows it. Two questions for the vendor before buying: does one purchase
   cover a test domain or a trial, and can the queue be used twice.
4. After the mock, decide what happens to the subset's trial copy: keep it
   and top up at cutover (short outage, older mail shows its state as of
   the mock), or have an administrator delete and recreate those
   destination users so they are copied fresh.

## How it works

Python command-line tool. Libraries: Google's API client and auth libraries,
pytest, pytest-html. Progress is kept in a local SQLite file.

### Access

Two service accounts, one per Workspace account, each with the narrowest
scopes that work:

| Side | Scope | Used for |
|---|---|---|
| Source | Gmail read-only | read messages and labels |
| Source | Calendar read-only | read calendars and events |
| Source | Calendar sharing read-only | read sharing settings (exact scope to confirm in pilot; plain read-only may not allow it) |
| Destination | Gmail insert | add messages (cannot read or delete) |
| Destination | Gmail labels | create labels |
| Destination | Gmail metadata | tests read headers only, never bodies |
| Destination | Calendar | create calendars, events, sharing |
| Destination | Directory read-only | inventory for tests |

The tool does not read the source directory. *My call:* that would need a
parent-organisation administrator identity. Instead their administrator
exports a list of our users, groups, group members and aliases as a CSV
(the inventory file), and the tests check the destination against it.

The Gmail metadata scope lets the tool (not the agent) see headers,
including subjects. It cannot search, so validation lists every message and
fetches headers one by one; expect this to be slow.

Keys live in files outside the project folder, readable only by the tool's
user. They are deleted and the delegation revoked after sign-off.

### Mapping file

A CSV with four columns: key (u001, u002, ...), source address, destination
address, batch name. The key is the user's permanent name inside the tool:
the ledger, the agent-facing output and the test names all use it. It also
defines which users are "ours".

*Changed at planning, 18 Nov:* the draft tracked users by Google's permanent
user ID. That needs directory access on the source, which the tool does not
have, and under the queue-then-copy schedule all copying finishes before
any address is renamed. The key column does the same job with no extra
access.

The tool refuses to run if any key, source or destination appears twice, or
if a destination mailbox already holds more than a handful of messages it
did not put there (default limit 5, to allow for Google's welcome mail),
unless explicitly overridden for that user.

### Mail copy

For each user: list source message IDs; skip those already in the ledger;
for each remaining one, download the raw message into memory, insert it into
the destination with the original date, labels and read/starred state, then
record source ID, destination ID and a hash of the Message-ID header in the
ledger. Content is never written to disk.

- Labels are created on the destination first, including nested ones.
- Dates: the destination date is taken from the message's own Date header.
  Messages with a missing or wrong header will show a different date; these
  are counted and reported.
- Threads: each thread's first message is inserted first, and the ledger
  maps source thread to destination thread. Gmail also requires matching
  subject and reply headers, so some threads may split; splits are counted
  and reported, not treated as failures.
- Crash safety: the ledger row is written as "pending" before the insert
  and "done" after. On resume, pending rows are checked against the
  destination by Message-ID before retrying. The tool cannot delete, so any
  duplicate that slips through is detected and reported by the tests.
- Speed is not promised. Google's limits are measured in the pilot (expect
  on the order of one message per second per mailbox) before any duration
  is quoted.
- Messages are inserted directly, not delivered, so nothing is re-scanned,
  auto-forwarded or bounced.
- Rate limits and transient errors: retry with backoff. Permanent failures
  are recorded by ID and reason code and reported, not retried forever.
- Several users run in parallel; one user's messages run in order.

### Calendar copy

For each user: create their secondary calendars on the destination; copy
events using the import call, which keeps the event's identity and does not
send invitations; copy sharing settings with notifications off, translating
addresses through the mapping file. Re-runs use the ledger to update
changed events and cancel deleted ones, with notifications off, rather than
importing again.

- Attendee and organiser addresses are left as they are, because the domain
  itself moves. Sharing with people who are not in the mapping file
  (for example parent-organisation staff) is kept as an outside address and
  counted in the report. *My call.*
- Special event types the import call may not accept (out-of-office, focus
  time, working location) are attempted, and otherwise reported and left out
  of the count checks.

To be confirmed in the pilot, not assumed: Meet links, room bookings, and
events where the user is a guest rather than the organiser.

### Commands (what the agent runs)

- `check` : validates the mapping file and access. No data moves.
- `copy --batch NAME [--mail] [--calendar]` : copies; safe to re-run.
- `status [--batch NAME]` : counts per user: found, copied, failed, remaining.
- `verify --batch NAME` : runs the validation tests and writes the HTML report.

Every command prints a small JSON summary. There is no delete command and no
code path that modifies the source.

### Privacy rules for an agent-run tool

- Output and logs are built from an allow-list of fields: user key, counts,
  IDs, status and reason codes. Subjects, bodies, addresses of
  correspondents, event titles and attendee names are never printed.
- Users appear in agent-facing output as short keys (u001, u002). The HTML
  report for the human approver shows the real addresses.
- Google error messages are reduced to their error code before printing.
- The agent is Claude, or Codex as a stand-in. The tool runs under its own
  operating-system user on the machine. The agent's user may run the four
  commands as that user and nothing else, so it cannot open the key files,
  the ledger or the mapping file. *My call.* Assumed machine: your Mac, kept
  awake for the duration of the copy.

### Steps only a person does

Granting and revoking delegation; go/no-go; releasing and claiming the
domain; DNS and mail routing; releasing the mail queue; sign-off; removing
anything from the source.

## Test suite

pytest, with pytest-html producing one self-contained HTML file per run.

**1. Logic tests (no network, run any time).** Against a fake Google API
with made-up data:
- mapping file: duplicates, missing users, malformed rows are rejected
- re-running a copy creates no duplicates
- an interrupted copy resumes and finishes
- labels, nested labels, read and starred state carry over
- threads stay grouped
- recurring events, exceptions and all-day events carry over
- sharing settings are translated through the mapping
- rate-limit and server errors are retried; permanent errors are recorded
- no invitation or notification option is ever switched on
- the tool makes no delete or modify call against the source
- **privacy:** a made-up message with a marker phrase in subject, body,
  sender and event title is copied, and the test fails if the marker appears
  anywhere in output, logs, the ledger or the HTML report

**2. Rehearsal tests (two trial Workspace accounts and a spare domain, with
seeded made-up data).** The same checks against real Google accounts, plus:
the calendar items listed above as unconfirmed, measured copy speed, and a
full practice of the domain move to see what happens to meetings when the
organiser's address changes account.

**3. Validation tests (the real migration, per user, per batch).**
- message count on the destination matches the source, per label
- every source Message-ID is present on the destination (compared as hashes)
- no destination message came from a different user's source mailbox
- calendar count, event count, recurring-series count and attendee counts match
- sharing settings match after translation
- every group, group member and alias in the inventory file exists on the
  destination
- no message appears twice on the destination
- nothing is left in the failed list, or each failure is listed by reason
- the freeze held: each source mailbox's change marker is the same at the
  end of the copy as at the start

The report shows pass/fail per user and per check, with counts only.

## Left to the builder

File layout, the fake API's design, parallelism level, backoff timings, and
the report's styling.

## Decisions and what is still open

Decided by you on 18 Nov: parent access assumed; agent is Claude or Codex;
a rehearsal with a spare domain is supported; queue first, then copy; no
limit on how far back mail is copied.

Still open, settled by the rehearsal rather than by a decision now:

- Total mail volume and real copy speed, which set how long the mail outage
  lasts and whether the fallback above is needed.
- The Google API behaviours marked "confirm in pilot" in this document.

## Changes made during the build (18 Nov 2026)

These were made while building the tool. Approved sections above are unchanged.

- The HTML report shows, for every check and every user, the condition, the
  expected result and the actual result, line by line (user requirement).
- Calendar validation also counts events missing on the destination, not
  only totals.
- Label check: a label with fewer messages on the destination is always a
  mismatch. The welcome-mail allowance only covers extra messages.
- The inventory file must have exactly the columns `type,address,parent`.
  Anything else stops `verify` with a reason.
- Failed calendar shares are counted (`acl_failed`) and caught by the
  sharing check.
- Unsupported calendar entry types (out-of-office, focus time, working
  location, birthdays) are reported, not treated as failures.
- `--help` is disabled; every output is one JSON line.
