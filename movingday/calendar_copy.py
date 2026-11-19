"""Calendar and sharing copy. Event content lives only in memory; the ledger gets ids, timestamps, states."""
from dataclasses import dataclass

from movingday.api import ApiError

EVENT_FIELDS = ("summary", "description", "location", "start", "end", "recurrence", "iCalUID",
                "attendees", "organizer", "reminders", "transparency", "visibility", "status",
                "sequence", "originalStartTime", "eventType")
NONE = {"send_updates": "none"}
GROUP_SUFFIX = "@group.calendar.google.com"
RESOURCE_SUFFIX = "@resource.calendar.google.com"


@dataclass
class CalStats:
    calendars: int = 0
    events_copied: int = 0
    events_updated: int = 0
    events_removed: int = 0
    events_failed: int = 0
    acl_copied: int = 0
    outside_shares: int = 0


def _events(src, dst, ledger, user_key, src_user, dst_user, key, src_cal, dst_cal, st):
    events = src.list_events(src_user, src_cal)
    rows = ledger.event_rows(user_key, key)
    live = {e["id"] for e in events}
    for sid, (did, _, _) in list(rows.items()):
        if sid in live:
            continue
        try:
            if did:
                dst.delete_event(dst_user, dst_cal, did, **NONE)
        except ApiError as e:
            if e.reason not in ("http_404", "http_410"):
                ledger.set_event(user_key, key, sid, did, rows[sid][1], "failed", e.reason)
                st.events_failed += 1
                continue
        ledger.drop_event(user_key, key, sid)
        del rows[sid]
        st.events_removed += 1
    # series first, then exceptions (which need the destination series id)
    for ev in sorted(events, key=lambda e: "recurringEventId" in e):
        sid, upd = ev["id"], ev.get("updated", "")
        did, old_upd, state = rows.get(sid, ("", None, None))
        if state == "done" and old_upd == upd:
            continue
        body = {k: ev[k] for k in EVENT_FIELDS if k in ev}
        try:
            if "recurringEventId" in ev:
                parent = rows.get(ev["recurringEventId"], ("",))[0]
                if not parent:
                    raise ApiError("series_missing", False)
                body["recurringEventId"] = parent
            if did:
                dst.update_event(dst_user, dst_cal, did, body, **NONE)
                st.events_updated += 1
            else:
                did = dst.import_event(dst_user, dst_cal, body, **NONE)
                st.events_copied += 1
            rows[sid] = (did, upd, "done")
            ledger.set_event(user_key, key, sid, did, upd, "done")
        except ApiError as e:
            reason = e.reason
            if ev.get("eventType", "default") != "default" and reason != "series_missing":
                reason = "unsupported_event_type"
            rows[sid] = (did, upd, "failed")
            ledger.set_event(user_key, key, sid, did, upd, "failed", reason)
            st.events_failed += 1


def translate_rules(src_rules, mapping, src_user, dst_user, dest_domain):
    """Yield (role, scope type, value or None, outside) for each source rule that is to be copied.
    Users map through the mapping; our source domain(s) and groups on them move to dest_domain."""
    src_domains = {u.source.split("@")[1] for u in mapping.users}
    for r in src_rules:
        typ, val = r["scope"]["type"], r["scope"].get("value")
        if val and val.lower() == src_user.lower():
            continue
        outside = False
        if typ == "user":
            mapped = mapping.dest_for(val)
            if mapped:
                val = mapped
            if val.lower() == dst_user.lower():
                continue
            outside = not mapped
        elif typ == "domain":
            outside = val.lower() not in src_domains
            val = val if outside else dest_domain
        elif typ == "group":
            local, _, domain = val.partition("@")
            outside = domain.lower() not in src_domains
            val = val if outside else f"{local}@{dest_domain}"
        yield r["role"], typ, val, outside


def _sharing(src, dst, mapping, src_user, dst_user, dest_domain, src_cal, dst_cal, st) -> int:
    """Returns the number of rules whose insert failed."""
    failed = 0
    have = {(r["role"], r["scope"]["type"], r["scope"].get("value", "").lower())
            for r in dst.list_acl(dst_user, dst_cal)}
    for role, typ, val, outside in translate_rules(
            src.list_acl(src_user, src_cal), mapping, src_user, dst_user, dest_domain):
        st.outside_shares += outside
        sig = (role, typ, (val or "").lower())
        if sig in have:
            continue
        scope = {"type": typ, **({"value": val} if val else {})}
        try:
            dst.insert_acl(dst_user, dst_cal, {"role": role, "scope": scope}, send_notifications=False)
        except ApiError:
            failed += 1
            continue
        have.add(sig)
        st.acl_copied += 1
    return failed


def select_calendars(src, ledger, user_key, src_user, claim: bool = True):
    """(calendars this user copies, owner-role calendars skipped). Copied: the primary calendar, and each
    group calendar the user owns or manages, by the one user who claims it first. "owner" is also reported
    for other people's calendars shared with "manage sharing"; those and resource calendars are skipped.
    claim=False only reads the claims (for comparisons): an unclaimed calendar counts as this user's."""
    out, skipped = [], 0
    for cal in src.list_calendars(src_user):
        if cal["primary"]:
            out.append(cal)
        elif cal["accessRole"] == "owner":
            if cal["id"].endswith(GROUP_SUFFIX) and (
                    ledger.claim_calendar(cal["id"], user_key) if claim
                    else ledger.calendar_claimant(cal["id"]) in (None, user_key)):
                out.append(cal)
            else:
                skipped += 1
    return out, skipped


def copy_calendars(src, dst, ledger, mapping, user_key, src_user, dst_user, dest_domain) -> CalStats:
    st = CalStats()
    cmap = ledger.cal_map(user_key)
    acl_failed = 0
    cals, skipped = select_calendars(src, ledger, user_key, src_user)
    for cal in cals:
        st.calendars += 1
        key = "primary" if cal["primary"] else cal["id"]
        if key not in cmap:
            cmap[key] = "primary" if cal["primary"] else dst.create_calendar(dst_user, cal["summary"], cal["timeZone"])
            ledger.set_cal(user_key, key, cmap[key])
        _events(src, dst, ledger, user_key, src_user, dst_user, key, cal["id"], cmap[key], st)
        acl_failed += _sharing(src, dst, mapping, src_user, dst_user, dest_domain, cal["id"], cmap[key], st)
    ledger.set_meta(user_key, "outside_shares", str(st.outside_shares))
    ledger.set_meta(user_key, "acl_failed", str(acl_failed))
    ledger.set_meta(user_key, "shared_calendars_skipped", str(skipped))
    return st
