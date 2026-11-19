import pytest
from fakes import make_apis

from movingday.api import ApiError
from movingday.calendar_copy import copy_calendars
from movingday.contacts import copy_contacts
from movingday.compare import (InventoryError, compare_calendar, compare_contacts, compare_drive, compare_inventory,
                               compare_mail, load_inventory)
from movingday.drive_copy import FORM, copy_drive
from movingday.ledger import Ledger
from movingday.mail import copy_mail
from movingday.mapping import Mapping, User

U, S, D = "u001", "s@x.org", "d@y.org"
G = "team@group.calendar.google.com"
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
    return compare_calendar(a.src_cal, a.dst_cal, led, MAP, U, S, D, "y.org")


def test_calendar_counts_match_after_copy(cal):
    a, led = cal
    a.src_cal.add_calendar(S, G, summary="Team")
    a.src_cal.add_event(S, S, {"summary": "r", "recurrence": ["RRULE:FREQ=DAILY"], "id": "r1",
                               "attendees": [{"email": "a@x"}, {"email": "b@x"}]})
    a.src_cal.add_event(S, S, {"summary": "x", "id": "x1", "status": "cancelled", "recurringEventId": "r1"})
    a.src_cal.add_event(S, G, {"summary": "q"})
    a.src_cal.add_acl(S, G, {"role": "reader", "scope": {"type": "user", "value": "pal@x.org"}})
    copy_calendars(a.src_cal, a.dst_cal, led, MAP, U, S, D, "y.org")
    r = cmp_cal(a, led)
    assert (r.calendars_src, r.calendars_dst) == (2, 2)
    assert (r.events_src, r.events_dst) == (3, 3)
    assert (r.series_src, r.series_dst) == (1, 1)
    assert (r.attendees_src, r.attendees_dst) == (2, 2)
    assert r.acl_missing == 0


def test_missing_event_and_unmapped_calendar_counted(cal):
    a, led = cal
    a.src_cal.add_calendar(S, G)
    a.src_cal.add_event(S, S, {"summary": "p"})
    a.src_cal.add_event(S, G, {"summary": "q"})
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
    copy_calendars(a.src_cal, a.dst_cal, led, MAP, U, S, D, "y.org")
    assert cmp_cal(a, led).acl_missing == 0
    a.dst_cal.cals[D][D]["acl"].pop()
    assert cmp_cal(a, led).acl_missing == 1
    a.dst_cal.add_acl(D, D, {"role": "reader", "scope": {"type": "domain", "value": "x.org"}})
    assert cmp_cal(a, led).acl_missing == 1  # the untranslated source domain does not count


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
    copy_calendars(a.src_cal, a.dst_cal, led, MAP, U, S, D, "y.org")
    evs = a.dst_cal.cals[D][D]["events"]
    del evs[next(iter(evs))]
    a.dst_cal.add_event(D, D, {"summary": "unrelated"})
    r = cmp_cal(a, led)
    assert (r.events_src, r.events_dst, r.events_missing) == (2, 2, 1)


def test_events_missing_for_unmapped_and_deleted_destination_calendar(cal):
    a, led = cal
    a.src_cal.add_calendar(S, G)
    a.src_cal.add_event(S, G, {"summary": "q"})
    a.src_cal.add_event(S, G, {"summary": "q2"})
    a.src_cal.add_acl(S, G, {"role": "reader", "scope": {"type": "user", "value": "pal@x.org"}})
    r = cmp_cal(a, led)
    assert (r.events_missing, r.acl_missing) == (2, 1)
    led.set_cal(U, G, "gone")  # mapped, but deleted on the destination
    r = cmp_cal(a, led)
    assert (r.calendars_dst, r.events_missing, r.acl_missing) == (0, 2, 1)


def test_group_calendar_owned_by_two_users_copied_once(cal):
    a, led = cal
    P, PD = "pal@x.org", "pal@y.org"
    a.src_cal.add_calendar(P, P, primary=True)
    a.dst_cal.add_calendar(PD, PD, primary=True)
    for u in (S, P):  # one team calendar, co-managed: both see it with accessRole owner
        a.src_cal.add_calendar(u, G, summary="Team")
        a.src_cal.add_event(u, G, {"summary": "q", "id": "g1"})
    copy_calendars(a.src_cal, a.dst_cal, led, MAP, U, S, D, "y.org")
    copy_calendars(a.src_cal, a.dst_cal, led, MAP, "u002", P, PD, "y.org")
    assert len(a.dst_cal.cals[D]) == 2 and len(a.dst_cal.cals[PD]) == 1
    assert led.get_meta(U, "shared_calendars_skipped") == "0"
    assert led.get_meta("u002", "shared_calendars_skipped") == "1"
    for key, su, du, n in ((U, S, D, 2), ("u002", P, PD, 1)):
        r = compare_calendar(a.src_cal, a.dst_cal, led, MAP, key, su, du, "y.org")
        assert (r.calendars_src, r.calendars_dst, r.events_missing, r.acl_missing) == (n, n, 0, 0)


def test_compare_calendar_does_not_claim_and_later_copy_still_can(cal):
    a, led = cal
    P, PD = "pal@x.org", "pal@y.org"
    a.src_cal.add_calendar(P, P, primary=True)
    a.dst_cal.add_calendar(PD, PD, primary=True)
    for u in (S, P):
        a.src_cal.add_calendar(u, G, summary="Team")
        a.src_cal.add_event(u, G, {"summary": "q", "id": "g1"})
    assert cmp_cal(a, led).calendars_src == 2  # primary + unclaimed group calendar
    assert led._r("SELECT * FROM calendar_claims") == []
    copy_calendars(a.src_cal, a.dst_cal, led, MAP, "u002", P, PD, "y.org")  # the other co-manager goes first
    assert len(a.dst_cal.cals[PD]) == 2
    assert led.calendar_claimant(G) == "u002"
    assert cmp_cal(a, led).calendars_src == 1  # claimed by someone else: no longer ours


def _person(name, groups=()):
    meta = {"source": {"type": "CONTACT", "id": "x"}}
    return {"names": [{"givenName": name, "metadata": meta}],
            "memberships": [{"contactGroupMembership": {"contactGroupResourceName": g}} for g in groups],
            "metadata": {"sources": [{"type": "CONTACT", "id": "x", "etag": "e"}]}}


@pytest.fixture
def con(env):
    a, led = env
    g = a.src_people.add_group(S, "Team")
    for n in range(3):
        a.src_people.add_contact(S, _person(f"n{n}", [g] if n < 2 else []))
    return a, led


def cmp_con(a, led):
    return compare_contacts(a.src_people, a.dst_people, led, U, S, D)


def test_contacts_match_after_copy(con):
    a, led = con
    copy_contacts(a.src_people, a.dst_people, led, U, S, D)
    r = cmp_con(a, led)
    assert (r.contacts_src, r.contacts_dst, r.contacts_missing) == (3, 3, 0)
    assert (r.labels_src, r.labels_dst, r.label_member_mismatches) == (1, 1, 0)


def test_contact_never_copied_or_deleted_on_destination_is_missing(con):
    a, led = con
    assert cmp_con(a, led).contacts_missing == 3  # nothing copied yet
    copy_contacts(a.src_people, a.dst_people, led, U, S, D)
    a.dst_people.contacts[D].popitem()
    assert cmp_con(a, led).contacts_missing == 1


def test_contact_row_not_done_is_missing_and_gone_row_is_not_expected(con):
    a, led = con
    copy_contacts(a.src_people, a.dst_people, led, U, S, D)
    sid, (did, etag, _) = next(iter(led.contact_rows(U).items()))
    led.set_contact(U, sid, did, etag, "failed", "http_500")
    assert cmp_con(a, led).contacts_missing == 1
    del a.src_people.contacts[S][sid]  # removed on source: no longer listed, so not expected
    assert cmp_con(a, led).contacts_missing == 0


def test_extra_destination_contact_does_not_hide_a_missing_one(con):
    a, led = con
    copy_contacts(a.src_people, a.dst_people, led, U, S, D)
    a.dst_people.contacts[D].popitem()
    a.dst_people.add_contact(D, _person("extra"))
    r = cmp_con(a, led)
    assert (r.contacts_dst, r.contacts_missing) == (3, 1)


def test_label_missing_or_with_fewer_members_is_a_mismatch(con):
    a, led = con
    copy_contacts(a.src_people, a.dst_people, led, U, S, D)
    (res,) = a.dst_people.groups[D]
    for c in list(a.dst_people.contacts[D].values())[:1]:
        c["memberships"] = []
    assert cmp_con(a, led).label_member_mismatches == 1
    a.dst_people.groups[D].clear()
    r = cmp_con(a, led)
    assert (r.labels_src, r.labels_dst, r.label_member_mismatches) == (1, 0, 1)


def test_label_matched_by_name_ignoring_case_and_extra_members_ok(con):
    a, led = con
    copy_contacts(a.src_people, a.dst_people, led, U, S, D)
    (res,) = a.dst_people.groups[D]
    a.dst_people.groups[D][res] = "TEAM"
    a.dst_people.add_contact(D, _person("more", [res]))
    assert cmp_con(a, led).label_member_mismatches == 0


def test_no_contacts_all_zero(env):
    a, led = env
    r = cmp_con(a, led)
    assert (r.contacts_src, r.contacts_dst, r.contacts_missing, r.labels_src, r.label_member_mismatches) == (0,) * 5


def test_compare_contacts_only_reads(con):
    a, led = con
    cmp_con(a, led)
    assert {c[0] for c in a.src_people.calls + a.dst_people.calls} <= {"list_contacts", "list_groups"}


PDF, DOC = "application/pdf", "application/vnd.google-apps.document"


def copy_drv(a, led):
    return copy_drive(a.src_drive, a.dst_drive, led, MAP, U, S, D, "y.org")


def cmp_drv(a, led):
    return compare_drive(a.src_drive, a.dst_drive, led, MAP, U, S, D, "y.org")


@pytest.fixture
def drv(env):
    a, led = env
    top = a.src_drive.add_folder(S, name="top")
    a.src_drive.add_file(S, PDF, top, b"one")
    a.src_drive.add_file(S, PDF, content=b"two")
    a.src_drive.add_file(S, DOC, top, b"doc")
    a.src_drive.add_file(S, FORM)  # unsupported: neither counted nor expected
    return a, led


def test_compare_drive_clean(drv):
    a, led = drv
    copy_drv(a, led)
    r = cmp_drv(a, led)
    assert (r.files_src, r.files_dst, r.files_missing, r.folders_src, r.folders_dst) == (3, 3, 0, 1, 1)
    assert (r.checksum_mismatches, r.acl_missing, r.acl_extra, r.out_of_step, r.folder_not_copied) == (0,) * 5
    assert {c[0] for c in a.src_drive.calls} <= {"root_id", "list_owned", "list_permissions", "download", "export"}


def test_compare_drive_missing_and_checksum(drv):
    a, led = drv
    r = cmp_drv(a, led)
    assert (r.files_missing, r.files_dst, r.out_of_step) == (3, 0, 5)  # nothing copied: every owned item has no row
    copy_drv(a, led)
    rows = led.drive_rows(U)
    pdfs = [sid for sid, row in rows.items() if row["md5"]]
    a.dst_drive.files[rows[pdfs[0]]["dst_id"]]["md5Checksum"] = "bad"
    a.dst_drive.files[rows[pdfs[1]]["dst_id"]]["trashed"] = True
    r = cmp_drv(a, led)
    assert (r.files_dst, r.files_missing, r.checksum_mismatches) == (2, 1, 1)


def test_compare_drive_acl_missing_and_extra(drv):
    a, led = drv
    sid = next(i for i, f in a.src_drive.files.items() if f["mimeType"] == PDF)
    a.src_drive.share(S, sid, {"role": "writer", "type": "user", "emailAddress": "pal@x.org"})
    a.src_drive.share(S, sid, {"role": "reader", "type": "anyone", "allowFileDiscovery": False})
    copy_drv(a, led)
    did = led.drive_rows(U)[sid]["dst_id"]
    perms = a.dst_drive.files[did]["perms"]
    perms.pop(next(k for k, p in perms.items() if p["type"] == "anyone"))
    a.dst_drive.share(D, did, {"role": "reader", "type": "user", "emailAddress": "stranger@else.com"})
    a.dst_drive.share(D, did, {"role": "owner", "type": "user", "emailAddress": D})  # the owner is not extra
    r = cmp_drv(a, led)
    assert (r.acl_missing, r.acl_extra, r.out_of_step) == (1, 1, 0)


def test_compare_drive_out_of_step_after_source_edit(drv):
    a, led = drv
    copy_drv(a, led)
    files = [i for i, f in a.src_drive.files.items() if f["mimeType"] == PDF]
    a.src_drive.edit(files[0], content=b"changed")                     # version
    a.src_drive.edit(files[1], parent=next(i for i, f in a.src_drive.files.items()
                                           if f["mimeType"].endswith("folder")))  # parent (and version)
    new = a.src_drive.add_file(S, PDF, content=b"new")                 # no row
    a.src_drive.share(S, new, {"role": "reader", "type": "anyone"})
    doc = next(i for i, f in a.src_drive.files.items() if f["mimeType"] == DOC)
    a.src_drive.share(S, doc, {"role": "reader", "type": "anyone"})    # sharing fingerprint
    r = cmp_drv(a, led)
    assert r.out_of_step == 4
    copy_drv(a, led)
    assert cmp_drv(a, led).out_of_step == 0
    a.src_drive.files[doc]["trashed"] = True                           # done row whose item is gone
    assert cmp_drv(a, led).out_of_step == 1
    copy_drv(a, led)  # the copy is trashed and the row closed
    assert cmp_drv(a, led).out_of_step == 0


def test_compare_drive_counts_files_whose_folder_is_not_copied(drv):
    a, led = drv
    theirs = a.src_drive.add_folder("pal@x.org", name="theirs")
    a.src_drive.add_file(S, PDF, theirs, b"in theirs")
    copy_drv(a, led)
    r = cmp_drv(a, led)
    assert (r.folder_not_copied, r.out_of_step, r.files_missing) == (1, 0, 0)


def test_compare_drive_failed_share_not_out_of_step(drv):
    a, led = drv
    sid = next(i for i, f in a.src_drive.files.items() if f["mimeType"] == PDF)
    a.src_drive.share(S, sid, {"role": "reader", "type": "user", "emailAddress": "pal@x.org"})
    a.dst_drive.fail_next("add_permission", ApiError("http_500", False))
    copy_drv(a, led)
    r = cmp_drv(a, led)
    assert (r.out_of_step, r.acl_missing) == (0, 1)


def test_compare_drive_failed_row_stale_version_not_out_of_step(drv):
    a, led = drv
    copy_drv(a, led)
    sid = next(i for i, f in a.src_drive.files.items() if f["mimeType"] == PDF)
    a.src_drive.edit(sid, content=b"changed")
    a.dst_drive.fail_next("update_content", ApiError("http_500", False))
    copy_drv(a, led)
    assert led.drive_rows(U)[sid]["state"] == "failed"
    r = cmp_drv(a, led)
    assert (r.out_of_step, r.files_missing) == (0, 1)


def test_compare_drive_reshare_only_in_step_check(drv):
    a, led = drv
    copy_drv(a, led)
    doc = next(i for i, f in a.src_drive.files.items() if f["mimeType"] == DOC)
    a.src_drive.share(S, doc, {"role": "reader", "type": "anyone"})
    r = cmp_drv(a, led)
    assert (r.out_of_step, r.acl_missing, r.acl_extra) == (1, 0, 0)


def test_compare_drive_counts_only_tagged_destination_files(drv):
    a, led = drv
    copy_drv(a, led)
    a.dst_drive.add_file(D, PDF, content=b"the person's own")  # listed under the full drive scope
    a.dst_drive.add_folder(D)
    r = cmp_drv(a, led)
    assert (r.files_dst, r.folders_dst) == (3, 1)
