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
  decision (and change `wsmigrate/runtime.py` to match).
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
- [ ] Domain-wide and group calendar sharing: the translated rules (our
  domain and our groups moved to the destination domain) are accepted.
  Recheck them after the practice domain move.
- [ ] Calendar `403 quotaExceeded` is not retried by the tool. Note it here
  if seen.

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
