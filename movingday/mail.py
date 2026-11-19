"""Resumable mail copy. Message bytes live only inside one loop iteration; nothing is written out."""
import hashlib
from dataclasses import dataclass
from email.parser import BytesHeaderParser

from movingday.api import ApiError

SKIP_LABELS = {"SPAM", "TRASH", "DRAFT", "CHAT"}


@dataclass
class MailStats:
    found: int
    copied: int
    skipped: int
    failed: int


def _hash_header(value: str) -> str:
    # ponytail: non-ASCII (RFC-violating) Message-IDs may hash differently from the API's decoded value;
    # recovery then drops and retries them (worst case one duplicate, reported by validation).
    return hashlib.sha256(value.strip().encode("utf-8", "surrogateescape")).hexdigest()


def _headers(raw: bytes):
    return BytesHeaderParser().parsebytes(raw)  # headers only; body is never parsed or kept


def msgid_hash(raw: bytes) -> str:
    value = _headers(raw).get("Message-ID")
    if value is None or not str(value).strip():
        return "raw:" + hashlib.sha256(raw).hexdigest()
    return _hash_header(str(value))


def _label_map(src, dst, src_user, dst_user) -> dict[str, str]:
    have = {l["name"].lower(): l["id"] for l in dst.list_labels(dst_user) if l["type"] == "user"}
    out = {}
    for l in src.list_labels(src_user):
        if l["type"] == "system":
            out[l["id"]] = l["id"]
        else:
            key = l["name"].lower()
            if key not in have:
                have[key] = dst.create_label(dst_user, l["name"])
            out[l["id"]] = have[key]
    return out


def _recover(dst, ledger, user_key, dst_user) -> None:
    rows = ledger.pending(user_key)
    if not rows:
        return
    seen: dict[str, list[tuple[str, str]]] = {}
    for mid, thread in dst.list_messages(dst_user):
        header, _labels = dst.get_header_and_labels(dst_user, mid)
        if header is not None and header.strip():
            seen.setdefault(_hash_header(header), []).append((mid, thread))
    for src_id, h in rows:
        found, n = seen.get(h, []), ledger.done_hash_count(user_key, h)
        if not h.startswith("raw:") and len(found) > n:
            ledger.mark_done(user_key, src_id, *found[n])
        else:
            ledger.drop_pending(user_key, src_id)


def copy_mail(src, dst, ledger, user_key, src_user, dst_user) -> MailStats:
    if ledger.get_meta(user_key, "preexisting") is None:
        ledger.set_meta(user_key, "preexisting", str(dst.profile(dst_user)["messagesTotal"]))
    if ledger.get_meta(user_key, "history_start") is None:
        ledger.set_meta(user_key, "history_start", str(src.profile(src_user)["historyId"]))
    labels = _label_map(src, dst, src_user, dst_user)
    _recover(dst, ledger, user_key, dst_user)

    # ponytail: relies on list order being newest-first; sort by internalDate if the rehearsal shows split threads
    listed = list(reversed(src.list_messages(src_user)))
    done = ledger.done_ids(user_key)
    copied = skipped = failed = 0
    for msg_id, _thread in listed:
        if msg_id in done:
            continue
        try:
            m = src.get_raw(src_user, msg_id)
            if SKIP_LABELS & set(m.label_ids):
                skipped += 1
                continue
            if any(i not in labels for i in m.label_ids):
                raise ApiError("unmapped_label", False)
            h = _headers(m.raw)
            has_date = h.get("Date") is not None
            ledger.mark_pending(user_key, msg_id, m.thread_id, msgid_hash(m.raw))
            new_id, new_thread = dst.insert(
                dst_user, m.raw, [labels[i] for i in m.label_ids],
                ledger.dst_thread(user_key, m.thread_id))
            ledger.mark_done(user_key, msg_id, new_id, new_thread)
            copied += 1
            if not has_date:  # counted immediately so an interrupted run cannot lose it
                n = int(ledger.get_meta(user_key, "no_date_header") or 0)
                ledger.set_meta(user_key, "no_date_header", str(n + 1))
        except ApiError as e:
            ledger.mark_failed(user_key, msg_id, e.reason)
            failed += 1
    if ledger.get_meta(user_key, "no_date_header") is None:
        ledger.set_meta(user_key, "no_date_header", "0")
    ledger.set_meta(user_key, "history_end", str(src.profile(src_user)["historyId"]))
    return MailStats(len(listed), copied, skipped, failed)
