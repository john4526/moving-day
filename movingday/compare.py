"""Source-versus-destination comparisons. Results are counts only: no address, subject or content."""
import csv
from collections import Counter
from dataclasses import dataclass

from movingday.calendar_copy import select_calendars, translate_rules
from movingday.mail import SKIP_LABELS, _hash_header


@dataclass
class MailResult:
    src_total: int
    dst_total: int
    missing: int
    duplicates: int
    extra: int
    label_mismatches: int
    threads_src: int
    threads_dst: int


def _scan(gmail, user):
    """(Counter of header hashes, headerless count, per-label counts, thread count, total), SKIP_LABELS excluded."""
    names = {l["id"]: l["id"] if l["type"] == "system" else l["name"].lower() for l in gmail.list_labels(user)}
    hashes, labels, threads, headerless, total = Counter(), Counter(), set(), 0, 0
    for mid, thread in gmail.list_messages(user):
        header, ids = gmail.get_header_and_labels(user, mid)
        if SKIP_LABELS & set(ids):
            continue
        total += 1
        threads.add(thread)
        labels.update({names.get(i, i) for i in ids})
        if header is not None and header.strip():
            hashes[_hash_header(header)] += 1
        else:
            headerless += 1
    return hashes, headerless, labels, len(threads), total, set(names.values())


def compare_mail(src, dst, src_user, dst_user, preexisting: int) -> MailResult:
    sh, sn, sl, st, stotal, snames = _scan(src, src_user)
    dh, dn, dl, dt, dtotal, _ = _scan(dst, dst_user)
    both = sh.keys() | dh.keys()
    missing = sum(max(sh[h] - dh[h], 0) for h in both) + max(sn - dn, 0)
    duplicates = sum(max(dh[h] - sh[h], 0) for h in both if sh[h])
    extra = sum(dh[h] for h in both if not sh[h]) + max(dn - sn, 0)
    mismatches = sum(dl[k] < sl[k] or dl[k] - sl[k] > preexisting for k in snames - SKIP_LABELS)
    return MailResult(stotal, dtotal, missing, duplicates, extra, mismatches, st, dt)


@dataclass
class CalResult:
    calendars_src: int = 0
    calendars_dst: int = 0
    events_src: int = 0
    events_dst: int = 0
    series_src: int = 0
    series_dst: int = 0
    attendees_src: int = 0
    attendees_dst: int = 0
    acl_missing: int = 0
    events_missing: int = 0


def _tally(events):
    return len(events), sum("recurrence" in e for e in events), sum(len(e.get("attendees", ())) for e in events)


def compare_calendar(src, dst, ledger, mapping, user_key, src_user, dst_user, dest_domain) -> CalResult:
    r = CalResult()
    cmap = ledger.cal_map(user_key)
    dst_list = dst.list_calendars(dst_user)
    dst_cals = {c["id"] for c in dst_list}
    dst_has_primary = any(c["primary"] for c in dst_list)
    for cal in select_calendars(src, ledger, user_key, src_user, claim=False)[0]:
        key = "primary" if cal["primary"] else cal["id"]
        r.calendars_src += 1
        skipped = ledger.failed_event_ids(user_key, key, "unsupported_event_type")
        ev = [e for e in src.list_events(src_user, cal["id"]) if e["id"] not in skipped]
        r.events_src, r.series_src, r.attendees_src = (
            a + b for a, b in zip((r.events_src, r.series_src, r.attendees_src), _tally(ev)))
        wanted = {(role, typ, (val or "").lower())
                  for role, typ, val, _ in translate_rules(
                      src.list_acl(src_user, cal["id"]), mapping, src_user, dst_user, dest_domain)}
        target = cmap.get(key)
        if target is None or not (dst_has_primary if target == "primary" else target in dst_cals):
            r.acl_missing += len(wanted)
            r.events_missing += len(ev)
            continue
        r.calendars_dst += 1
        dst_events = dst.list_events(dst_user, target)
        r.events_dst, r.series_dst, r.attendees_dst = (
            a + b for a, b in zip((r.events_dst, r.series_dst, r.attendees_dst), _tally(dst_events)))
        present, rows = {e["id"] for e in dst_events}, ledger.event_rows(user_key, key)
        r.events_missing += sum(
            rows.get(e["id"], ("", "", ""))[2] != "done" or rows[e["id"]][0] not in present for e in ev)
        have = {(x["role"], x["scope"]["type"], x["scope"].get("value", "").lower())
                for x in dst.list_acl(dst_user, target)}
        r.acl_missing += len(wanted - have)
    return r


class InventoryError(Exception):
    def __init__(self, reason: str, row: int):
        super().__init__(f"{reason} (row {row})")
        self.reason = reason
        self.row = row


def load_inventory(path) -> list[tuple[str, str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        header = [(h or "").strip().lower() for h in reader.fieldnames or []]
        if header != ["type", "address", "parent"]:
            raise InventoryError("missing_column", 1)
        reader.fieldnames = header
        rows = []
        for n, x in enumerate(reader, start=2):
            row = tuple((x.get(c) or "").strip().lower() for c in header)
            if row[0] not in ("user", "group", "member", "alias"):
                raise InventoryError("bad_type", n)
            rows.append(row)
    return rows


def compare_inventory(rows, directory, dest_domain) -> dict:
    def dest(addr):
        return f"{addr.split('@')[0]}@{dest_domain}".lower()

    users, groups, aliases = directory.list_users(), directory.list_groups(), directory.list_aliases()
    out = dict.fromkeys(("missing_users", "missing_groups", "missing_members", "missing_aliases"), 0)
    for typ, addr, parent in rows:
        if typ == "user":
            out["missing_users"] += dest(addr) not in users
        elif typ == "group":
            out["missing_groups"] += dest(addr) not in groups
        elif typ == "member":
            out["missing_members"] += dest(addr) not in groups.get(dest(parent), ())
        elif typ == "alias":
            out["missing_aliases"] += aliases.get(dest(addr)) != dest(parent)
    return out
