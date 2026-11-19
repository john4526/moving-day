# Rehearsal checklist

Tick each item when it has been tested on the trial accounts and spare
subdomains, and write the result next to it. These are the things the design
does not yet know. Do not start the real copy until each is answered. Where
an item says "else", the fallback is named.

## A. Google accounts and access

- [ ] A subdomain (for example `mig-a.` or `mig-b.`) is accepted as the
  address of a trial account.
- [ ] `calendar.acls.readonly` is accepted for reading sharing settings.
  Else fall back to the full `calendar` scope on the source, and record the
  decision (and change `movingday/runtime.py` to match).
- [ ] The destination calendar id `primary` works for every call.

## B. Lockout and freeze

- [ ] Copy works on a locked-out account (password reset to a random value,
  all sessions signed out).
- [ ] Phones and mail apps are cut off by the lockout.
- [ ] A suspended account blocks the tool (so suspension must not be used).
- [ ] The source history id stays the same on an idle locked-out account
  over several hours (otherwise the freeze check fails with no real change).
- [ ] Mail sent by parent-organisation staff to our users during the freeze
  does not pass through the queue. Confirm whether it arrives in the old
  account, and so would be missed by the copy.

## C. Mail

- [ ] Threads stay grouped on the destination.
- [ ] A message over 25 MB inserts.
- [ ] Creating a label that already exists returns the existing one (the
  409 path).
- [ ] Message-ID header lookup works whatever the header's letter case
  (`metadataHeaders`).
- [ ] `list_labels` marks system labels with type `system`.
- [ ] A brand-new destination mailbox's message count: what does it include
  (welcome mail, spam, trash)? Confirm `max_existing = 5` is enough slack.
- [ ] Measured messages per second per mailbox: ______
- [ ] Measured messages per second per mailbox with `parallel_users = 4`:
  ______
- [ ] Insert accepts the system labels `SENT`, `IMPORTANT` and `CATEGORY_*`.
- [ ] Fetching chat messages in raw format works.
- [ ] Whether Gmail adds its own Message-ID to an inserted message that has
  none.

## D. Calendar

- [ ] Copying events with the import call sends no invitations or emails to
  attendees. (The import API has no `sendUpdates` setting; the tool relies on
  import being silent.)
- [ ] Meet links survive.
- [ ] Room bookings survive.
- [ ] Guest (non-organiser) events copy correctly.
- [ ] Out-of-office, focus-time and working-location events: record what
  happens (they are reported, not treated as failures).
- [ ] Exceptions to recurring events stay linked to their series.
- [ ] Cancelled single occurrences of a recurring meeting stay cancelled on
  the destination. If import rejects them for a missing iCalUID, start or
  end, the fix is to give them the series' iCalUID.
- [ ] Hidden calendars a user owns are copied.
- [ ] Meetings after the practice domain move: what happens when the
  organiser's address changes?
- [ ] Delegated and co-managed calendars: an assistant's view of the
  director's calendar is skipped (not copied into the assistant's account),
  and a team calendar managed by several people is copied once.
  A user's own calendar shared with "manage sharing" goes to whichever
  co-manager is copied first. Check the scan's `team_calendars` count and
  decide the batch order for those people.
- [ ] Domain-wide and group calendar sharing: the translated rules (our
  domain and our groups moved to the destination domain) are accepted.
  Recheck them after the practice domain move.
- [ ] Calendar `403 quotaExceeded` is not retried by the tool. Note it here
  if seen.

## D2. Contacts (People API)

- [ ] Bulk create limit: how many contacts per minute and per day does
  `createContact` allow per user, and does the tool's retry cope? Note the
  speed seen.
- [ ] The system group `starred` exists on a destination account and a
  contact created with that membership is starred.
- [ ] A stale etag on `updateContact` returns 400 (FAILED_PRECONDITION). The
  copy retries once on any `http_400` after fetching a fresh etag.
- [ ] `updateContact` is accepted with `metadata.sources` (the CONTACT entry
  and etag from `people.get`) and `updatePersonFields` listing every copied
  field, including `memberships`.
- [ ] Label membership set through `memberships` on create and update keeps
  user labels and `starred` (no `contactGroups.members.modify` is used).
- [ ] Output-only sub-fields are not needed on write (names.displayName,
  phoneNumbers.canonicalForm, formattedType are stripped).
- [ ] `otherContacts.list` accepts `readMask=metadata`, and the
  `contacts.other.readonly` scope is accepted for the source.
- [ ] Creating a contact group whose name exists returns 409, and the tool
  then reuses the existing group (letter case ignored).
- [ ] `connections.list` with `sources=READ_SOURCE_TYPE_CONTACT` returns no
  profile-only fields (a contact shows only its own saved values).
- [ ] Photos are not copied: a contact with a photo copies without it and
  without error.
- [ ] `contactGroups.list` returns `memberCount` for user labels (the
  contact-label check depends on it).
- [ ] Contacts count on the destination equals the source after `copy`; the
  report's contact checks pass.

## D3. Drive

- [ ] `drive.file` on the destination is enough to read back sharing on the
  files the tool created (`verify` sharing check). Else switch to `drive`,
  record the decision, and change `movingday/runtime.py` to match.
- [ ] Created and modified times are kept when set on upload (and modified
  time on a metadata update).
- [ ] The export-link fallback works for a Google file whose export is above
  10 MB.
- [ ] Export links honour Range requests (else a large export is held whole
  in memory).
- [ ] A refused conversion on upload is detectable (400, or 403 not for rate
  limiting) and the unconverted retry works; the file counts as kept as an
  Office file.
- [ ] A rate-limit 403 during upload is retried, not taken as a refused
  conversion.
- [ ] A resumable upload's 308 `Range` header always shows the whole chunk
  persisted (no short persists); the uploader assumes full chunks.
- [ ] `update_content` on a converted Google file re-converts it in place
  (same file, same address).
- [ ] Measured speed in MB per second per user: ______
- [ ] Behaviour at Google's 750 GB per user per day upload limit (the error
  seen, and that a re-run the next day carries on).
- [ ] Sharing to a non-Google address without a notification is refused, and
  is counted as a failed share.
- [ ] A copy moved to the destination trash is restorable.
- [ ] What Drive does with another owner's copy inside a folder its owner
  trashes (the tool moves its own copies out first).
- [ ] Under `drive.file`, user A's token can create a file in, and move a
  file into and out of, a folder the tool created as user B; and it can
  `files.get("root")` and create with `parents=[root]`.
- [ ] `permissions.list` on a My Drive item also returns the permissions it
  inherits from its folder. Confirm the tool's explicit copy of each on
  every child is acceptable, and whether removing an inherited-only
  permission gives 403.
- [ ] A file's `version` does not change between runs when nobody touched
  it (else every top-up re-copies it and "Drive in step" stays red).
- [ ] Time of a no-change Drive top-up and of `verify` on the largest Drive:
  ______ (this sets the floor for the Drive part of the mail outage).
- [ ] Drive accepts `createdTime` on create. Else drop it from the create
  metadata in `movingday/drive_copy.py`.
- [ ] While two mapped users both have a copy of the same folder (ownership
  moved, the old copy not yet trashed), which copy `dst_folder` returns for
  its children, and whether the next run corrects their placement.

## E. Directory

- [ ] Aliases that belong to groups (not users): the tool only checks user
  aliases, so a group alias in the inventory will show as missing. Check
  those by hand.

## F. Mail queuing vendor

- [ ] Queue hold and release works on the test subdomain.
- [ ] Vendor answer: does one purchase cover a test domain or a trial?
- [ ] Vendor answer: can the queue be used twice?

## G. Mock on real volunteers

Use the commands from `docs/operator-setup.md`, section 7.

- [ ] 1. `check` for all users (no `--batch`). Note the mailbox sizes and
  that every user shows `ok: true`.
- [ ] 2. Lock out the volunteers (a person does this, in the source
  account). Include the user with the largest mailbox.
- [ ] 3. `copy --batch NAME`. Record the `seconds` value printed.
- [ ] 4. `verify --batch NAME --expect-changes`. Note the report file name.
  Time it on the largest mailbox: in the real cutover `verify` runs inside
  the mail outage. Time taken: ______
- [ ] 5. Open the HTML report. Confirm every row shows Condition, Expected
  and Actual, and that no correspondent address, subject or event title
  appears anywhere in it.
- [ ] 6. Record the total duration and work out whether the real copy fits
  the window. If not, the design's fallback is a decision for Jonathon.
- [ ] 7. Decide what happens to the volunteers' trial copy (keep and top up,
  or delete and recreate the destination users).
- [ ] 8. Record how much of the operator's Claude usage the mock took
  (see "Models and token limits" in `docs/operator-setup.md`). Scale it to
  the real cutover and confirm it fits the operator's plan: ______
- [ ] 9. The operator runs one `status` and one `verify` by hand from the
  fallback card, with no agent.
