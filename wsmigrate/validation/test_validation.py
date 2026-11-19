"""Sign-off checks. Each docstring is the report's condition; expected and actual are recorded before the
assertion. Everything recorded or asserted is a count or plain value: never addresses, subjects or content."""
import pytest

from wsmigrate.compare import compare_inventory, load_inventory
from wsmigrate.mapping import load_mapping


# expected values that do not depend on data, shown even when a check cannot run
EXPECTED = {
    "test_mapping_valid": "at least 1 user",
    "test_inventory_present": "0 missing users\n0 missing groups\n0 missing members\n0 missing aliases",
    "test_mail_none_missing": "0 missing",
    "test_mail_no_duplicates": "0 duplicates",
    "test_mail_label_counts": "0 labels with different counts",
    "test_calendar_sharing": "0 missing shares",
    "test_calendar_events_missing": "0 missing events",
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
    pre = int(ledger.get_meta(user.key, "preexisting") or 0)
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


def test_nothing_left_failed(ledger, user, record_property):
    """No message or event is left in a failed state (unsupported event types excepted)."""
    # unsupported event types are expected and retried every run (R5)
    n = sum(v for k, v in ledger.failures(user.key).items() if k != "unsupported_event_type")
    _show(record_property, EXPECTED["test_nothing_left_failed"], f"{n} failed")
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
    failed, and calendars shared with the user that were skipped."""
    def meta(name):
        return ledger.get_meta(user.key, name) or "not recorded"

    notes = [f"threads: {mail.threads_src} on source, {mail.threads_dst} on destination",
             f"messages with no Date header: {meta('no_date_header')}",
             "calendar entries of unsupported types not copied: "
             f"{ledger.failures(user.key).get('unsupported_event_type', 0)}",
             f"outside shares: {meta('outside_shares')}", f"failed shares: {meta('acl_failed')}",
             f"shared calendars skipped: {meta('shared_calendars_skipped')}"]
    _show(record_property, EXPECTED["test_carried_over_notes"], "\n".join(notes))
