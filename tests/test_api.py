import pytest

from fakes import FakeCalendar, make_apis
from movingday.api import ApiError, with_retry


def _flaky(errors):
    state = {"n": 0}

    def fn():
        state["n"] += 1
        if errors:
            raise errors.pop(0)
        return "ok"

    return fn, state


def test_retry_waits_then_succeeds():
    slept = []
    fn, _ = _flaky([ApiError("rate_limited", True), ApiError("http_503", True)])
    assert with_retry(fn, sleep=slept.append) == "ok"
    assert slept == [1, 2]


def test_retry_gives_up_after_six():
    slept = []
    fn, state = _flaky([ApiError("http_503", True) for _ in range(10)])
    with pytest.raises(ApiError) as e:
        with_retry(fn, sleep=slept.append)
    assert e.value.reason == "http_503"
    assert slept == [1, 2, 4, 8, 16, 32]  # six retries after the first call
    assert state["n"] == 7


def test_non_retryable_raises_immediately():
    slept = []
    fn, state = _flaky([ApiError("http_404", False)])
    with pytest.raises(ApiError):
        with_retry(fn, sleep=slept.append)
    assert state["n"] == 1 and slept == []


def test_api_error_str_is_reason_only():
    assert str(ApiError("http_404", False)) == "http_404"


RAW = b"Message-ID: <abc@x>\r\nSubject: s\r\n\r\nbody"


def test_fake_gmail_roundtrip():
    g = make_apis().src_gmail
    m1 = g.add_message("a@x", RAW, ["INBOX"])
    m2 = g.add_message("a@x", b"Subject: none\r\n\r\nb", ["SENT"])
    msgs = g.list_messages("a@x")
    assert [m for m, _ in msgs] == [m2, m1]
    r = g.get_raw("a@x", m1)
    assert r.raw == RAW and r.label_ids == ["INBOX"]
    assert g.get_header_and_labels("a@x", m1) == ("<abc@x>", ["INBOX"])
    assert g.get_header_and_labels("a@x", m2) == (None, ["SENT"])
    h0 = int(g.profile("b@x")["historyId"])
    nid, tid = g.insert("b@x", RAW, ["INBOX"], None)
    nid2, tid2 = g.insert("b@x", RAW, [], tid)
    assert tid2 == tid and nid2 != nid
    p = g.profile("b@x")
    assert p["messagesTotal"] == 2 and int(p["historyId"]) > h0
    assert g.get_header_and_labels("b@x", nid) == ("<abc@x>", ["INBOX"])


def test_fake_create_label_is_case_insensitive():
    g = make_apis().dst_gmail
    lid = g.create_label("a@x", "Work")
    assert g.create_label("a@x", "work") == lid
    assert [l["name"] for l in g.list_labels("a@x") if l["type"] == "user"] == ["Work"]


def test_fake_records_calls():
    c = FakeCalendar()
    c.add_calendar("a@x", "cal1", summary="S")
    eid = c.import_event("a@x", "cal1", {"iCalUID": "u1"}, send_updates="none")
    c.insert_acl("a@x", "cal1", {"role": "reader"}, send_notifications=False)
    assert ("import_event", {"user": "a@x", "cal_id": "cal1", "body": {"iCalUID": "u1"},
                             "send_updates": "none"}) in c.calls
    assert c.calls[-1][1]["send_notifications"] is False
    assert [e["id"] for e in c.list_events("a@x", "cal1")] == [eid]


def test_fake_fail_next_accepts_instance_or_class():
    g = make_apis().src_gmail
    g.fail_next("list_messages", ApiError("http_500", True), times=2)
    for _ in range(2):
        with pytest.raises(ApiError):
            g.list_messages("a@x")
    assert g.list_messages("a@x") == []
    g.fail_next("profile", KeyboardInterrupt)
    with pytest.raises(KeyboardInterrupt):
        g.profile("a@x")


def test_http_errors_map_by_status_without_response_text():
    import httplib2
    from googleapiclient.errors import HttpError

    from movingday.api import _to_api_error

    def err(status, reason="x"):
        body = ('{"error": {"message": "secret@x.com", "errors": [{"reason": "%s"}]}}' % reason).encode()
        return _to_api_error(HttpError(httplib2.Response({"status": status}), body, uri="https://g/secret"))

    got = [(e.reason, e.retryable) for e in
           (err(429), err(403, "userRateLimitExceeded"), err(403), err(503), err(404))]
    assert got == [("rate_limited", True), ("rate_limited", True), ("http_403", False),
                   ("http_503", True), ("http_404", False)]
    assert "secret" not in str(err(500)) + repr(err(500))


def test_fake_list_events_keeps_cancelled_instances_only():
    c = FakeCalendar()
    c.add_calendar("a@x", "cal1")
    series = c.add_event("a@x", "cal1", {"summary": "s"})
    c.add_event("a@x", "cal1", {"status": "cancelled"})
    inst = c.add_event("a@x", "cal1", {"status": "cancelled", "recurringEventId": series,
                                       "originalStartTime": {"dateTime": "2026-01-02T10:00:00Z"}})
    assert [e["id"] for e in c.list_events("a@x", "cal1")] == [series, inst]


def test_fake_ids_are_unique_across_instances():
    a = make_apis()
    src = {a.src_gmail.add_message("u@x", RAW, []), a.src_gmail.add_label("u@x", "L"),
           a.src_cal.create_calendar("u@x", "S", "UTC")}
    dst = {a.dst_gmail.add_message("u@x", RAW, []), a.dst_gmail.add_label("u@x", "L"),
           a.dst_cal.create_calendar("u@x", "S", "UTC")}
    assert not src & dst


def test_fake_insert_rejects_unknown_label_ids():
    a = make_apis()
    src_label = a.src_gmail.add_label("u@x", "Work")
    assert a.src_gmail.calls == []  # seeding is not recorded
    with pytest.raises(ApiError) as e:
        a.dst_gmail.insert("u@x", RAW, ["INBOX", src_label], None)
    assert e.value.reason == "http_400"
    dst_label = a.dst_gmail.create_label("u@x", "Work")
    a.dst_gmail.insert("u@x", RAW, ["INBOX", dst_label], None)


def test_network_errors_map_to_retryable_api_error():
    import httplib2

    from movingday.api import _execute

    class Req:
        def execute(self):
            raise httplib2.ServerNotFoundError("host secret.example")

    with pytest.raises(ApiError) as e:
        _execute(Req)
    assert (e.value.reason, e.value.retryable) == ("network_error", True)
    assert e.value.__cause__ is None and e.value.__suppress_context__


def test_incomplete_read_maps_to_retryable_network_error():
    import http.client

    from movingday.api import _execute

    class Req:
        def execute(self):
            raise http.client.IncompleteRead(b"partial body")

    with pytest.raises(ApiError) as e:
        _execute(Req)
    assert (e.value.reason, e.value.retryable) == ("network_error", True)
    assert e.value.__cause__ is None and e.value.__suppress_context__
