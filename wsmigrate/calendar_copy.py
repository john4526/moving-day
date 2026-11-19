"""Calendar and sharing copy. Event content lives only in memory; the ledger gets ids, timestamps, states."""
from dataclasses import dataclass

from wsmigrate.api import ApiError

EVENT_FIELDS = ("summary", "description", "location", "start", "end", "recurrence", "iCalUID",
                "attendees", "organizer", "reminders", "transparency", "visibility", "status",
                "sequence", "originalStartTime", "eventType")
NONE = {"send_updates": "none"}


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


def _sharing(src, dst, mapping, src_user, dst_user, src_cal, dst_cal, st) -> int:
    """Returns the number of rules whose insert failed."""
    failed = 0
    have = {(r["role"], r["scope"]["type"], r["scope"].get("value", "").lower())
            for r in dst.list_acl(dst_user, dst_cal)}
    for r in src.list_acl(src_user, src_cal):
        typ, val = r["scope"]["type"], r["scope"].get("value")
        if val and val.lower() == src_user.lower():
            continue
        if typ == "user":
            mapped = mapping.dest_for(val)
            if mapped:
                val = mapped
            if val.lower() == dst_user.lower():
                continue
            if not mapped:
                st.outside_shares += 1
        sig = (r["role"], typ, (val or "").lower())
        if sig in have:
            continue
        scope = {"type": typ, **({"value": val} if val else {})}
        try:
            dst.insert_acl(dst_user, dst_cal, {"role": r["role"], "scope": scope}, send_notifications=False)
        except ApiError:
            failed += 1
            continue
        have.add(sig)
        st.acl_copied += 1
    return failed


def copy_calendars(src, dst, ledger, mapping, user_key, src_user, dst_user) -> CalStats:
    st = CalStats()
    cmap = ledger.cal_map(user_key)
    acl_failed = 0
    for cal in src.list_calendars(src_user):
        if cal["accessRole"] != "owner":
            continue
        st.calendars += 1
        key = "primary" if cal["primary"] else cal["id"]
        if key not in cmap:
            cmap[key] = "primary" if cal["primary"] else dst.create_calendar(dst_user, cal["summary"], cal["timeZone"])
            ledger.set_cal(user_key, key, cmap[key])
        _events(src, dst, ledger, user_key, src_user, dst_user, key, cal["id"], cmap[key], st)
        acl_failed += _sharing(src, dst, mapping, src_user, dst_user, cal["id"], cmap[key], st)
    ledger.set_meta(user_key, "outside_shares", str(st.outside_shares))
    ledger.set_meta(user_key, "acl_failed", str(acl_failed))
    return st
