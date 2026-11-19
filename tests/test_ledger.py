import threading

from wsmigrate.ledger import Ledger


def make(tmp_path):
    return Ledger(str(tmp_path / "ledger.db"))


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
        led = make(tmp_path)
        for i in range(25):
            led.mark_pending("u001", f"m{n}-{i}", "t", "h")
            led.mark_done("u001", f"m{n}-{i}", "d", "dt")
    ts = [threading.Thread(target=work, args=(n,)) for n in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(make(tmp_path).done_ids("u001")) == 100
