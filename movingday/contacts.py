"""Contacts and contact labels copy. Contact data lives only in memory; the ledger gets resource names,
etags, states and reason codes. Nothing is ever deleted, on either side."""
from dataclasses import dataclass

from movingday.api import PERSON_FIELDS, ApiError

MY_CONTACTS = "contactGroups/myContacts"
KEPT_SYSTEM_GROUPS = (MY_CONTACTS, "contactGroups/starred")


@dataclass
class ContactStats:
    found: int = 0
    contacts_copied: int = 0
    contacts_updated: int = 0
    contacts_failed: int = 0
    labels_created: int = 0
    removed_on_source: int = 0


def _groups(src, dst, ledger, user_key, src_user, dst_user, st) -> dict[str, str]:
    """Source group -> destination group: user labels matched by name (any letter case), created when absent;
    myContacts and starred kept as they are. Other system groups are absent, so dropped."""
    gmap = ledger.group_map(user_key)
    by_name = {g["name"].lower(): g["resourceName"] for g in dst.list_groups(dst_user)
               if g["groupType"] == "USER_CONTACT_GROUP"}
    for g in src.list_groups(src_user):
        if g["groupType"] != "USER_CONTACT_GROUP" or g["resourceName"] in gmap:
            continue
        dst_group = by_name.get(g["name"].lower())
        if dst_group is None:
            dst_group = by_name[g["name"].lower()] = dst.create_group(dst_user, g["name"])
            st.labels_created += 1
        gmap[g["resourceName"]] = dst_group
        ledger.set_group(user_key, g["resourceName"], dst_group)
    return {**gmap, **{g: g for g in KEPT_SYSTEM_GROUPS}}


def _body(person, gmap) -> dict:
    body = {f: [{k: v for k, v in item.items() if k not in ("metadata", "source")} for item in person[f]]
            for f in PERSON_FIELDS if f in person and f != "memberships"}
    groups = [gmap[g] for g in (m.get("contactGroupMembership", {}).get("contactGroupResourceName")
                                for m in person.get("memberships", [])) if g in gmap]
    body["memberships"] = [{"contactGroupMembership": {"contactGroupResourceName": g}}
                           for g in groups or [MY_CONTACTS]]
    return body


def _update(dst, dst_user, dst_resource, body):
    try:
        dst.update_contact(dst_user, dst_resource, dst.get_etag(dst_user, dst_resource), body)
    except ApiError as e:
        if e.reason != "http_400":  # stale etag: re-fetch once and retry
            raise
        dst.update_contact(dst_user, dst_resource, dst.get_etag(dst_user, dst_resource), body)


def copy_contacts(src, dst, ledger, user_key, src_user, dst_user) -> ContactStats:
    st = ContactStats()
    gmap = _groups(src, dst, ledger, user_key, src_user, dst_user, st)
    people = src.list_contacts(src_user)
    st.found = len(people)
    rows = ledger.contact_rows(user_key)
    live = {p["resourceName"] for p in people}
    st.removed_on_source = sum(sid not in live and state == "done" for sid, (_, _, state) in rows.items())
    for p in people:
        sid, etag = p["resourceName"], p["etag"]
        did, old_etag, state = rows.get(sid, ("", None, None))
        if state == "done" and old_etag == etag:
            continue
        body = _body(p, gmap)
        try:
            if did:
                _update(dst, dst_user, did, body)
                st.contacts_updated += 1
            else:
                # ponytail: a pending row (crash mid-create) is overwritten and the contact created again;
                # contacts have no stable dedupe key. Worst case one duplicate per crash, which compare reports.
                ledger.set_contact(user_key, sid, "", etag, "pending")
                did, _ = dst.create_contact(dst_user, body)
                st.contacts_copied += 1
            ledger.set_contact(user_key, sid, did, etag, "done")
        except ApiError as e:
            ledger.set_contact(user_key, sid, did, etag, "failed", e.reason)
            st.contacts_failed += 1
    ledger.set_meta(user_key, "other_contacts", str(src.count_other_contacts(src_user)))
    ledger.set_meta(user_key, "contacts_removed_on_source", str(st.removed_on_source))
    return st
