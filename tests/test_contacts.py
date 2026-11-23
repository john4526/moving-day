import pytest
from fakes import FakePeople, make_apis

from movingday.api import ApiError, People
from movingday.contacts import ContactStats, copy_contacts
from movingday.ledger import Ledger

U, S, D = "u001", "s@x.org", "d@y.org"
MY, STAR = "contactGroups/myContacts", "contactGroups/starred"


@pytest.fixture
def env(tmp_path):
    a = make_apis()
    led = Ledger(str(tmp_path / "l.db"))
    yield a, led
    led.close()


def run(env):
    a, led = env
    return copy_contacts(a.src_people, a.dst_people, led, U, S, D)


def mem(group):
    return {"contactGroupMembership": {"contactGroupResourceName": group, "contactGroupId": group.split("/")[1]},
            "metadata": {"source": {"type": "CONTACT", "id": "x"}}}


def person(name=None, email=None, phone=None, groups=()):
    meta = {"primary": True, "source": {"type": "CONTACT", "id": "x"}}
    p = {"memberships": [mem(MY)] + [mem(g) for g in groups],
         "metadata": {"sources": [{"type": "CONTACT", "id": "x", "etag": "e"}, {"type": "PROFILE", "id": "p"}]}}
    if name:
        p["names"] = [{"givenName": name, "metadata": meta}]
    if email:
        p["emailAddresses"] = [{"value": email, "type": "work", "metadata": meta}]
    if phone:
        p["phoneNumbers"] = [{"value": phone, "metadata": meta}]
    return p


def edit(a, res, p):
    a.src_people.update_contact(S, res, {**p, **a.src_people.get_update_meta(S, res)})


def dst_contacts(a):
    return list(a.dst_people.contacts.get(D, {}).values())


def label_names(a, contact):
    names = {g["resourceName"]: g["name"] for g in a.dst_people.list_groups(D)}
    return {names[m["contactGroupMembership"]["contactGroupResourceName"]] for m in contact["memberships"]}


def test_copies_contacts_with_fields_and_labels(env):
    a, led = env
    fam = a.src_people.add_group(S, "Family")
    a.src_people.add_contact(S, person("Wanjiru", "w@x.org", "+254700000001",
                                       groups=(fam, STAR, "contactGroups/friends")))
    a.src_people.set_other_contacts(S, 3)
    assert run(env) == ContactStats(1, 1, 0, 0, 1, 0)
    [c] = dst_contacts(a)
    assert c["names"] == [{"givenName": "Wanjiru"}]
    assert c["emailAddresses"] == [{"value": "w@x.org", "type": "work"}]
    assert c["phoneNumbers"] == [{"value": "+254700000001"}]
    assert label_names(a, c) == {"myContacts", "starred", "Family"}
    assert led.get_meta(U, "other_contacts") == "3"


def test_label_case_reuse(env):
    a, _ = env
    existing = a.dst_people.add_group(D, "family")
    fam = a.src_people.add_group(S, "Family")
    a.src_people.add_contact(S, person("A", groups=(fam,)))
    assert run(env).labels_created == 0
    assert [g["name"] for g in a.dst_people.list_groups(D) if g["groupType"] == "USER_CONTACT_GROUP"] == ["family"]
    [c] = dst_contacts(a)
    assert existing in {m["contactGroupMembership"]["contactGroupResourceName"] for m in c["memberships"]}


def test_contact_with_only_a_phone(env):
    a, _ = env
    a.src_people.add_contact(S, person(phone="+254700000002"))
    assert run(env) == ContactStats(1, 1, 0, 0, 0, 0)
    [c] = dst_contacts(a)
    assert c["phoneNumbers"] == [{"value": "+254700000002"}] and "names" not in c


def test_rerun_no_duplicates(env):
    a, _ = env
    fam = a.src_people.add_group(S, "Family")
    a.src_people.add_contact(S, person("A", groups=(fam,)))
    a.src_people.add_contact(S, person("B"))
    run(env)
    assert run(env) == ContactStats(2, 0, 0, 0, 0, 0)
    calls = [c for c, _ in a.dst_people.calls]
    assert calls.count("create_contact") == 2 and calls.count("create_group") == 1
    assert len(dst_contacts(a)) == 2


def test_edited_source_contact_updated(env):
    a, _ = env
    res = a.src_people.add_contact(S, person("A", phone="1"))
    run(env)
    edit(a, res, person("A", phone="2", groups=(STAR,)))
    assert run(env) == ContactStats(1, 0, 1, 0, 0, 0)
    [c] = dst_contacts(a)
    assert c["phoneNumbers"] == [{"value": "2"}] and label_names(a, c) == {"myContacts", "starred"}
    assert run(env).contacts_updated == 0


def test_stale_etag_retried_once(env):
    a, led = env
    res = a.src_people.add_contact(S, person("A", phone="1"))
    run(env)
    edit(a, res, person("A", phone="2"))
    a.dst_people.fail_next("update_contact", ApiError("http_400", False))
    assert run(env) == ContactStats(1, 0, 1, 0, 0, 0)
    assert [c for c, _ in a.dst_people.calls].count("get_update_meta") == 2
    assert dst_contacts(a)[0]["phoneNumbers"] == [{"value": "2"}]
    edit(a, res, person("A", phone="3"))
    a.dst_people.fail_next("update_contact", ApiError("http_400", False), times=2)
    assert run(env).contacts_failed == 1
    assert led.contact_rows(U)[res][2] == "failed"
    assert run(env).contacts_updated == 1  # a failed row is retried on the next run


def test_source_only_read(env):
    a, _ = env
    fam = a.src_people.add_group(S, "Family")
    a.src_people.add_contact(S, person("A", groups=(fam,)))
    run(env)
    run(env)
    assert {c for c, _ in a.src_people.calls} <= {"list_contacts", "list_groups", "count_other_contacts"}


def test_removed_on_source_counted_never_deleted(env):
    a, led = env
    res = a.src_people.add_contact(S, person("A"))
    a.src_people.add_contact(S, person("B"))
    run(env)
    del a.src_people.contacts[S][res]
    assert run(env) == ContactStats(1, 0, 0, 0, 0, 1)
    assert len(dst_contacts(a)) == 2
    assert led.get_meta(U, "contacts_removed_on_source") == "1"
    for cls in (People, FakePeople):
        assert not [n for n in dir(cls) if "delete" in n.lower() or "remove" in n.lower()]


def test_ledger_holds_no_contact_data(env):
    a, led = env
    fam = a.src_people.add_group(S, "Harambee Board")
    a.src_people.add_contact(S, person("Wanjiru", "wanjiru@x.org", "+254711222333", groups=(fam,)))
    run(env)
    dump = "\n".join(led.db.iterdump())
    for secret in ("Wanjiru", "wanjiru@x.org", "+254711222333", "Harambee Board"):
        assert secret not in dump


def test_interrupted_copy_resumes(env):
    a, led = env
    first = a.src_people.add_contact(S, person("A"))
    a.src_people.add_contact(S, person("B"))
    a.dst_people.fail_next("create_contact", KeyboardInterrupt)
    with pytest.raises(KeyboardInterrupt):
        run(env)
    assert led.contact_rows(U) == {first: ("", a.src_people.contacts[S][first]["etag"], "pending")}
    assert run(env) == ContactStats(2, 2, 0, 0, 0, 0)
    assert len(dst_contacts(a)) == 2
    assert {s for _, _, s in led.contact_rows(U).values()} == {"done"}


def test_empty_contacts(env):
    a, led = env
    assert run(env) == ContactStats(0, 0, 0, 0, 0, 0)
    assert led.get_meta(U, "other_contacts") == "0"
    assert led.get_meta(U, "contacts_removed_on_source") == "0"


def test_profile_and_second_contact_source_fields_not_copied(env):
    a, _ = env
    p = person("Wanjiru", "w@x.org", "+254700000003")
    p["names"] += [{"givenName": "Profile Name", "metadata": {"source": {"type": "PROFILE", "id": "p"}}},
                   {"givenName": "Linked", "metadata": {"source": {"type": "CONTACT", "id": "y"}}}]
    p["emailAddresses"].append({"value": "profile@x.org", "metadata": {"source": {"type": "PROFILE", "id": "p"}}})
    p["names"][0].update(displayName="Wanjiru", displayNameLastFirst="Wanjiru")
    p["phoneNumbers"][0].update(canonicalForm="+254700000003", formattedType="Mobile", type="mobile")
    a.src_people.add_contact(S, p)
    assert run(env).contacts_copied == 1
    [c] = dst_contacts(a)
    assert c["names"] == [{"givenName": "Wanjiru"}]
    assert c["emailAddresses"] == [{"value": "w@x.org", "type": "work"}]
    assert c["phoneNumbers"] == [{"value": "+254700000003", "type": "mobile"}]


def test_contact_gone_while_failed_or_pending_counted_not_failed(env):
    a, led = env
    failed = a.src_people.add_contact(S, person("A"))
    pending = a.src_people.add_contact(S, person("B"))
    a.dst_people.fail_next("create_contact", ApiError("http_500", False))
    run(env)
    led.set_contact(U, pending, "", "e", "pending")  # as left by a crash mid-create
    a.dst_people.contacts[D].clear()
    a.src_people.contacts[S].clear()
    assert run(env) == ContactStats(0, 0, 0, 0, 0, 2)
    assert {s for _, _, s in led.contact_rows(U).values()} == {"gone"}
    assert led.get_meta(U, "contacts_removed_on_source") == "2"
    assert run(env).removed_on_source == 2


def test_contact_deleted_on_destination_is_created_again(env):
    a, led = env
    res = a.src_people.add_contact(S, person("A", phone="1"))
    run(env)
    a.dst_people.contacts[D].clear()  # someone on the destination deleted the copy
    edit(a, res, person("A", phone="2"))
    assert run(env) == ContactStats(1, 1, 0, 0, 0, 0)
    [c] = dst_contacts(a)
    assert c["phoneNumbers"] == [{"value": "2"}] and led.failures(U) == {}
