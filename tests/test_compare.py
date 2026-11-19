import pytest
from fakes import make_apis

from wsmigrate.calendar_copy import copy_calendars
from wsmigrate.compare import InventoryError, compare_calendar, compare_inventory, compare_mail, load_inventory
from wsmigrate.ledger import Ledger
from wsmigrate.mail import copy_mail
from wsmigrate.mapping import Mapping, User

U, S, D = "u001", "s@x.org", "d@y.org"
MAP = Mapping([User("u001", S, D, "b"), User("u002", "pal@x.org", "pal@y.org", "b")])


def raw(n, mid=True):
    h = f"Subject: s{n}\r\n" + (f"Message-ID: <id{n}@x>\r\n" if mid else "")
    return (h + f"\r\nbody{n}\r\n").encode()


@pytest.fixture
def env(tmp_path):
    a = make_apis()
    led = Ledger(str(tmp_path / "l.db"))
    yield a, led
    led.close()


def mail(a, side, n, labels=("INBOX",), **kw):
    g, u = (a.src_gmail, S) if side == "s" else (a.dst_gmail, D)
    return g.add_message(u, raw(n, **kw), list(labels))


def cmp_mail(a, pre=0):
    return compare_mail(a.src_gmail, a.dst_gmail, S, D, pre)


def test_identical_mailboxes_all_zero(env):
    a, led = env
    lid = a.src_gmail.add_label(S, "Donors")
    for n in range(3):
        mail(a, "s", n, ["INBOX", lid])
    mail(a, "s", 9, ["SPAM"])
    copy_mail(a.src_gmail, a.dst_gmail, led, U, S, D)
    r = cmp_mail(a)
    assert (r.src_total, r.dst_total) == (3, 3)
    assert (r.missing, r.duplicates, r.extra, r.label_mismatches) == (0, 0, 0, 0)
    assert r.threads_src == r.threads_dst == 3


def test_missing_message_counted(env):
    a, led = env
    for n in range(3):
        mail(a, "s", n)
    copy_mail(a.src_gmail, a.dst_gmail, led, U, S, D)
    del a.dst_gmail.messages[D][next(iter(a.dst_gmail.messages[D]))]
    r = cmp_mail(a)
    assert (r.missing, r.duplicates, r.extra) == (1, 0, 0)
    assert r.label_mismatches == 1  # INBOX 3 vs 2


def test_duplicate_on_destination_counted(env):
    a, _ = env
    mail(a, "s", 1)
    mail(a, "d", 1)
    mail(a, "d", 1)
    r = cmp_mail(a)
    assert (r.missing, r.duplicates, r.extra) == (0, 1, 0)


def test_foreign_message_counted_as_extra(env):
    a, _ = env
    mail(a, "s", 1)
    mail(a, "d", 1)
    mail(a, "d", 2)
    r = cmp_mail(a)
    assert (r.missing, r.duplicates, r.extra) == (0, 0, 1)


def test_welcome_mail_within_preexisting_is_not_a_label_mismatch(env):
    a, _ = env
    mail(a, "s", 1)
    mail(a, "d", 1)
    mail(a, "d", 2)
    assert cmp_mail(a, pre=1).label_mismatches == 0
    assert cmp_mail(a, pre=0).label_mismatches == 1


def test_user_labels_matched_by_name_case_insensitively_and_dst_only_ignored(env):
    a, _ = env
    sl, dl = a.src_gmail.add_label(S, "Donors"), a.dst_gmail.add_label(D, "donors")
    extra = a.dst_gmail.add_label(D, "OnlyHere")
    mail(a, "s", 1, [sl])
    mail(a, "d", 1, [dl, extra])
    assert cmp_mail(a).label_mismatches == 0


def test_headerless_messages_compared_by_count(env):
    a, _ = env
    for _ in range(2):
        mail(a, "s", 0, mid=False)
    mail(a, "d", 0, mid=False)
    assert cmp_mail(a).missing == 1
    mail(a, "d", 0, mid=False)
    mail(a, "d", 0, mid=False)
    r = cmp_mail(a)
    assert (r.missing, r.extra) == (0, 1)


def test_shared_message_id_not_reported_as_duplicate(env):
    a, _ = env
    for _ in range(2):
        mail(a, "s", 1)
        mail(a, "d", 1)
    r = cmp_mail(a)
    assert (r.missing, r.duplicates, r.extra) == (0, 0, 0)


def test_draft_and_chat_excluded_from_totals(env):
    a, _ = env
    mail(a, "s", 1)
    mail(a, "s", 2, ["DRAFT"])
    mail(a, "d", 1)
    mail(a, "d", 3, ["CHAT"])
    r = cmp_mail(a)
    assert (r.src_total, r.dst_total, r.extra) == (1, 1, 0)


@pytest.fixture
def cal(env):
    a, led = env
    a.src_cal.add_calendar(S, S, primary=True)
    a.dst_cal.add_calendar(D, D, primary=True)
    return a, led


def cmp_cal(a, led):
    return compare_calendar(a.src_cal, a.dst_cal, led, MAP, U, S, D)


def test_calendar_counts_match_after_copy(cal):
    a, led = cal
    a.src_cal.add_calendar(S, "sec1", summary="Team")
    a.src_cal.add_event(S, S, {"summary": "r", "recurrence": ["RRULE:FREQ=DAILY"], "id": "r1",
                               "attendees": [{"email": "a@x"}, {"email": "b@x"}]})
    a.src_cal.add_event(S, S, {"summary": "x", "id": "x1", "status": "cancelled", "recurringEventId": "r1"})
    a.src_cal.add_event(S, "sec1", {"summary": "q"})
    a.src_cal.add_acl(S, "sec1", {"role": "reader", "scope": {"type": "user", "value": "pal@x.org"}})
    copy_calendars(a.src_cal, a.dst_cal, led, MAP, U, S, D)
    r = cmp_cal(a, led)
    assert (r.calendars_src, r.calendars_dst) == (2, 2)
    assert (r.events_src, r.events_dst) == (3, 3)
    assert (r.series_src, r.series_dst) == (1, 1)
    assert (r.attendees_src, r.attendees_dst) == (2, 2)
    assert r.acl_missing == 0


def test_missing_event_and_unmapped_calendar_counted(cal):
    a, led = cal
    a.src_cal.add_calendar(S, "sec1")
    a.src_cal.add_event(S, S, {"summary": "p"})
    a.src_cal.add_event(S, "sec1", {"summary": "q"})
    r = cmp_cal(a, led)  # nothing copied
    assert (r.calendars_src, r.calendars_dst) == (2, 0)
    assert (r.events_src, r.events_dst) == (2, 0)


def test_unsupported_event_type_excluded_only_from_source(cal):
    a, led = cal
    a.src_cal.add_event(S, S, {"summary": "p", "id": "e1"})
    a.src_cal.add_event(S, S, {"summary": "f", "id": "e2", "eventType": "focusTime"})
    a.src_cal.add_event(S, S, {"summary": "g", "id": "e3"})
    led.set_cal(U, "primary", "primary")
    led.set_event(U, "primary", "e2", "", "t", "failed", "unsupported_event_type")
    led.set_event(U, "primary", "e3", "", "t", "failed", "http_500")
    a.dst_cal.add_event(D, D, {"summary": "p"})
    r = cmp_cal(a, led)
    assert (r.events_src, r.events_dst) == (2, 1)  # e3 failed for another reason: still counted


def test_missing_share_counted(cal):
    a, led = cal
    a.src_cal.add_acl(S, S, {"role": "owner", "scope": {"type": "user", "value": S}})  # skipped
    a.src_cal.add_acl(S, S, {"role": "reader", "scope": {"type": "user", "value": "pal@x.org"}})
    a.src_cal.add_acl(S, S, {"role": "reader", "scope": {"type": "domain", "value": "x.org"}})
    copy_calendars(a.src_cal, a.dst_cal, led, MAP, U, S, D)
    assert cmp_cal(a, led).acl_missing == 0
    a.dst_cal.cals[D][D]["acl"].pop()
    assert cmp_cal(a, led).acl_missing == 1


def test_inventory_missing_group_member_and_alias_counted(tmp_path):
    p = tmp_path / "inv.csv"
    p.write_text("type,address,parent\n"
                 "user,ann@x.org,\nuser,bob@x.org,\n"
                 "group,team@x.org,\ngroup,gone@x.org,\n"
                 "member,ann@x.org,team@x.org\nmember,bob@x.org,team@x.org\nmember,ann@x.org,gone@x.org\n"
                 "alias,a1@x.org,ann@x.org\nalias,a2@x.org,ann@x.org\n")
    rows = load_inventory(str(p))
    assert rows[0] == ("user", "ann@x.org", "")
    d = make_apis().dst_dir
    d.users, d.groups, d.aliases = {"ann@y.org"}, {"team@y.org": {"ann@y.org"}}, {"a1@y.org": "ann@y.org"}
    assert compare_inventory(rows, d, "y.org") == {
        "missing_users": 1, "missing_groups": 1, "missing_members": 2, "missing_aliases": 1}


def test_label_dropped_on_destination_counts_despite_preexisting(env):
    a, _ = env
    lid = a.src_gmail.add_label(S, "Board")
    mail(a, "s", 1, ["INBOX", lid])
    mail(a, "d", 1, ["INBOX"])
    assert cmp_mail(a, pre=1).label_mismatches == 1


def test_inventory_bad_header_and_type_rejected(tmp_path):
    p = tmp_path / "i.csv"
    p.write_text("Type,Email,Parent\nuser,a@x.org,\n")
    with pytest.raises(InventoryError) as e:
        load_inventory(str(p))
    assert (e.value.reason, e.value.row) == ("missing_column", 1)
    p.write_text("Type, ADDRESS ,parent\nusers,a@x.org,\n")
    with pytest.raises(InventoryError) as e:
        load_inventory(str(p))
    assert (e.value.reason, e.value.row) == ("bad_type", 2)


def test_event_missing_not_hidden_by_extra_event(cal):
    a, led = cal
    for n in range(2):
        a.src_cal.add_event(S, S, {"summary": "s", "id": f"e{n}"})
    copy_calendars(a.src_cal, a.dst_cal, led, MAP, U, S, D)
    evs = a.dst_cal.cals[D][D]["events"]
    del evs[next(iter(evs))]
    a.dst_cal.add_event(D, D, {"summary": "unrelated"})
    r = cmp_cal(a, led)
    assert (r.events_src, r.events_dst, r.events_missing) == (2, 2, 1)


def test_events_missing_for_unmapped_and_deleted_destination_calendar(cal):
    a, led = cal
    a.src_cal.add_calendar(S, "sec1")
    a.src_cal.add_event(S, "sec1", {"summary": "q"})
    a.src_cal.add_event(S, "sec1", {"summary": "q2"})
    a.src_cal.add_acl(S, "sec1", {"role": "reader", "scope": {"type": "user", "value": "pal@x.org"}})
    r = cmp_cal(a, led)
    assert (r.events_missing, r.acl_missing) == (2, 1)
    led.set_cal(U, "sec1", "gone")  # mapped, but deleted on the destination
    r = cmp_cal(a, led)
    assert (r.calendars_dst, r.events_missing, r.acl_missing) == (0, 2, 1)
