"""Sign-off checks. Each docstring is the report's condition; expected and actual are recorded before the
assertion. Everything recorded or asserted is a count or plain value: never addresses, subjects or content."""
import pytest

from movingday.compare import compare_inventory, load_inventory
from movingday.mapping import load_mapping


# expected values that do not depend on data, shown even when a check cannot run
EXPECTED = {
    "test_mapping_valid": "at least 1 user",
    "test_inventory_present": "0 missing users\n0 missing groups\n0 missing members\n0 missing aliases",
    "test_mail_none_missing": "0 missing",
    "test_mail_no_duplicates": "0 duplicates",
    "test_mail_label_counts": "0 labels with different counts",
    "test_calendar_sharing": "0 missing shares",
    "test_calendar_events_missing": "0 missing events",
    "test_contacts_count": "as many contacts as the source",
    "test_contacts_missing": "0 missing contacts",
    "test_contact_labels": "as many labels as the source\n0 labels short of members",
    "test_drive_files_missing": "0 missing files",
    "test_drive_checksums": "0 checksum mismatches",
    "test_drive_sharing": "0 missing shares\n0 extra shares",
    "test_drive_in_step": "0 changed since copied",
    "test_nothing_left_failed": "0 failed",
    "test_carried_over_notes": "for information",
}


def _show(record_property, expected, actual):
    record_property("expected", expected)
    record_property("actual", actual)


def test_mapping_valid(cfg, record_property):
    """The user mapping file loads and lists at least one user."""
    n = len(load_mapping(cfg.mapping_file).users)
    _show(record_property, EXPECTED["test_mapping_valid"], f"{n} users")
    assert n > 0, f"users={n}"


def test_inventory_present(cfg, apis, record_property):
    """Every user, group, group member and alias in the inventory exists on the destination."""
    r = compare_inventory(load_inventory(cfg.inventory_file), apis.dst_dir, cfg.dest_domain)
    names = [k.replace("_", " ") for k in r]
    _show(record_property, EXPECTED["test_inventory_present"], "\n".join(f"{v} {k}" for k, v in zip(names, r.values())))
    assert not any(r.values()), " ".join(f"{k}={v}" for k, v in r.items())


def test_mail_none_missing(mail, record_property):
    """Every source message is present on the destination."""
    _show(record_property, EXPECTED["test_mail_none_missing"], f"{mail.missing} missing")
    assert mail.missing == 0, f"missing={mail.missing}"


def test_mail_no_duplicates(mail, record_property):
    """No source message was copied more than once."""
    _show(record_property, EXPECTED["test_mail_no_duplicates"], f"{mail.duplicates} duplicates")
    assert mail.duplicates == 0, f"duplicates={mail.duplicates}"


def test_mail_no_foreign_messages(mail, ledger, user, record_property):
    """The destination holds no messages other than the copies and the mail it already had before the copy."""
    pre = int(ledger.get_meta(user.key, "preexisting") or 0) - ledger.adopted_count(user.key)
    _show(record_property, f"at most {pre} extra (already there before the copy)", f"{mail.extra} extra")
    assert mail.extra <= pre, f"extra={mail.extra} preexisting={pre}"


def test_mail_label_counts(mail, record_property):
    """Every label holds as many messages on the destination as on the source, allowing for mail already there."""
    _show(record_property, EXPECTED["test_mail_label_counts"], f"{mail.label_mismatches} labels with different counts")
    assert mail.label_mismatches == 0, f"label_mismatches={mail.label_mismatches}"


def test_calendar_counts(cal, record_property):
    """The destination has as many calendars, events, recurring series and attendees as the source."""
    pairs = {"calendars": (cal.calendars_src, cal.calendars_dst), "events": (cal.events_src, cal.events_dst),
             "recurring series": (cal.series_src, cal.series_dst),
             "attendees": (cal.attendees_src, cal.attendees_dst)}
    _show(record_property, "\n".join(f"{k}: {s} (same as source)" for k, (s, _) in pairs.items()),
          "\n".join(f"{k}: {d}" for k, (_, d) in pairs.items()))
    src, dst = zip(*pairs.values())
    assert src == dst, f"calendars/events/series/attendees source={src} destination={dst}"


def test_calendar_sharing(cal, record_property):
    """Every calendar share on the source exists on the destination."""
    _show(record_property, EXPECTED["test_calendar_sharing"], f"{cal.acl_missing} missing shares")
    assert cal.acl_missing == 0, f"acl_missing={cal.acl_missing}"


def test_calendar_events_missing(cal, record_property):
    """Every source event has been copied and is present on the destination."""
    _show(record_property, EXPECTED["test_calendar_events_missing"], f"{cal.events_missing} missing events")
    assert cal.events_missing == 0, f"events_missing={cal.events_missing}"


def test_contacts_count(con, record_property):
    """The destination has as many contacts as the source."""
    _show(record_property, f"contacts: {con.contacts_src} (same as source)", f"contacts: {con.contacts_dst}")
    assert con.contacts_src == con.contacts_dst, f"contacts source={con.contacts_src} destination={con.contacts_dst}"


def test_contacts_missing(con, record_property):
    """Every source contact has been copied and is present on the destination."""
    _show(record_property, EXPECTED["test_contacts_missing"], f"{con.contacts_missing} missing contacts")
    assert con.contacts_missing == 0, f"contacts_missing={con.contacts_missing}"


def test_contact_labels(con, record_property):
    """Every contact label on the source exists on the destination with at least as many contacts."""
    _show(record_property, f"labels: {con.labels_src} (same as source)\n0 labels short of members",
          f"labels: {con.labels_dst}\n{con.label_member_mismatches} labels short of members")
    assert con.labels_src == con.labels_dst and con.label_member_mismatches == 0, (
        f"labels source={con.labels_src} destination={con.labels_dst} short={con.label_member_mismatches}")


def test_drive_files_count(drv, record_property):
    """The destination has as many files as the source owns (unsupported types excepted)."""
    _show(record_property, f"files: {drv.files_src} (same as source)", f"files: {drv.files_dst}")
    assert drv.files_src == drv.files_dst, f"files source={drv.files_src} destination={drv.files_dst}"


def test_drive_files_missing(drv, record_property):
    """Every source file has been copied and is present on the destination."""
    _show(record_property, EXPECTED["test_drive_files_missing"], f"{drv.files_missing} missing files")
    assert drv.files_missing == 0, f"files_missing={drv.files_missing}"


def test_drive_folders(drv, record_property):
    """The destination has as many folders as the source owns."""
    _show(record_property, f"folders: {drv.folders_src} (same as source)",
          f"folders: {drv.folders_dst}\nfiles whose folder is not copied: {drv.folder_not_copied} (for information)")
    assert drv.folders_src == drv.folders_dst, f"folders source={drv.folders_src} destination={drv.folders_dst}"


def test_drive_checksums(drv, record_property):
    """Every uploaded (non-Google) file is byte for byte identical on the destination (checksum)."""
    _show(record_property, EXPECTED["test_drive_checksums"], f"{drv.checksum_mismatches} checksum mismatches")
    assert drv.checksum_mismatches == 0, f"checksum_mismatches={drv.checksum_mismatches}"


def test_drive_sharing(drv, record_property):
    """Every copied file and folder is shared on the destination exactly as on the source (translated)."""
    _show(record_property, EXPECTED["test_drive_sharing"],
          f"{drv.acl_missing} missing shares\n{drv.acl_extra} extra shares")
    assert drv.acl_missing == 0 and drv.acl_extra == 0, f"acl_missing={drv.acl_missing} acl_extra={drv.acl_extra}"


def test_drive_in_step(drv, request, record_property):
    """No source file or folder was changed, moved, re-shared, added or deleted since it was last copied."""
    if request.config.getoption("--expect-changes"):
        request.applymarker(pytest.mark.xfail(strict=False, reason="changes expected"))
    _show(record_property, EXPECTED["test_drive_in_step"], f"{drv.out_of_step} changed since copied")
    assert drv.out_of_step == 0, f"out_of_step={drv.out_of_step}"


def test_nothing_left_failed(ledger, user, record_property):
    """No message, event, contact or Drive item is left in a failed state (unsupported event types excepted)."""
    # unsupported event types are expected and retried every run (R5)
    left = {k: v for k, v in sorted(ledger.failures(user.key).items()) if k != "unsupported_event_type"}
    n = sum(left.values())
    by_reason = f" ({', '.join(f'{k}: {v}' for k, v in left.items())})" if left else ""
    _show(record_property, EXPECTED["test_nothing_left_failed"], f"{n} failed{by_reason}")
    assert n == 0, f"failed={n}"


def test_freeze_held(ledger, user, request, record_property):
    """The source mailbox did not change while it was being copied."""
    start, end = ledger.get_meta(user.key, "history_start"), ledger.get_meta(user.key, "history_end")
    if start is None:  # fails even with --expect-changes
        _show(record_property, "a recorded copy", "no copy recorded")
        pytest.fail("no copy recorded", pytrace=False)
    if request.config.getoption("--expect-changes"):
        request.applymarker(pytest.mark.xfail(strict=False, reason="changes expected"))
    _show(record_property, f"history id at end: {start} (same as at start)", f"history id at end: {end}")
    assert start == end, "source mailbox changed during the copy"


def test_carried_over_notes(mail, ledger, user, record_property):
    """For information (always passes): threads on each side (a difference means split threads), messages with
    no Date header, calendar entries of unsupported types not copied, shares outside our domain, shares that
    failed, calendars shared with the user that were skipped, other contacts (not copied), contacts removed
    on the source since an earlier run, and Drive: unsupported files and forms not copied, files kept as Office
    files, unplaced files, outside and failed shares, copies moved to the destination trash, and whether
    trashing was held because too many copies were gone."""
    def meta(name):
        return ledger.get_meta(user.key, name) or "not recorded"

    notes = [f"threads: {mail.threads_src} on source, {mail.threads_dst} on destination",
             f"messages with no Date header: {meta('no_date_header')}",
             "calendar entries of unsupported types not copied: "
             f"{ledger.failures(user.key).get('unsupported_event_type', 0)}",
             f"outside shares: {meta('outside_shares')}", f"failed shares: {meta('acl_failed')}",
             f"shared calendars skipped: {meta('shared_calendars_skipped')}",
             f"other contacts not copied: {meta('other_contacts')}",
             f"contacts removed on source: {meta('contacts_removed_on_source')}",
             f"Drive unsupported files not copied: {meta('drive_unsupported')}",
             f"Drive forms not copied: {meta('drive_forms')}",
             f"Drive files kept as Office files: {meta('drive_kept_as_office')}",
             f"Drive files unplaced: {meta('drive_unplaced')}",
             f"Drive outside shares: {meta('drive_outside_shares')}",
             f"Drive failed shares: {meta('drive_shares_failed')}",
             f"Drive files moved to destination trash: {meta('drive_trashed')}",
             f"Drive trashing held: {meta('drive_trash_held')}",
             "Comments, suggestions and version history are not carried over."]
    _show(record_property, EXPECTED["test_carried_over_notes"], "\n".join(notes))
