# Workspace Migration Utility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Python command-line tool, `wsmigrate`, that copies mail and calendars between two Google Workspace accounts, is operated by an AI agent that never sees content, and proves its result with a pytest suite that writes HTML reports.

**Architecture:** Thin wrapper classes (`Gmail`, `Calendar`, `Directory`) are the only code that talks to Google; in-memory fakes with the same methods back every logic test. Copy logic writes progress to a SQLite ledger so runs are resumable and re-runnable. All agent-facing output goes through one allow-list emitter.

**Tech Stack:** Python 3.11+ (3.14 is installed), `google-api-python-client`, `google-auth`, `pytest`, `pytest-html`; stdlib `sqlite3`, `tomllib`, `argparse`, `csv`, `hashlib`, `fcntl`, `concurrent.futures`.

**Spec:** `docs/design.md` (approved 18 Nov 2026). Read it before any task.

**Out of this plan:** the rehearsal, the mock and the cutover are operations on real accounts. This plan ends with the tool, its tests and the checklists those operations follow. The real `Gmail`/`Calendar`/`Directory` wrappers cannot be tested until the rehearsal; everything else can.

## Global Constraints

- No dependencies beyond the four named above.
- No code path calls a delete, trash or modify method on the **source** account, or on destination **mail**. The only delete in the codebase is `Calendar.delete_event` on the destination.
- Every calendar write passes `sendUpdates="none"`; every sharing write passes `sendNotifications=False`.
- Nothing is printed except through `safeout.emit`. No `print`, no `logging` to stdout/stderr, no tracebacks carrying exception messages.
- Message and event content is held in memory only; never written to disk, the ledger or a log.
- Users appear in output, ledger and test IDs by key (`u001`), never by address. Only the HTML report shows addresses.
- Scopes, exact strings. Source: `https://www.googleapis.com/auth/gmail.readonly`, `https://www.googleapis.com/auth/calendar.readonly`, `https://www.googleapis.com/auth/calendar.acls.readonly`. Destination: `https://www.googleapis.com/auth/gmail.insert`, `https://www.googleapis.com/auth/gmail.labels`, `https://www.googleapis.com/auth/gmail.metadata`, `https://www.googleapis.com/auth/calendar`, `https://www.googleapis.com/auth/admin.directory.user.readonly`, `https://www.googleapis.com/auth/admin.directory.group.readonly`. (`calendar.acls.readonly` is unverified; rehearsal checklist covers it.)
- Not copied and excluded from every count: labels `SPAM`, `TRASH`, `DRAFT`, `CHAT`.
- Commit after each task. Never commit keys, CSVs, the ledger or reports (`.gitignore` already blocks them).

## Review Focus

Inputs the spec does not mention that are most likely to bite. Each has a test in the task named.

1. **Mapping file exported from Excel:** byte-order mark, mixed-case addresses, trailing spaces. Expected: normalised and accepted; duplicates that differ only by case are still rejected. (Task 1)
2. **The agent starts `copy` twice** because the first looked hung. Expected: the second exits at once with reason `already_running` and copies nothing. (Task 8)
3. **Messages with no Message-ID header, or two messages sharing one.** Expected: each is copied exactly once and validation does not report them missing or duplicated. (Tasks 5, 7)
4. **Label names that differ only by case, or already exist on the destination.** Expected: mapped to the existing label, not a crash on create. (Task 5)
5. **A user with an empty mailbox or no calendars.** Expected: zero counts, all checks pass. (Tasks 5, 6, 9)

---

## File structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | package metadata, dependencies, `wsmigrate` console script |
| `wsmigrate/config.py` | load the TOML config |
| `wsmigrate/mapping.py` | load and validate the mapping CSV |
| `wsmigrate/safeout.py` | the only output path; allow-list enforcement |
| `wsmigrate/ledger.py` | SQLite progress store |
| `wsmigrate/api.py` | `ApiError`, `with_retry`, real `Gmail`/`Calendar`/`Directory` wrappers |
| `wsmigrate/runtime.py` | `Apis` bundle and `get_apis(config)`; the seam tests patch |
| `wsmigrate/mail.py` | mail copy |
| `wsmigrate/calendar_copy.py` | calendar copy |
| `wsmigrate/compare.py` | source/destination comparisons that return counts |
| `wsmigrate/cli.py` | `check`, `copy`, `status`, `verify` |
| `wsmigrate/validation/conftest.py`, `test_validation.py` | the per-user validation suite `verify` runs |
| `tests/fakes.py` | `FakeGmail`, `FakeCalendar`, `FakeDirectory` |
| `tests/test_*.py` | logic tests, one file per module |
| `docs/operator-setup.md`, `docs/rehearsal-checklist.md` | what a person does |

---

### Task 1: Project skeleton, config and mapping file

**Files:**
- Create: `pyproject.toml`, `wsmigrate/__init__.py`, `wsmigrate/config.py`, `wsmigrate/mapping.py`
- Test: `tests/test_mapping.py`, `tests/test_config.py`

**Interfaces — Produces:**

```python
# config.py
@dataclass(frozen=True)
class Config:
    source_key_file: str; dest_key_file: str
    mapping_file: str; inventory_file: str
    ledger_path: str; report_dir: str          # report_dir must contain no spaces
    dest_admin: str                            # admin address used for Directory calls
    dest_domain: str                           # the temporary domain
    parallel_users: int = 4
    max_existing: int = 5
def load_config(path: str) -> Config           # TOML; path from --config or $WSMIGRATE_CONFIG

# mapping.py
@dataclass(frozen=True)
class User: key: str; source: str; destination: str; batch: str
class MappingError(Exception):                 # .reason: str, .row: int (1-based, header is row 1)
class Mapping:
    users: list[User]
    def batch(self, name: str) -> list[User]
    def by_key(self, key: str) -> User
    def dest_for(self, source_address: str) -> str | None   # case-insensitive
def load_mapping(path: str) -> Mapping
```

Mapping CSV header is exactly `key,source,destination,batch`. Read as `utf-8-sig`; strip every cell; lowercase addresses. Key must match `^u\d{3}$`. Reasons: `missing_column`, `bad_key`, `bad_address` (no single `@`, or empty), `duplicate_key`, `duplicate_source`, `duplicate_destination`, `same_source_and_destination`, `empty_batch`.

- [ ] **Step 1:** Write `pyproject.toml` (name `wsmigrate`, `requires-python = ">=3.11"`, the four dependencies, script `wsmigrate = "wsmigrate.cli:main"`, pytest `testpaths = ["tests"]`). Create a venv and `pip install -e .`.
- [ ] **Step 2:** Write failing tests in `tests/test_mapping.py`:
  - `test_valid_file_loads`: three rows in two batches; `batch("b1")` returns two users; `dest_for("SAM@old.org")` returns the mapped address.
  - one test per reason above, each asserting `exc.reason` and `exc.row`.
  - `test_excel_export_is_normalised`: file starts with a BOM, has `" Sam@Old.org "`; loads, and `users[0].source == "sam@old.org"`.
  - `test_duplicates_differing_only_by_case_rejected`: `sam@old.org` and `SAM@old.org` → `duplicate_source`.
- [ ] **Step 3:** Run `pytest tests/test_mapping.py -v`. Expected: FAIL, module not found.
- [ ] **Step 4:** Implement `mapping.py`, then `config.py` with `tests/test_config.py::test_loads_and_applies_defaults` and `::test_report_dir_with_space_rejected`.
- [ ] **Step 5:** Run `pytest -v`. Expected: all PASS.
- [ ] **Step 6:** Commit: `feat: config and mapping file loader`.

### Task 2: Safe output

**Files:**
- Create: `wsmigrate/safeout.py`
- Test: `tests/test_safeout.py`

**Interfaces — Produces:**

```python
class UnsafeOutput(Exception): ...
ALLOWED_KEYS: frozenset[str]
def emit(obj: dict, stream=None) -> None       # one line of JSON to stream (default sys.stdout)
def reason_for(exc: BaseException) -> str      # ApiError -> its .reason; MappingError -> its .reason;
                                               # anything else -> "internal_" + type(exc).__name__
```

`ALLOWED_KEYS`: `command batch user ok reason row found copied skipped failed remaining pending src_messages dst_existing calendars events_copied events_updated events_removed events_failed acl_copied outside_shares no_date_header threads_src threads_dst users passed failed_checks checks name failures report seconds`.

Value rule, applied recursively: `int`, `bool`, `None`; `str` matching `^[A-Za-z0-9_./-]{1,120}$`; `list` of dicts; `dict` whose keys are all allowed — except under `failures`, whose keys are reason strings (same pattern) and values ints. Anything else raises `UnsafeOutput` and nothing is written.

- [ ] **Step 1:** Write failing tests: `test_emits_one_json_line`; `test_unknown_key_rejected` (`{"subject": "x"}`); `test_string_with_space_rejected`; `test_string_with_at_sign_rejected`; `test_nested_list_checked` (bad key inside `users[0]`); `test_failures_map_allows_reason_keys`; `test_nothing_written_when_rejected`; `test_reason_for_unknown_exception_has_no_message` (`reason_for(ValueError("secret text")) == "internal_ValueError"`).
- [ ] **Step 2:** Run `pytest tests/test_safeout.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement `safeout.py`.
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: allow-list output emitter`.

### Task 3: Ledger

**Files:**
- Create: `wsmigrate/ledger.py`
- Test: `tests/test_ledger.py`

**Interfaces — Produces:**

```python
class Ledger:
    def __init__(self, path: str)              # creates tables; WAL mode; one instance per thread
    # mail
    def done_ids(self, user_key) -> set[str]
    def mark_pending(self, user_key, src_id, src_thread, msgid_hash) -> None
    def mark_done(self, user_key, src_id, dst_id, dst_thread) -> None
    def mark_failed(self, user_key, src_id, reason) -> None
    def pending(self, user_key) -> list[tuple[str, str]]          # (src_id, msgid_hash)
    def drop_pending(self, user_key, src_id) -> None
    def done_hash_count(self, user_key, msgid_hash) -> int
    def dst_thread(self, user_key, src_thread) -> str | None      # from any done row of that thread
    def counts(self, user_key) -> dict                            # {"done","failed","pending"}
    def failures(self, user_key) -> dict[str, int]                # reason -> count, mail and events
    # calendar
    def cal_map(self, user_key) -> dict[str, str]                 # src_cal -> dst_cal
    def set_cal(self, user_key, src_cal, dst_cal) -> None
    def event_rows(self, user_key, src_cal) -> dict[str, tuple[str, str, str]]  # src_event -> (dst_event, updated, state)
    def set_event(self, user_key, src_cal, src_event, dst_event, updated, state, reason=None) -> None
    def drop_event(self, user_key, src_cal, src_event) -> None
    # per-user facts
    def set_meta(self, user_key, name, value: str) -> None
    def get_meta(self, user_key, name) -> str | None
```

Tables: `messages(user_key, src_id, src_thread, dst_id, dst_thread, msgid_hash, state, reason, PRIMARY KEY(user_key, src_id))`, `calendars`, `events`, `meta`. States: `pending`, `done`, `failed`. A row marked failed is not in `done_ids`, so the next run retries it. Meta names used later: `preexisting`, `history_start`, `history_end`, `no_date_header`, `outside_shares`, `skipped_event_types`.

- [ ] **Step 1:** Write failing tests: `test_pending_then_done_roundtrip`; `test_failed_row_is_retried` (not in `done_ids`, counted in `failures`); `test_dst_thread_lookup`; `test_reopen_keeps_state` (close, new `Ledger` on same path); `test_users_are_isolated`; `test_event_rows_roundtrip`; `test_meta_roundtrip`.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: sqlite ledger`.

### Task 4: Google wrappers, retry and fakes

**Files:**
- Create: `wsmigrate/api.py`, `wsmigrate/runtime.py`, `tests/fakes.py`
- Test: `tests/test_api.py`

**Interfaces — Produces:**

```python
# api.py
class ApiError(Exception):                     # .reason: str ("http_404", "rate_limited", "http_503"...), .retryable: bool
def with_retry(fn, *, sleep=time.sleep, tries: int = 6):   # waits 1,2,4,8,16,32 s on retryable ApiError; re-raises after
@dataclass
class RawMessage: raw: bytes; label_ids: list[str]; thread_id: str

class Gmail:
    def __init__(self, key_file: str, scopes: list[str])
    def profile(self, user) -> dict            # {"messagesTotal": int, "threadsTotal": int, "historyId": str}
    def list_labels(self, user) -> list[dict]  # {"id","name","type": "system"|"user","messagesTotal": int}
    def create_label(self, user, name) -> str  # returns id; on "already exists" returns the existing id
    def list_messages(self, user) -> list[tuple[str, str]]   # (id, threadId), newest first, no spam/trash
    def get_raw(self, user, msg_id) -> RawMessage
    def get_message_id_header(self, user, msg_id) -> str | None   # metadata format only
    def insert(self, user, raw, label_ids, thread_id) -> tuple[str, str]  # (id, threadId); internalDateSource="dateHeader"

class Calendar:
    def __init__(self, key_file: str, scopes: list[str])
    def list_calendars(self, user) -> list[dict]   # {"id","summary","timeZone","primary": bool,"accessRole"}
    def create_calendar(self, user, summary, time_zone) -> str
    def list_events(self, user, cal_id) -> list[dict]   # event resources; series and exceptions; no deleted
    def import_event(self, user, cal_id, body) -> str
    def update_event(self, user, cal_id, event_id, body) -> None
    def delete_event(self, user, cal_id, event_id) -> None
    def list_acl(self, user, cal_id) -> list[dict]      # {"role","scope": {"type","value"}}
    def insert_acl(self, user, cal_id, rule) -> None

class Directory:
    def __init__(self, key_file: str, admin: str)
    def list_users(self) -> set[str]
    def list_groups(self) -> dict[str, set[str]]        # group address -> member addresses
    def list_aliases(self) -> dict[str, str]            # alias -> user address

# runtime.py
@dataclass
class Apis: src_gmail: Gmail; dst_gmail: Gmail; src_cal: Calendar; dst_cal: Calendar; dst_dir: Directory
def get_apis(config: Config) -> Apis           # builds real wrappers with the Global Constraints scopes
```

Real wrappers: `service_account.Credentials.from_service_account_file(...).with_subject(user)`, one service per (thread, user); convert every `HttpError` to `ApiError` using status only (429, and 403 with a rate-limit reason → `rate_limited`, retryable; 5xx retryable; others not). Never put response text in the exception. `insert` uses resumable media upload. Each public method calls Google through `with_retry`.

Fakes (`tests/fakes.py`): `FakeGmail`, `FakeCalendar`, `FakeDirectory` with the same methods over in-memory dicts, plus:
- `.calls: list[tuple[str, dict]]` recording every method name and keyword arguments;
- `FakeGmail.add_message(user, raw, label_ids, thread_id=None) -> str` and `FakeCalendar.add_event(...)`, `add_calendar(...)`, `add_acl(...)` for seeding;
- `.fail_next(method, error: ApiError, times=1)` to inject errors;
- `FakeGmail.insert` parses the Message-ID header out of `raw` so `get_message_id_header` works, and treats label names case-insensitively in `create_label`;
- `FakeCalendar.import_event`/`update_event`/`delete_event`/`insert_acl` record the `sendUpdates`/`sendNotifications` value they were given (wrappers pass them explicitly, so the fake's signature takes them as keyword arguments with no default).
- `make_apis() -> Apis` returning a bundle of fresh fakes.

- [ ] **Step 1:** Write failing tests: `test_retry_waits_then_succeeds` (fake fn fails twice with retryable error; injected `sleep` saw `[1, 2]`); `test_retry_gives_up_after_six`; `test_non_retryable_raises_immediately`; `test_fake_gmail_roundtrip` (add, list newest-first, get_raw, insert, header lookup); `test_fake_create_label_is_case_insensitive`; `test_fake_records_calls`.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement `with_retry`, `ApiError`, the fakes, then the real wrappers and `runtime.py`.
- [ ] **Step 4:** Run `pytest -v`. Expected: PASS. Also run `python -c "import wsmigrate.api, wsmigrate.runtime"`. Expected: no error.
- [ ] **Step 5:** Commit: `feat: google wrappers, retry and test fakes`.

### Task 5: Mail copy

**Files:**
- Create: `wsmigrate/mail.py`
- Test: `tests/test_mail.py`

**Interfaces:**
- Consumes: `Gmail` (or `FakeGmail`), `Ledger`, `ApiError`.
- Produces:

```python
SKIP_LABELS = {"SPAM", "TRASH", "DRAFT", "CHAT"}
def msgid_hash(raw: bytes) -> str     # sha256 hex of the stripped Message-ID header value;
                                      # if there is none: "raw:" + sha256 hex of the whole message
@dataclass
class MailStats: found: int; copied: int; skipped: int; failed: int
def copy_mail(src, dst, ledger, user_key, src_user, dst_user) -> MailStats
```

Algorithm (order matters):
1. If `get_meta(user_key, "preexisting")` is unset, store `dst.profile(dst_user)["messagesTotal"]`. Store `history_start` from the source profile.
2. Build the label map: system labels map to themselves by id; user labels map by name, compared case-insensitively against destination labels, created when absent.
3. Recover pending rows: if any exist, build a `Counter` of destination hashes (`list_messages` + `get_message_id_header`; messages with no header are ignored). For each pending row, if the destination count for its hash exceeds `done_hash_count`, mark it done with empty destination ids; otherwise drop it so it is retried.
4. `list_messages(src_user)`, reversed so the oldest comes first (a thread's first message is inserted before its replies). `# ponytail: relies on list order being newest-first; sort by internalDate if the rehearsal shows split threads`.
5. For each id not in `done_ids`: `get_raw`; if any label is in `SKIP_LABELS`, count as skipped; else `mark_pending`, `insert` with mapped labels and `ledger.dst_thread(...)`, `mark_done`. On `ApiError`: `mark_failed(reason)` and continue.
6. Count messages with no `Date` header into meta `no_date_header`. Store `history_end` from the source profile.

- [ ] **Step 1:** Write failing tests (each seeds `make_apis()` and a temp ledger):
  - `test_copies_all_messages_with_labels_and_state`: three messages with `INBOX`, `UNREAD`, `STARRED` and user label `Donors/2025`; destination has all three with the same label names.
  - `test_rerun_creates_no_duplicates`: run twice; destination count unchanged; second `MailStats.copied == 0`.
  - `test_interrupted_run_resumes`: `fail_next("insert", KeyboardInterrupt)` on the second message, catch it, run again; destination has exactly three.
  - `test_pending_row_already_on_destination_is_not_reinserted`: mark one pending by hand, insert that message into the destination directly, run; destination count for it is 1.
  - `test_threads_stay_grouped`: two source messages in one thread land in one destination thread.
  - `test_spam_trash_draft_chat_skipped`.
  - `test_permanent_error_recorded_and_run_continues`: `fail_next("insert", ApiError("http_400", retryable=False))`; `failed == 1`, others copied, `ledger.failures` has `http_400`.
  - `test_source_is_only_read`: every method name in `src.calls` is in `{"profile","list_labels","list_messages","get_raw"}`.
  - `test_message_without_message_id_copied_once` and `test_two_messages_sharing_message_id_both_copied` (Review Focus 3).
  - `test_label_differing_only_by_case_reuses_existing` (destination already has `donors`, source has `Donors`) (Review Focus 4).
  - `test_empty_mailbox` : all-zero stats, no error (Review Focus 5).
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement `mail.py`.
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: resumable mail copy`.

### Task 6: Calendar copy

**Files:**
- Create: `wsmigrate/calendar_copy.py`
- Test: `tests/test_calendar_copy.py`

**Interfaces:**
- Consumes: `Calendar`, `Ledger`, `Mapping`.
- Produces:

```python
EVENT_FIELDS = ("summary","description","location","start","end","recurrence","iCalUID",
                "attendees","organizer","reminders","transparency","visibility","status",
                "sequence","originalStartTime","eventType")
@dataclass
class CalStats: calendars: int; events_copied: int; events_updated: int; events_removed: int
                events_failed: int; acl_copied: int; outside_shares: int
def copy_calendars(src, dst, ledger, mapping, user_key, src_user, dst_user) -> CalStats
```

Rules:
- Copy only calendars with `accessRole == "owner"`. The primary calendar maps to destination id `"primary"`; others are created once and remembered with `set_cal`.
- Event body = the `EVENT_FIELDS` present on the source event, unchanged (addresses are not translated).
- Series first (events with no `recurringEventId`), then exceptions, whose `recurringEventId` is replaced with the destination id from the ledger.
- New source event → `import_event`. Ledger row exists and `updated` differs → `update_event`. Ledger row whose source event is gone → `delete_event` then `drop_event`.
- `ApiError` on an event → `set_event(..., state="failed", reason=...)`; if the event's `eventType` is not `default`, also add one to meta `skipped_event_types`.
- Sharing: skip the rule whose scope value is `src_user`; for `scope.type == "user"`, replace the value with `mapping.dest_for(value)` when mapped, otherwise keep it and add one to `outside_shares` (also stored in meta); other scope types unchanged. Skip rules already present on the destination.
- All writes: `sendUpdates="none"` / `sendNotifications=False`.

- [ ] **Step 1:** Write failing tests: `test_primary_and_secondary_calendars_copied`; `test_recurring_series_and_exception_linked` (exception's `recurringEventId` equals the destination series id); `test_all_day_event_copied`; `test_rerun_updates_changed_event_without_duplicating`; `test_deleted_source_event_removed_on_rerun`; `test_sharing_translated_through_mapping`; `test_unmapped_share_kept_and_counted`; `test_no_notifications_ever` (every recorded write in `dst.calls` has `sendUpdates == "none"` or `sendNotifications is False`); `test_subscribed_calendar_not_copied` (`accessRole == "reader"`); `test_failed_event_recorded_and_run_continues`; `test_source_is_only_read` (methods ⊆ `{"list_calendars","list_events","list_acl"}`); `test_user_with_only_empty_primary_calendar` (Review Focus 5).
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: calendar and sharing copy`.

### Task 7: Comparisons

**Files:**
- Create: `wsmigrate/compare.py`
- Test: `tests/test_compare.py`

**Interfaces:**
- Consumes: `Gmail`, `Calendar`, `Directory`, `Ledger`, `Mapping`, `SKIP_LABELS`, `msgid_hash`.
- Produces:

```python
@dataclass
class MailResult:
    src_total: int; dst_total: int      # excluding SKIP_LABELS
    missing: int                        # source hashes with fewer copies on the destination
    duplicates: int                     # hashes with more copies on the destination than the source
    extra: int                          # destination hashes absent from the source
    label_mismatches: int               # labels whose count differs by more than `preexisting`
    threads_src: int; threads_dst: int  # reported, never asserted
def compare_mail(src, dst, src_user, dst_user, preexisting: int) -> MailResult

@dataclass
class CalResult:
    calendars_src: int; calendars_dst: int
    events_src: int; events_dst: int    # excluding events the ledger marks failed with a non-default type
    series_src: int; series_dst: int
    attendees_src: int; attendees_dst: int
    acl_missing: int
def compare_calendar(src, dst, ledger, mapping, user_key, src_user, dst_user) -> CalResult

def load_inventory(path) -> list[tuple[str, str, str]]   # CSV header: type,address,parent ; type in user|group|member|alias
def compare_inventory(rows, directory, dest_domain) -> dict   # {"missing_users","missing_groups","missing_members","missing_aliases"}
```

Mail hashes on both sides come from `get_message_id_header` (hash the header value the same way `msgid_hash` does); messages with no header are compared by count only (a shortfall adds to `missing`). Comparison is by multiset (`collections.Counter`), not set. Inventory addresses are matched by local part against `dest_domain`.

- [ ] **Step 1:** Write failing tests: `test_identical_mailboxes_all_zero`; `test_missing_message_counted`; `test_duplicate_on_destination_counted`; `test_foreign_message_counted_as_extra`; `test_welcome_mail_within_preexisting_is_not_a_label_mismatch`; `test_headerless_messages_compared_by_count` and `test_shared_message_id_not_reported_as_duplicate` (Review Focus 3); `test_calendar_counts_match_after_copy` (run `copy_calendars` first); `test_missing_share_counted`; `test_inventory_missing_group_member_and_alias_counted`.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: source/destination comparisons`.

### Task 8: CLI — check, copy, status

**Files:**
- Create: `wsmigrate/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: everything above; `runtime.get_apis` (tests monkeypatch it to return fakes).
- Produces: `def main(argv: list[str] | None = None) -> int`. Exit codes: 0 success, 1 completed with failures, 2 refused to run.

Commands (all take `--config PATH`, default `$WSMIGRATE_CONFIG`):
- `check`: load mapping; for every user call both profiles. Emit `{"command":"check","ok":bool,"users":[{"user","src_messages","dst_existing","ok","reason"}]}`. A mapping error emits `{"command":"check","ok":false,"reason":..., "row":...}` and exits 2.
- `copy --batch NAME [--mail] [--calendar] [--allow-existing KEY ...]` (neither flag means both): take an exclusive non-blocking `fcntl.flock` on `<ledger_path>.lock`; if held, emit `reason: "already_running"` and exit 2. Refuse any user whose destination `messagesTotal` exceeds `max_existing` plus that user's ledger `done` count unless listed in `--allow-existing` (reason `destination_not_empty`, exit 2, nothing copied for anyone). Run users through `ThreadPoolExecutor(max_workers=config.parallel_users)`, each worker with its own `Ledger` and `get_apis`. Emit one summary with per-user stats and `seconds`.
- `status [--batch NAME]`: from the ledger only, per user: `copied`, `failed`, `pending`, `failures`.
- Any uncaught exception: emit `{"command":..., "ok":false, "reason": safeout.reason_for(exc)}`, exit 1. Never let a traceback reach stdout or stderr.

- [ ] **Step 1:** Write failing tests (call `main([...])`, capture stdout, `json.loads` each line):
  - `test_check_reports_sizes_per_user_key`.
  - `test_check_bad_mapping_exits_2_with_reason_and_row`.
  - `test_copy_batch_copies_only_that_batch`.
  - `test_copy_refuses_when_destination_not_empty` and `test_allow_existing_overrides_for_named_user`.
  - `test_second_copy_refused_while_first_holds_lock` (hold the flock in the test, then call `main`) → exit 2, `already_running`, destination untouched (Review Focus 2).
  - `test_status_reads_ledger_without_calling_google` (fakes' `.calls` empty).
  - `test_unexpected_error_emits_reason_only` (patch `copy_mail` to raise `RuntimeError("SECRET")`; `"SECRET"` not in stdout or stderr).
  - `test_output_never_contains_addresses` (no `@` anywhere in stdout for check, copy, status).
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run `pytest -v`. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: check, copy and status commands`.

### Task 9: verify, validation suite, HTML report, privacy end-to-end

**Files:**
- Create: `wsmigrate/validation/__init__.py`, `wsmigrate/validation/conftest.py`, `wsmigrate/validation/test_validation.py`
- Modify: `wsmigrate/cli.py` (add `verify`), `pyproject.toml` (include `wsmigrate/validation` as package data)
- Test: `tests/test_verify.py`, `tests/test_privacy.py`, `tests/test_no_destructive_calls.py`

**Interfaces:**
- Consumes: `compare_*`, `Ledger`, `Mapping`, `runtime.get_apis`.
- Produces: `wsmigrate verify --batch NAME [--expect-changes]`.

`verify` runs `pytest.main` in-process on `wsmigrate/validation` with `--batch`, `--config`, `--html=<report_dir>/<batch>-<YYYYMMDD-HHMMSS>.html`, `--self-contained-html`, `-p no:cacheprovider`, with stdout and stderr redirected to `os.devnull`, and a small plugin object that records each test's node name and outcome. It emits `{"command":"verify","batch","passed","failed_checks","checks":[{"name","user","ok"}],"report"}` and exits 0 only if nothing failed.

`conftest.py`: adds the `--batch`, `--config`, `--expect-changes` options; parametrizes a `user` fixture over the batch with `ids` = user keys; a session fixture caches one `MailResult` and one `CalResult` per user; `pytest_html_results_summary` adds a table of key → source address → destination address (the only place addresses appear).

`test_validation.py` — one assertion each, messages contain counts only:

| Test | Passes when |
|---|---|
| `test_mapping_valid` (once) | mapping loads |
| `test_inventory_present` (once) | all four `compare_inventory` counts are 0 |
| `test_mail_none_missing` | `missing == 0` |
| `test_mail_no_duplicates` | `duplicates == 0` |
| `test_mail_no_foreign_messages` | `extra <= preexisting` |
| `test_mail_label_counts` | `label_mismatches == 0` |
| `test_calendar_counts` | calendars, events, series and attendees each equal |
| `test_calendar_sharing` | `acl_missing == 0` |
| `test_nothing_left_failed` | `ledger.failures(user)` is empty, ignoring events with a non-default type |
| `test_freeze_held` | `history_start == history_end`; marked `xfail(strict=False)` when `--expect-changes` |

- [ ] **Step 1:** Write failing tests:
  - `tests/test_verify.py::test_verify_passes_after_clean_copy`: fakes seeded, `copy` then `verify`; exit 0, `failed_checks == 0`, report file exists.
  - `::test_verify_fails_and_names_check_when_message_missing`: delete one destination message from the fake; exit 1; a check named `test_mail_none_missing` for that user key has `ok == false`.
  - `::test_user_with_empty_mailbox_and_calendar_passes` (Review Focus 5).
  - `::test_expect_changes_does_not_fail_freeze_check`.
  - `tests/test_privacy.py::test_marker_never_leaks`: seed a message whose subject, body, sender name and sender address contain `ZEBRA7731`, an event titled and described with it, and an attendee named with it. Run `check`, `copy`, `status`, `verify`. Assert `ZEBRA7731` appears in none of: captured stdout, captured stderr, the bytes of the ledger file, the HTML report text.
  - `::test_report_shows_user_addresses_but_no_correspondents`: the mapped addresses are in the report; the seeded sender address is not.
  - `tests/test_no_destructive_calls.py::test_no_delete_trash_or_modify_in_package`: read every `.py` under `wsmigrate/`; the only match for `\.(delete|trash|untrash|modify|batchDelete|batchModify)\(` is the events delete inside `Calendar.delete_event`.
  - `::test_no_print_or_logging_in_package`: no `print(` and no `import logging` under `wsmigrate/`.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement `verify`, `conftest.py`, `test_validation.py`.
- [ ] **Step 4:** Run `pytest -v`. Expected: all PASS. Open one generated report in a browser and confirm it shows the key/address table and a pass/fail row per user per check.
- [ ] **Step 5:** Commit: `feat: verify command, validation suite and html report`.

### Task 10: Operator documents

**Files:**
- Create: `docs/operator-setup.md`, `docs/rehearsal-checklist.md`
- Modify: `README.md` (replace "Planned commands" with real usage; link both documents)

No tests; a person follows these.

- [ ] **Step 1:** Write `docs/operator-setup.md`:
  - creating the two service accounts and the exact scopes to delegate (copy the list from Global Constraints), as steps for the administrators of each account;
  - creating a separate macOS user `wsmigrate`, installing the tool in its home, placing keys, config, mapping, inventory, ledger and reports there with mode 700;
  - the single sudoers line letting the operator's user run only `/Users/wsmigrate/venv/bin/wsmigrate` as `wsmigrate`;
  - the agent's standing instructions: run only `sudo -u wsmigrate /Users/wsmigrate/venv/bin/wsmigrate ...`; never read files under `/Users/wsmigrate`; stop and ask on any exit code 2 or any failed check;
  - the people-only steps from the spec;
  - teardown: revoke delegation, delete keys, delete the `wsmigrate` user.
  - State plainly that these are changes to the Mac's security settings and are made by Jonathon, not by an agent.
- [ ] **Step 2:** Write `docs/rehearsal-checklist.md` with one checkbox per item the spec marks unconfirmed: subdomain accepted for a trial account; `calendar.acls.readonly` reads sharing (else fall back to full `calendar` on the source and record it); copy works on a locked-out account; phones and mail apps are cut off by the lockout; a suspended account blocks the tool; Meet links survive; room bookings survive; guest (non-organiser) events; out-of-office, focus-time and working-location events; exceptions to recurring events stay linked; threads stay grouped; measured messages per second per mailbox and with `parallel_users` at 4; message over 25 MB inserts; queue hold and release on the test subdomain; meetings after the practice domain move; vendor answers on test-domain pricing and re-use. Then the mock steps: `check` for all users, lock volunteers, `copy`, `verify --expect-changes`, record duration.
- [ ] **Step 3:** Update `README.md`.
- [ ] **Step 4:** Commit: `docs: operator setup and rehearsal checklist`.

---

## Self-review notes

- Spec coverage: every Goals, Anti-goals, Privacy-rules and Test-suite line in `docs/design.md` maps to a task above. The "fallback" of copying older mail first is deliberately not built; the spec makes it a decision for after the rehearsal.
- Deviation from the spec, already written back into it: users are tracked by the mapping file's key column, not Google's permanent ID.
- Not testable here: the real `Gmail`/`Calendar`/`Directory` wrappers. Their first test is the rehearsal.
