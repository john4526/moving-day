"""Sign-off checks. Assertion messages carry counts only: never addresses, subjects or content."""
import pytest

from wsmigrate.compare import compare_inventory, load_inventory
from wsmigrate.mapping import load_mapping


def test_mapping_valid(cfg):
    assert load_mapping(cfg.mapping_file).users, "users=0"


def test_inventory_present(cfg, apis):
    r = compare_inventory(load_inventory(cfg.inventory_file), apis.dst_dir, cfg.dest_domain)
    assert not any(r.values()), " ".join(f"{k}={v}" for k, v in r.items())


def test_mail_none_missing(mail):
    assert mail.missing == 0, f"missing={mail.missing}"


def test_mail_no_duplicates(mail):
    assert mail.duplicates == 0, f"duplicates={mail.duplicates}"


def test_mail_no_foreign_messages(mail, ledger, user):
    pre = int(ledger.get_meta(user.key, "preexisting") or 0)
    assert mail.extra <= pre, f"extra={mail.extra} preexisting={pre}"


def test_mail_label_counts(mail):
    assert mail.label_mismatches == 0, f"label_mismatches={mail.label_mismatches}"


def test_calendar_counts(cal):
    src = (cal.calendars_src, cal.events_src, cal.series_src, cal.attendees_src)
    dst = (cal.calendars_dst, cal.events_dst, cal.series_dst, cal.attendees_dst)
    assert src == dst, f"calendars/events/series/attendees source={src} destination={dst}"


def test_calendar_sharing(cal):
    assert cal.acl_missing == 0, f"acl_missing={cal.acl_missing}"


def test_calendar_events_missing(cal):
    assert cal.events_missing == 0, f"events_missing={cal.events_missing}"


def test_nothing_left_failed(ledger, user):
    # unsupported event types are expected and retried every run (R5)
    n = sum(v for k, v in ledger.failures(user.key).items() if k != "unsupported_event_type")
    assert n == 0, f"failed={n}"


def test_freeze_held(ledger, user, request):
    if request.config.getoption("--expect-changes"):
        request.applymarker(pytest.mark.xfail(strict=False, reason="changes expected"))
    start, end = ledger.get_meta(user.key, "history_start"), ledger.get_meta(user.key, "history_end")
    assert start == end, "source mailbox changed during the copy"
