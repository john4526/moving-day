import pytest
from fakes import make_apis

from movingday.api import ApiError
from movingday.calendar_copy import CalStats, copy_calendars
from movingday.ledger import Ledger
from movingday.mapping import Mapping, User

U, S, D = "u001", "s@x.org", "d@y.org"
G = "team@group.calendar.google.com"
MAP = Mapping([User("u001", S, D, "b"), User("u002", "pal@x.org", "pal@y.org", "b")])


@pytest.fixture
def env(tmp_path):
    a = make_apis()
    a.src_cal.add_calendar(S, S, primary=True)
    a.dst_cal.add_calendar(D, D, primary=True)
    led = Ledger(str(tmp_path / "l.db"))
    yield a, led
    led.close()


def run(env):
    a, led = env
    return copy_calendars(a.src_cal, a.dst_cal, led, MAP, U, S, D, "y.org")


def dst_events(a, cal=D):
    return a.dst_cal.cals[D][cal]["events"]


def test_primary_and_secondary_calendars_copied(env):
    a, led = env
    a.src_cal.add_calendar(S, G, summary="Team", time_zone="Africa/Nairobi")
    a.src_cal.add_event(S, S, {"summary": "p"})
    a.src_cal.add_event(S, G, {"summary": "q"})
    st = run(env)
    assert st == CalStats(2, 2, 0, 0, 0, 0, 0)
    assert [e["summary"] for e in dst_events(a).values()] == ["p"]
    new = led.cal_map(U)[G]
    assert led.cal_map(U)["primary"] == "primary"
    assert a.dst_cal.cals[D][new]["meta"]["summary"] == "Team"
    assert a.dst_cal.cals[D][new]["meta"]["timeZone"] == "Africa/Nairobi"
    run(env)
    assert sum(c == "create_calendar" for c, _ in a.dst_cal.calls) == 1
    assert len(a.dst_cal.cals[D]) == 2 and len(dst_events(a, new)) == 1


def test_recurring_series_and_exception_linked(env):
    a, led = env
    sid = a.src_cal.add_event(S, S, {"summary": "s", "recurrence": ["RRULE:FREQ=DAILY"]})
    a.src_cal.add_event(S, S, {"summary": "x", "recurringEventId": sid})
    a.src_cal.add_event(S, S, {"status": "cancelled", "recurringEventId": sid})
    st = run(env)
    assert st.events_copied == 3 and st.events_failed == 0
    dsid = led.event_rows(U, "primary")[sid][0]
    others = [e for e in dst_events(a).values() if e["id"] != dsid]
    assert len(others) == 2 and all(e["recurringEventId"] == dsid for e in others)


def test_exception_without_series_recorded_as_failed(env):
    a, led = env
    sid = a.src_cal.add_event(S, S, {"recurrence": ["RRULE:FREQ=DAILY"]})
    a.src_cal.add_event(S, S, {"recurringEventId": sid})
    a.dst_cal.fail_next("import_event", ApiError("http_400", False))
    st = run(env)
    assert st.events_failed == 2 and st.events_copied == 0
    assert led.failures(U) == {"http_400": 1, "series_missing": 1}


def test_all_day_event_copied(env):
    a, led = env
    a.src_cal.add_event(S, S, {"summary": "d", "start": {"date": "2026-03-01"}, "end": {"date": "2026-03-02"}})
    run(env)
    (e,) = dst_events(a).values()
    assert e["start"] == {"date": "2026-03-01"} and e["end"] == {"date": "2026-03-02"}


def test_rerun_updates_changed_event_without_duplicating(env):
    a, led = env
    eid = a.src_cal.add_event(S, S, {"summary": "v1"})
    run(env)
    assert run(env) == CalStats(1, 0, 0, 0, 0, 0, 0)
    ev = a.src_cal.cals[S][S]["events"][eid]
    ev["summary"], ev["updated"] = "v2", "2030-01-01T00:00:00.000Z"
    st = run(env)
    assert (st.events_copied, st.events_updated) == (0, 1)
    (e,) = dst_events(a).values()
    assert e["summary"] == "v2"


def test_deleted_source_event_removed_on_rerun(env):
    a, led = env
    eid = a.src_cal.add_event(S, S, {"summary": "x"})
    run(env)
    del a.src_cal.cals[S][S]["events"][eid]
    assert run(env).events_removed == 1
    assert dst_events(a) == {} and led.event_rows(U, "primary") == {}


def test_delete_already_gone_is_dropped(env):
    a, led = env
    eid = a.src_cal.add_event(S, S, {"summary": "x"})
    run(env)
    dst_events(a).clear()
    del a.src_cal.cals[S][S]["events"][eid]
    run(env)
    assert led.event_rows(U, "primary") == {}


def test_sharing_translated_through_mapping(env):
    a, led = env
    a.src_cal.add_acl(S, S, {"role": "owner", "scope": {"type": "user", "value": S}})
    a.src_cal.add_acl(S, S, {"role": "reader", "scope": {"type": "user", "value": "PAL@x.org"}})
    a.src_cal.add_acl(S, S, {"role": "reader", "scope": {"type": "domain", "value": "x.org"}})
    st = run(env)
    assert st.acl_copied == 2 and st.outside_shares == 0
    assert a.dst_cal.cals[D][D]["acl"] == [
        {"role": "reader", "scope": {"type": "user", "value": "pal@y.org"}},
        {"role": "reader", "scope": {"type": "domain", "value": "y.org"}}]
    assert run(env).acl_copied == 0  # already present


def test_source_domain_and_group_shares_translated(env):
    a, led = env
    a.src_cal.add_acl(S, S, {"role": "reader", "scope": {"type": "domain", "value": "X.org"}})
    a.src_cal.add_acl(S, S, {"role": "writer", "scope": {"type": "group", "value": "staff@x.org"}})
    st = run(env)
    assert (st.acl_copied, st.outside_shares) == (2, 0)
    assert a.dst_cal.cals[D][D]["acl"] == [
        {"role": "reader", "scope": {"type": "domain", "value": "y.org"}},
        {"role": "writer", "scope": {"type": "group", "value": "staff@y.org"}}]


def test_outside_domain_and_group_shares_kept_and_counted(env):
    a, led = env
    a.src_cal.add_acl(S, S, {"role": "reader", "scope": {"type": "domain", "value": "parent.org"}})
    a.src_cal.add_acl(S, S, {"role": "reader", "scope": {"type": "group", "value": "all@parent.org"}})
    a.src_cal.add_acl(S, S, {"role": "reader", "scope": {"type": "default"}})
    st = run(env)
    assert (st.acl_copied, st.outside_shares) == (3, 2)
    assert led.get_meta(U, "outside_shares") == "2"
    assert [r["scope"] for r in a.dst_cal.cals[D][D]["acl"]] == [
        {"type": "domain", "value": "parent.org"}, {"type": "group", "value": "all@parent.org"},
        {"type": "default"}]


def test_unmapped_share_kept_and_counted(env):
    a, led = env
    a.src_cal.add_acl(S, S, {"role": "writer", "scope": {"type": "user", "value": "out@z.com"}})
    st = run(env)
    assert st.outside_shares == 1 and st.acl_copied == 1
    assert led.get_meta(U, "outside_shares") == "1"
    assert a.dst_cal.cals[D][D]["acl"][0]["scope"]["value"] == "out@z.com"


def test_share_resolving_to_dst_user_skipped(env):
    a, led = env
    a.src_cal.add_calendar(S, G, summary="s")
    a.src_cal.add_acl(S, G, {"role": "writer", "scope": {"type": "user", "value": D}})
    st = run(env)
    assert st.acl_copied == 0 and st.outside_shares == 0


def test_no_notifications_ever(env):
    a, led = env
    a.src_cal.add_event(S, S, {"summary": "x"})
    eid = a.src_cal.add_event(S, S, {"summary": "y"})
    a.src_cal.add_acl(S, S, {"role": "reader", "scope": {"type": "user", "value": "pal@x.org"}})
    run(env)
    a.src_cal.cals[S][S]["events"][eid]["updated"] = "2030-01-01T00:00:00.000Z"
    run(env)
    del a.src_cal.cals[S][S]["events"][eid]
    run(env)
    w = [(m, k) for m, k in a.dst_cal.calls if m in ("import_event", "update_event", "delete_event", "insert_acl")]
    assert {m for m, _ in w} == {"import_event", "update_event", "delete_event", "insert_acl"}
    for m, k in w:
        assert k.get("send_updates") == "none" or k.get("send_notifications") is False


def test_subscribed_calendar_not_copied(env):
    a, led = env
    a.src_cal.add_calendar(S, "sub", access_role="reader")
    a.src_cal.add_event(S, "sub", {"summary": "x"})
    st = run(env)
    assert st.calendars == 1 and "sub" not in led.cal_map(U)
    assert len(a.dst_cal.cals[D]) == 1


def test_failed_event_recorded_and_run_continues(env):
    a, led = env
    bad = a.src_cal.add_event(S, S, {"summary": "bad", "eventType": "focusTime"})
    ok = a.src_cal.add_event(S, S, {"summary": "ok"})
    a.dst_cal.fail_next("import_event", ApiError("http_400", False))
    st = run(env)
    assert (st.events_copied, st.events_failed) == (1, 1)
    assert led.event_rows(U, "primary")[bad][2] == "failed"
    assert led.failures(U) == {"unsupported_event_type": 1}
    assert led.event_rows(U, "primary")[ok][2] == "done"
    st = run(env)  # failed rows retried
    assert (st.events_copied, st.events_failed) == (1, 0)


def test_failed_event_reason_kept_for_default_type(env):
    a, led = env
    a.src_cal.add_event(S, S, {"eventType": "default"})
    a.dst_cal.fail_next("import_event", ApiError("http_400", False))
    run(env)
    assert led.failures(U) == {"http_400": 1}


def test_source_is_only_read(env):
    a, led = env
    a.src_cal.add_event(S, S, {"summary": "x"})
    a.src_cal.add_acl(S, S, {"role": "reader", "scope": {"type": "user", "value": "pal@x.org"}})
    run(env)
    assert {m for m, _ in a.src_cal.calls} <= {"list_calendars", "list_events", "list_acl"}


def test_user_with_only_empty_primary_calendar(env):
    st = run(env)
    assert st == CalStats(1, 0, 0, 0, 0, 0, 0)
    assert led_meta(env) == "0"


def led_meta(env):
    return env[1].get_meta(U, "outside_shares")


def test_ledger_holds_no_content(env):
    a, led = env
    a.src_cal.add_event(S, S, {"summary": "SECRETSUMMARY", "attendees": [{"email": "who@z.com"}]})
    run(env)
    dump = "\n".join(led.db.iterdump())
    assert "SECRETSUMMARY" not in dump and "who@z.com" not in dump and S not in dump


def test_failed_acl_insert_counted_and_loop_continues(env):
    a, led = env
    a.src_cal.add_acl(S, S, {"role": "reader", "scope": {"type": "user", "value": "pal@x.org"}})
    a.src_cal.add_acl(S, S, {"role": "reader", "scope": {"type": "domain", "value": "x.org"}})
    a.dst_cal.fail_next("insert_acl", ApiError("http_403", False))
    st = run(env)
    assert st.acl_copied == 1
    assert led.get_meta(U, "acl_failed") == "1"
    assert a.dst_cal.cals[D][D]["acl"] == [{"role": "reader", "scope": {"type": "domain", "value": "y.org"}}]
    assert sum(m == "insert_acl" for m, _ in a.dst_cal.calls) == 2
    run(env)  # retried next run
    assert led.get_meta(U, "acl_failed") == "0" and len(a.dst_cal.cals[D][D]["acl"]) == 2


def test_failed_delete_recorded_and_retried(env):
    a, led = env
    eid = a.src_cal.add_event(S, S, {"summary": "x"})
    run(env)
    del a.src_cal.cals[S][S]["events"][eid]
    a.dst_cal.fail_next("delete_event", ApiError("http_500", False))
    assert run(env).events_failed == 1
    assert led.failures(U) == {"http_500": 1}
    assert led.event_rows(U, "primary")[eid][2] == "failed"
    assert run(env).events_removed == 1
    assert led.event_rows(U, "primary") == {} and dst_events(a) == {}


def test_delegated_other_user_primary_not_copied(env):
    a, led = env
    a.src_cal.add_calendar(S, "boss@x.org", summary="Boss", access_role="owner")  # "manage sharing" delegate
    a.src_cal.add_event(S, "boss@x.org", {"summary": "x"})
    st = run(env)
    assert st.calendars == 1 and len(a.dst_cal.cals[D]) == 1
    assert led.get_meta(U, "shared_calendars_skipped") == "1"
    assert "boss@x.org" not in "\n".join(led.db.iterdump())


def test_resource_calendar_skipped(env):
    a, led = env
    a.src_cal.add_calendar(S, "c_1@resource.calendar.google.com", access_role="owner")
    st = run(env)
    assert st.calendars == 1 and len(a.dst_cal.cals[D]) == 1
    assert led.get_meta(U, "shared_calendars_skipped") == "1"
