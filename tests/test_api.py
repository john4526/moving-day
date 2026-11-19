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


def test_fake_people_update_needs_fresh_metadata_sources():
    from fakes import FakePeople

    p = FakePeople()
    res = p.add_contact("a@x", {"names": [{"givenName": "A"}]})
    meta = p.get_update_meta("a@x", res)
    [source] = meta["metadata"]["sources"]
    assert source["type"] == "CONTACT" and source["etag"] == meta["etag"]
    with pytest.raises(ApiError) as e:  # updateContact without metadata.sources is a 400
        p.update_contact("a@x", res, {"names": [{"givenName": "B"}], "etag": meta["etag"]})
    assert (e.value.reason, e.value.retryable) == ("http_400", False)
    new = p.update_contact("a@x", res, {"names": [{"givenName": "B"}], **meta})
    assert new != meta["etag"] and p.get_update_meta("a@x", res)["etag"] == new
    with pytest.raises(ApiError) as e:  # stale source etag: 400 failedPrecondition
        p.update_contact("a@x", res, {"names": [{"givenName": "C"}], **meta})
    assert e.value.reason == "http_400"
    [c] = p.list_contacts("a@x")
    assert c["resourceName"] == res and c["etag"] == new and c["names"] == [{"givenName": "B"}]
    assert "metadata" not in c


def test_fake_people_rejects_two_values_for_a_singleton_field():
    from fakes import FakePeople

    p = FakePeople()
    two = {"names": [{"givenName": "A"}, {"givenName": "B"}]}
    with pytest.raises(ApiError) as e:
        p.create_contact("a@x", two)
    assert e.value.reason == "http_400"
    res, _ = p.create_contact("a@x", {"names": [{"givenName": "A"}], "emailAddresses": [{"value": "1"}, {"value": "2"}]})
    with pytest.raises(ApiError):
        p.update_contact("a@x", res, {**two, **p.get_update_meta("a@x", res)})


def test_fake_people_groups_and_other_contacts():
    from fakes import FakePeople

    p = FakePeople()
    g = p.add_group("a@x", "Work")
    assert p.create_group("a@x", "work") == g
    groups = {x["name"]: x for x in p.list_groups("a@x")}
    assert groups["Work"]["groupType"] == "USER_CONTACT_GROUP"
    assert groups["myContacts"]["groupType"] == "SYSTEM_CONTACT_GROUP"
    p.add_contact("a@x", {"memberships": [{"contactGroupMembership": {"contactGroupResourceName": g}}]})
    assert {x["name"]: x["memberCount"] for x in p.list_groups("a@x")}["Work"] == 1
    with pytest.raises(ApiError):  # membership of a group the account does not have
        p.create_contact("b@x", {"memberships": [{"contactGroupMembership": {"contactGroupResourceName": g}}]})
    p.set_other_contacts("a@x", 4)
    assert p.count_other_contacts("a@x") == 4 and p.count_other_contacts("b@x") == 0


# --- Drive (real wrapper over stand-in HTTP/service objects) ---
import json
import threading

from googleapiclient.errors import HttpError
from httplib2 import Response

from fakes import FakeDrive
from movingday.api import CHUNK, Drive


def _drive(svc):
    d = Drive.__new__(Drive)
    d._services = {(threading.get_ident(), "u@x"): svc}
    return d


def _http_error(status, reason=""):
    body = json.dumps({"error": {"errors": [{"reason": reason}]}}).encode()
    return HttpError(Response({"status": status}), body)


class _Req:
    def __init__(self, fn):
        self.execute = fn


class _Files:
    def __init__(self, svc):
        self.svc = svc

    def export(self, **kw):
        def go():
            if self.svc.export_error:
                raise self.svc.export_error
            return b"exported"
        return _Req(go)

    def get(self, **kw):
        return _Req(lambda: {"exportLinks": {"application/pdf": "https://link/x"}})


class _Svc:
    def __init__(self, link_status=200, export_error=None):
        self.export_error, self.requests = export_error, []
        svc = self

        class Http:
            def request(self, url, method="GET", body=None, headers=None):
                svc.requests.append((url, method, body, headers))
                return svc.respond(url, method, body, headers)

        self._http = Http()
        self.link_status = link_status
        self.perm_calls = []

    def files(self):
        return _Files(self)

    def permissions(self):
        svc = self

        class P:
            def create(self, **kw):
                svc.perm_calls.append(kw)
                return _Req(lambda: {"id": "p1"})
        return P()

    def respond(self, url, method, body, headers):
        if url == "https://link/x":
            return Response({"status": self.link_status}), b"linked"
        raise AssertionError(url)


def test_drive_export_falls_back_to_export_link():
    svc = _Svc(export_error=_http_error(403, "exportSizeLimitExceeded"))
    assert b"".join(_drive(svc).export("u@x", "f1", "application/pdf")) == b"linked"
    assert [r[0] for r in svc.requests] == ["https://link/x"]
    both = _Svc(link_status=500, export_error=_http_error(403, "exportSizeLimitExceeded"))
    both.link_status = 404
    with pytest.raises(ApiError) as e:
        list(_drive(both).export("u@x", "f1", "application/pdf"))
    assert e.value.reason == "export_too_large" and not e.value.retryable


def test_drive_upload_refused_conversion_retries_unconverted():
    svc = _Svc()
    sent = []

    def respond(url, method, body, headers):
        if method == "POST":
            if json.loads(body).get("mimeType", "").startswith("application/vnd.google-apps."):
                return Response({"status": 400}), b""
            return Response({"status": 200, "location": "https://up/1"}), b""
        sent.append((headers["Content-Range"], body))
        return Response({"status": 200}), b'{"id": "new1"}'

    svc.respond = respond
    meta = {"name": "n", "mimeType": "application/vnd.google-apps.document"}
    out = _drive(svc).upload("u@x", meta, iter([b"abc"]), "application/msword", True)
    assert out == ("new1", False)
    assert sent == [("bytes 0-2/3", b"abc")]
    assert json.loads(svc.requests[1][2])["mimeType"] == "application/msword"


def test_drive_upload_sends_chunks_with_open_ended_range_until_last():
    svc = _Svc()
    sent = []

    def respond(url, method, body, headers):
        if method == "POST":
            return Response({"status": 200, "location": "https://up/1"}), b""
        sent.append(headers["Content-Range"])
        last = headers["Content-Range"].endswith("/5")
        return Response({"status": 200 if last else 308}), b'{"id": "n"}'

    svc.respond = respond
    assert _drive(svc).upload("u@x", {"name": "n"}, iter([b"ab", b"cde"]), "text/plain", False) == ("n", False)
    assert sent == ["bytes 0-1/*", "bytes 2-4/5"]


def test_drive_add_permission_never_notifies():
    svc = _Svc()
    assert _drive(svc).add_permission("u@x", "f1", {"role": "reader", "type": "user"}) == "p1"
    assert svc.perm_calls[0]["sendNotificationEmail"] is False


def test_drive_download_yields_bounded_chunks():
    data = bytes(CHUNK * 2 + 5)

    class Http:
        def request(self, uri, method="GET", body=None, headers=None):
            lo, hi = headers["range"].removeprefix("bytes=").split("-")
            part = data[int(lo):int(hi) + 1]
            return Response({"status": 206, "content-range": f"bytes {lo}-{int(lo) + len(part) - 1}/{len(data)}"}), part

    class Req:
        uri, http, headers = "https://dl", Http(), {}

    class Files:
        def get_media(self, **kw):
            return Req()

    class Svc:
        def files(self):
            return Files()

    chunks = list(_drive(Svc()).download("u@x", "f1"))
    assert [len(c) for c in chunks] == [CHUNK, CHUNK, 5]


def test_fake_drive_refuses_writes_on_source():
    d = FakeDrive(source=True)
    with pytest.raises(AssertionError):
        d.trash("u@x", "nope")
    assert d.root_id("u@x") == "root-u@x"
    f = d.add_file("u@x", "text/plain", content=b"x")
    assert d.list_owned("u@x")[0]["parents"] == ["root-u@x"] and d.list_owned("u@x")[0]["id"] == f
