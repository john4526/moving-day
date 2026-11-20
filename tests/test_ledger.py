import threading

import pytest

from movingday.ledger import Ledger

_open = []


def make(tmp_path):
    led = Ledger(str(tmp_path / "ledger.db"))
    _open.append(led)
    return led


@pytest.fixture(autouse=True)
def _close_ledgers():
    yield
    while _open:
        _open.pop().close()


def test_pending_then_done_roundtrip(tmp_path):
    led = make(tmp_path)
    led.mark_pending("u001", "m1", "t1", "h1")
    assert led.pending("u001") == [("m1", "h1")]
    assert led.done_ids("u001") == set()
    led.mark_done("u001", "m1", "d1", "dt1")
    assert led.pending("u001") == []
    assert led.done_ids("u001") == {"m1"}
    assert led.done_hash_count("u001", "h1") == 1
    assert led.counts("u001") == {"done": 1, "failed": 0, "pending": 0}
    led.mark_pending("u001", "m1", "t1", "h1")  # never downgrades done
    assert led.done_ids("u001") == {"m1"}
    led.mark_pending("u001", "m2", "t2", "h2")
    led.drop_pending("u001", "m2")
    led.drop_pending("u001", "m1")  # only drops pending rows
    assert led.pending("u001") == [] and led.done_ids("u001") == {"m1"}


def test_failed_row_is_retried(tmp_path):
    led = make(tmp_path)
    led.mark_pending("u001", "m1", "t1", "h1")
    led.mark_failed("u001", "m1", "http_500")
    assert "m1" not in led.done_ids("u001")
    assert led.failures("u001") == {"http_500": 1}
    assert led.counts("u001")["failed"] == 1
    led.mark_pending("u001", "m1", "t1", "h1")  # retry resets
    assert led.counts("u001") == {"done": 0, "failed": 0, "pending": 1}
    assert led.failures("u001") == {}


def test_empty_destination_ids_are_valid(tmp_path):
    led = make(tmp_path)
    led.mark_pending("u001", "m1", "t1", "h1")
    led.mark_done("u001", "m1", "", "")
    assert led.done_ids("u001") == {"m1"}
    assert led.dst_thread("u001", "t1") is None


def test_dst_thread_lookup(tmp_path):
    led = make(tmp_path)
    led.mark_pending("u001", "m1", "t1", "h1")
    assert led.dst_thread("u001", "t1") is None
    led.mark_done("u001", "m1", "d1", "dt1")
    assert led.dst_thread("u001", "t1") == "dt1"
    assert led.dst_thread("u001", "t9") is None


def test_reopen_keeps_state(tmp_path):
    led = make(tmp_path)
    led.mark_pending("u001", "m1", "t1", "h1")
    led.mark_done("u001", "m1", "d1", "dt1")
    led.set_cal("u001", "c1", "dc1")
    led.set_meta("u001", "preexisting", "5")
    led.close()
    _open.remove(led)
    led = make(tmp_path)
    assert led.done_ids("u001") == {"m1"}
    assert led.cal_map("u001") == {"c1": "dc1"}
    assert led.get_meta("u001", "preexisting") == "5"


def test_users_are_isolated(tmp_path):
    led = make(tmp_path)
    led.mark_pending("u001", "m1", "t1", "h1")
    led.mark_done("u001", "m1", "d1", "dt1")
    led.set_cal("u001", "c1", "dc1")
    led.set_meta("u001", "x", "1")
    led.set_event("u001", "c1", "e1", "de1", "u", "done")
    assert led.done_ids("u002") == set()
    assert led.done_hash_count("u002", "h1") == 0
    assert led.dst_thread("u002", "t1") is None
    assert led.cal_map("u002") == {}
    assert led.get_meta("u002", "x") is None
    assert led.event_rows("u002", "c1") == {}
    assert led.counts("u002") == {"done": 0, "failed": 0, "pending": 0}


def test_event_rows_roundtrip(tmp_path):
    led = make(tmp_path)
    led.set_event("u001", "c1", "e1", "de1", "2026-01-01", "done")
    led.set_event("u001", "c1", "e1", "de1", "2026-02-01", "done")  # replace
    led.set_event("u001", "c1", "e2", "", "", "failed", reason="unsupported_event_type")
    led.set_event("u001", "c2", "e3", "", "", "failed", reason="http_500")
    assert led.event_rows("u001", "c1") == {
        "e1": ("de1", "2026-02-01", "done"),
        "e2": ("", "", "failed"),
    }
    led.mark_pending("u001", "m1", "t1", "h1")
    led.mark_failed("u001", "m1", "http_500")
    assert led.failures("u001") == {"unsupported_event_type": 1, "http_500": 2}
    led.drop_event("u001", "c1", "e1")
    assert set(led.event_rows("u001", "c1")) == {"e2"}


def test_meta_roundtrip(tmp_path):
    led = make(tmp_path)
    assert led.get_meta("u001", "history_start") is None
    led.set_meta("u001", "history_start", "100")
    led.set_meta("u001", "history_start", "200")
    assert led.get_meta("u001", "history_start") == "200"


def test_concurrent_writers_share_file(tmp_path):
    def work(n):
        led = Ledger(str(tmp_path / "ledger.db"))
        for i in range(25):
            led.mark_pending("u001", f"m{n}-{i}", "t", "h")
            led.mark_done("u001", f"m{n}-{i}", "d", "dt")
        won.append(led.claim_calendar("g", f"u00{n}"))
        led.close()
    won = []
    ts = [threading.Thread(target=work, args=(n,)) for n in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(make(tmp_path).done_ids("u001")) == 100
    assert sorted(won) == [False, False, False, True]  # exactly one claimant


def test_claim_calendar_first_claimant_wins(tmp_path):
    led = make(tmp_path)
    assert led.claim_calendar("g", "u001") is True
    assert led.claim_calendar("g", "u002") is False
    assert led.claim_calendar("g", "u001") is True


def test_mark_failed_does_not_downgrade_done(tmp_path):
    led = make(tmp_path)
    led.mark_pending("u001", "m1", "t1", "h1")
    led.mark_done("u001", "m1", "d1", "dt1")
    led.mark_failed("u001", "m1", "http_500")
    assert "m1" in led.done_ids("u001")
    assert led.failures("u001") == {}


def test_mark_done_without_pending_row(tmp_path):
    led = make(tmp_path)
    led.mark_done("u001", "m1", "d1", "dt1")
    assert led.done_ids("u001") == {"m1"}
    assert led.counts("u001") == {"done": 1, "failed": 0, "pending": 0}


def test_failed_event_ids_filters_by_reason(tmp_path):
    led = make(tmp_path)
    led.set_event("u001", "primary", "e1", "", "t", "failed", "unsupported_event_type")
    led.set_event("u001", "primary", "e2", "", "t", "failed", "http_500")
    led.set_event("u001", "other", "e3", "", "t", "failed", "unsupported_event_type")
    assert led.failed_event_ids("u001", "primary", "unsupported_event_type") == {"e1"}


def test_contact_and_group_rows_roundtrip(tmp_path):
    led = make(tmp_path)
    led.set_contact("u001", "people/c1", "", "e1", "pending")
    assert led.contact_rows("u001") == {"people/c1": ("", "e1", "pending")}
    led.set_contact("u001", "people/c1", "people/d1", "e1", "done")
    led.set_contact("u001", "people/c2", "", "e2", "failed", "http_500")
    assert led.contact_rows("u001") == {"people/c1": ("people/d1", "e1", "done"),
                                        "people/c2": ("", "e2", "failed")}
    assert led.contact_rows("u002") == {}
    led.set_group("u001", "contactGroups/a", "contactGroups/b")
    assert led.group_map("u001") == {"contactGroups/a": "contactGroups/b"}
    assert led.group_map("u002") == {}


def test_failures_include_failed_contacts_not_gone(tmp_path):
    led = make(tmp_path)
    led.set_contact("u001", "people/c1", "", "e1", "failed", "http_400")
    led.set_contact("u001", "people/c2", "", "e2", "gone")
    led.set_contact("u001", "people/c3", "people/d3", "e3", "done")
    led.set_contact("u002", "people/c4", "", "e4", "failed", "http_500")
    assert led.failures("u001") == {"http_400": 1}


def test_drive_rows_roundtrip(tmp_path):
    led = make(tmp_path)
    led.set_drive("u001", "s1", dst_id="d1", kind="folder", src_version="3", src_modified="t", md5="m",
                  src_parent="p", placed=0, share_fp="fp", state="done", reason=None)
    row = led.drive_rows("u001")["s1"]
    assert row == {"user_key": "u001", "src_id": "s1", "dst_id": "d1", "kind": "folder", "src_version": "3",
                   "src_modified": "t", "md5": "m", "src_parent": "p", "placed": 0, "share_fp": "fp",
                   "state": "done", "reason": None}
    assert led.drive_rows("u002") == {}


def test_set_drive_partial_update_keeps_columns(tmp_path):
    led = make(tmp_path)
    led.set_drive("u001", "s1", kind="file", md5="m", state="pending")
    led.set_drive("u001", "s1", state="failed", reason="http_500")
    row = led.drive_rows("u001")["s1"]
    assert (row["kind"], row["md5"], row["state"], row["reason"], row["placed"]) == ("file", "m", "failed",
                                                                                    "http_500", 1)
    with pytest.raises(ValueError):
        led.set_drive("u001", "s1", name="x")


def test_dst_folder_across_users(tmp_path):
    led = make(tmp_path)
    led.set_drive("u001", "F", dst_id="D", kind="folder", state="pending")
    assert led.dst_folder("F") is None
    led.set_drive("u001", "F", state="done")
    led.set_drive("u002", "G", dst_id="E", kind="file", state="done")
    assert led.dst_folder("F") == "D" and led.dst_folder("G") is None


def test_drive_shares_add_drop(tmp_path):
    led = make(tmp_path)
    led.add_drive_share("u001", "s1", "sigA", "p1")
    led.add_drive_share("u001", "s1", "sigB", "p2")
    assert led.drive_shares("u001", "s1") == {"sigA": "p1", "sigB": "p2"}
    led.drop_drive_share("u001", "s1", "sigA")
    assert led.drive_shares("u001", "s1") == {"sigB": "p2"} and led.drive_shares("u002", "s1") == {}


def test_failures_include_drive(tmp_path):
    led = make(tmp_path)
    led.set_drive("u001", "s1", state="failed", reason="http_500")
    led.set_drive("u001", "s2", state="done")
    led.mark_failed("u001", "m1", "http_500")
    assert led.failures("u001") == {"http_500": 2}
