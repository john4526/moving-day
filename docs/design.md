# Moving Day: migration utility design (draft for approval)

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

- Renamed from wsmigrate to Moving Day (command `movingday`).
- The HTML report shows, for every check and every user, the condition, the
  expected result and the actual result, line by line (user requirement).
- Calendar validation also counts events missing on the destination, not
  only totals.
- Label check: a label with fewer messages on the destination is always a
  mismatch. The welcome-mail allowance only covers extra messages.
- The inventory file must have the columns `type,address,parent` (letter
  case ignored). A wrong header or an unknown type fails the inventory check
  (`"passed": false`) with the reason (`missing_column`, `bad_type`) in the
  report; it does not stop `verify`.
- Failed calendar shares are counted in the ledger (`acl_failed`, shown in
  the report's carried-over notes) and are caught by the sharing check, which
  compares the source's sharing rules with the destination's.
- Calendars copied are each user's primary calendar and team (group)
  calendars they own or manage, each copied once across all users by the
  first to claim it; other people's calendars shared with "manage sharing"
  and resource calendars are skipped and counted (`shared_calendars_skipped`).
- Domain-wide and group calendar shares on our source domain are moved to
  `dest_domain`; shares to other domains and groups are kept and counted as
  outside shares.
- The report has an informational "carried over notes" row per user: split
  threads, messages with no Date header, unsupported calendar entries not
  copied, outside shares, failed shares and shared calendars skipped.
- Unsupported calendar entry types (out-of-office, focus time, working
  location, birthdays) are reported, not treated as failures.
- `--help` is disabled; every output is one JSON line.
- `check` is now a pre-migration scan: `--source-only` uses the source key only, and the output adds own, delegated and team calendar counts and a summary. Comparisons no longer claim shared calendars.

## Contacts (added 19 November 2026, approved)

Scope change: contacts are now in scope. Each person's saved contacts ("My
Contacts") are copied with their labels. "Other contacts" (addresses Gmail
saves automatically for autocomplete) are not copied (decision A, 19 Nov);
the scan and the report show how many each person has, for information.

- API: Google People API. Source scopes add `contacts.readonly` and
  `contacts.other.readonly` (count only); destination adds `contacts`.
- Copied fields: names, email addresses, phone numbers, addresses,
  organisations, notes (biographies), birthdays, events, URLs, relations,
  nicknames, and label membership (user labels mapped by name; starred kept).
  Photos are not copied.
- Resumable and re-runnable via the ledger (source and destination resource
  names, source etag, state, reason — no contact data). A contact edited on
  the source since the last run is updated on the destination; nothing is
  duplicated. The tool never deletes contacts; a contact removed on the
  source since an earlier run is counted for information.
- `copy` gains `--contacts`; with no part flags, mail, calendars and
  contacts are all copied.
- `check` reports contacts and other contacts per person.
- The report adds checks with expected and actual: contact count, contacts
  missing on the destination, label count, contacts per label; and an
  informational line for other contacts not copied and contacts removed on
  the source.
- People API behaviours not yet confirmed (bulk-create limits, label
  handling, starred) go on the rehearsal checklist.

## Drive files (added 19 November 2026, approved)

Scope change: Drive files are now in scope (your decision, 19 Nov). This
replaces "Drive files" in the non-goals. Shared drives stay out.

### What is copied

Every file and folder each person **owns** in My Drive, not trashed:
Google Docs, Sheets and Slides, and uploaded files of any type (PDFs, Office
files, images, video). The owner's copy carries it; people it was shared
with get it back through the sharing copy, so it reappears in their
"Shared with me". Files our people can open but someone outside the mapping
file owns (for example parent-organisation staff) are not copied; the scan
counts them per person, for information.

### Approaches (decided 19 Nov: A)

- **A. Export and re-upload inside Moving Day (chosen).** Same ledger,
  re-run safety, privacy rules and report as mail and calendars; the agent
  runs it. Loses comments, suggestions and version history; every file gets
  a new address.
- **B. Google Workspace Migrate.** Keeps revisions and sharing, but it is a
  separate install operated by hand, aimed at 1,000+ user moves; same reason
  approach A in the original design was rejected.
- **C. Users download and re-upload their own files.** No code, same losses,
  70 people doing it by hand, no validation report.
- Rejected outright: sharing each source file with the destination user so
  it can be copied directly. That changes the source (anti-goal).

### What is lost (users are told before cutover)

- Comments, suggestions and version history on every file.
- File addresses: links in old mail, calendar events and other documents
  still point at the source copy.
- Formatting that does not survive a round trip through Word, Excel or
  PowerPoint format (for example some Sheets formulas, Apps Script bound to
  a file, embedded charts linked to other files).
- Files of types Google cannot export (below).

### Goals and anti-goals added

- Goal: every in-scope file reaches its owner's destination Drive, in the
  same folder structure, shared with the same people (translated through
  the mapping file).
- Goal: uploaded files arrive byte for byte identical (checked by checksum).
- Anti-goal: a sharing notification email reaching anyone during the copy.
- Anti-goal: a file name, folder name or file content appearing in agent
  output, logs, the ledger or the report.
- Anti-goal: a file landing in the wrong person's Drive.

### Access

| Side | Scope | Used for |
|---|---|---|
| Source | `drive.readonly` | list, download and export files; read sharing |
| Destination | `drive.file` | create folders and files, set sharing, read back only what the tool created |

`drive.file` is the narrowest destination scope: the tool can only see
files it created. *My call.* It also means the tool cannot see or count
files a destination user made themselves. If the rehearsal shows it is not
enough (for example for reading back sharing), fall back to `drive` and
record the decision. Like Gmail, the source grant cannot be limited to our
users: it can read every file in the parent organisation's Drive. Enable
the Google Drive API on both projects.

### How it works

For each user, under the existing per-user parallelism:

1. **Folders first**, parents before children, creating the same names on
   the destination and recording source folder to destination folder in the
   ledger.
2. **Files.** Each file is created in the destination folder that matches
   its source parent.
   - Docs, Sheets and Slides are exported (to .docx, .xlsx, .pptx) and
     uploaded with conversion back to Google format.
   - Uploaded files are downloaded and uploaded unchanged.
   - Kept: name, description, starred, modified time and created time
     (where Google allows setting them; confirm in rehearsal).
   - If Google refuses to convert an exported file back on upload, the
     .docx, .xlsx or .pptx is kept unconverted and counted as "kept as
     Office file" (your decision, 19 Nov). It counts as copied, not failed.
   - Not exportable, counted and reported, not failures: Forms (your
     decision, 19 Nov: count and report; people rebuild the ones they need
     by hand), Drawings, Sites, My Maps, Jamboards, Apps Script projects,
     shortcuts and other Google types with no export. *My call on Drawings
     and shortcuts.*
3. **Sharing.** Each file's and folder's sharing is copied with
   notifications off, translated with the same rules as calendars (users
   through the mapping file; our source domain and groups on it moved to
   `dest_domain`; anything else kept and counted as an outside share).
   Link sharing ("anyone with the link") is copied as a setting; the link
   itself is new. Sharing a destination file to someone outside Google
   without a notification is refused by Google; those are counted as failed
   shares.
4. **Files in someone else's folder.** If a file's folder belongs to
   another person in the mapping file, the file is placed in that person's
   copied folder if it already exists, otherwise at the top of the owner's
   Drive and marked "unplaced". A re-run moves unplaced files into their
   folder once it has been copied. Batches should therefore be copied with
   the folder owners first or re-run once at the end. Files in a folder
   owned by someone outside the mapping file go to the top of the owner's
   Drive and are counted.

Content handling: file content passes through memory in bounded chunks
(download chunk, upload chunk) and is never written to disk, so large
videos do not need to fit in memory.

Re-run and crash safety:

- Each created file and folder is tagged on the destination with a private
  app property holding the source file ID (an ID, not content). The ledger
  row is written "pending" before the upload and "done" after. On resume, a
  pending row is looked up on the destination by that tag before retrying,
  so a crash mid-upload does not leave a duplicate.
- Ledger rows hold source ID, destination ID, source modified time, a
  checksum for uploaded files, source parent folder ID, a fingerprint of
  the file's translated sharing, state and reason code. No names.

### Schedule: pre-copy, then top up in the freeze (your decision, 19 Nov)

Drive is copied in the week before cutover, while people are still working,
and re-run as often as useful. During the freeze, `copy --drive` runs once
more and copies only what changed. This keeps Drive volume out of the mail
outage. The schedule above gains a step 0 ("pre-copy Drive"), and step 3
includes the Drive top-up.

Every run lists each person's owned files again and compares them with the
ledger, so a re-run brings the destination back in step with the source:

- **Content changed** (source modified time or checksum differs): the
  destination file's content is replaced in place. Same file, same address
  on the destination.
- **Renamed or moved**: the destination file is renamed or moved to match.
  Moves into, out of and between other people's folders follow the
  "someone else's folder" rule above.
- **Sharing changed**: sharing added on the source is added on the
  destination. Sharing removed on the source is removed on the destination,
  but only sharing the tool itself added. *My call:* otherwise someone whose
  access was taken away on the source keeps it on the destination. This is
  the one Drive call that removes something, and it only touches
  destination files the tool created.
- **Deleted on the source, or no longer owned by this person** (trashed,
  deleted, or ownership moved to someone else): the destination copy is
  moved to the destination user's trash, where it can be restored for 30
  days, and counted. *My call:* leaving it would show people files they
  had deleted. Nothing is ever permanently deleted, and only files the tool
  created can be trashed. If ownership moved to another person in the
  mapping file, that person's run copies it as a new file. If the source
  lists nothing for a person who has copies, or more than half of a
  person's copies would go, nothing is trashed for that person that run:
  the run is not ok, with reason `too_many_gone`, and a person decides.
- **New file**: copied as usual.

The source is never changed, so the pre-copy is safe while people work. The
lockout stops our users, but parent-organisation staff who can edit our
users' files can still change them during the freeze. The top-up catches
changes made before it runs. The report's "Drive in step" check catches
anything changed after.

The mock on a subset of real users (stage 2) exercises the pre-copy and a
top-up run.

Limits to confirm in the rehearsal, not assumed:

- Google refuses to export a Google file larger than 10 MB through the
  normal export call. The fallback is the file's export link with the same
  credentials; if that also fails, the file is recorded as failed with
  reason `export_too_large`.
- Google limits uploads to 750 GB per user per day. The scan reports total
  bytes per person so this can be planned around.
- Conversion of large Sheets and Slides on upload may time out or be
  refused; failures are recorded by reason.

### Commands

- `copy` gains `--drive`. With no part flags, mail, calendars, contacts and
  Drive are all copied.
- `check` adds, per person: owned files, owned folders, total bytes,
  files of each unsupported type, files owned by people outside the mapping
  file that they can open, and files in folders owned by someone else. The
  summary adds total bytes and the largest Drive.

### Report checks (expected and actual for each)

- File count: source owned and supported versus destination created.
- Files missing on the destination (by source ID tag).
- Folder count.
- Uploaded files whose checksum does not match.
- Sharing: translated source sharing versus destination sharing, per file.
- Drive in step: source files changed, renamed, moved, re-shared or deleted
  since their last copy. Expected 0. During the mock this is reported as
  expected-to-differ, like the mail "freeze held" check.
- Informational line: unsupported files not copied (Forms counted
  separately), files kept as Office files, unplaced files, files in folders
  owned outside the mapping, outside shares, failed shares, files moved to
  the destination trash, and a fixed note that comments and version history
  are not carried over.

### Tests added

Logic tests against a fake Drive: folder tree recreated; Google files
exported and converted, uploaded files byte-identical; sharing translated
with notifications always off; re-run copies nothing new; a crash between
upload and ledger write leaves no duplicate; unplaced files are placed on a
re-run; unsupported types counted, not failed; a refused conversion keeps
the Office file; top-up runs handle changed content, rename, move, sharing
added and removed, and source deletion (destination trash, never a
permanent delete); no Drive call of any kind is made with the source
credentials except reads; the only Drive removals are destination
permission removal and destination trash, on tool-created files; the
privacy test's marker phrase in a file name, folder name and file content
never appears in output, logs, ledger or report.

### Build notes

- The source grant request to the parent organisation adds only
  `drive.readonly`. Ask for it in the same request as the contacts scopes.
- `copy --contacts` and the contacts counts in `check` (task C2 of the
  contacts plan) land before the Drive CLI work, which builds on them.
- `tests/test_no_destructive_calls.py` is widened to allow exactly the two
  destination Drive removals above, in `api.py`, and nothing else.
