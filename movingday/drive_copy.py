"""Drive copy and top-up. File content passes through in chunks; the ledger gets ids, versions, times,
checksums, hashed share signatures, states and reason codes. The only removals are on destination files the
tool created: trashing one, and removing a permission the tool added."""
import hashlib
from dataclasses import dataclass
from typing import Iterable

from movingday.api import FOLDER, SRC_KEY, ApiError
from movingday.calendar_copy import translate_rules

GOOGLE = "application/vnd.google-apps."
FORM = GOOGLE + "form"
EXPORTS = {
    GOOGLE + "document": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    GOOGLE + "spreadsheet": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    GOOGLE + "presentation": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
_VALUE = {"user": "emailAddress", "group": "emailAddress", "domain": "domain"}


@dataclass
class DriveStats:
    folders: int = 0
    files_copied: int = 0
    files_updated: int = 0
    files_moved: int = 0
    files_trashed: int = 0
    files_failed: int = 0
    kept_as_office: int = 0
    unsupported: int = 0
    forms: int = 0
    unplaced: int = 0
    trash_held: int = 0  # 1: too many copies would go to the trash this run, so none did (too_many_gone)
    shares_added: int = 0
    shares_removed: int = 0
    shares_failed: int = 0
    outside_shares: int = 0


def drive_rules(perms: list[dict]) -> list[dict]:
    """Drive permissions in translate_rules' shape; the owner and entries without their address are dropped.
    allowFileDiscovery rides along as "discoverable" (translate_rules ignores it)."""
    out = []
    for p in perms:
        key = _VALUE.get(p["type"])
        if p["role"] == "owner" or (key and not p.get(key)):
            continue
        scope = {"type": p["type"], **({"value": p[key]} if key else {})}
        out.append({"role": p["role"], "scope": scope, "discoverable": bool(p.get("allowFileDiscovery"))})
    return out


def share_sig(role, typ, val, discoverable: bool) -> str:
    """Hashed, so the ledger never holds the address."""
    return hashlib.sha256(f"{role}|{typ}|{(val or '').lower()}|{int(bool(discoverable))}".encode()).hexdigest()


def share_fp(sigs: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(sorted(sigs)).encode()).hexdigest()


def _wanted(src, mapping, src_user, dst_user, dest_domain, sid, st) -> dict[str, dict]:
    wanted = {}
    for rule in drive_rules(src.list_permissions(src_user, sid)):
        for role, typ, val, outside in translate_rules([rule], mapping, src_user, dst_user, dest_domain):
            st.outside_shares += outside
            disc = rule["discoverable"] if typ in ("anyone", "domain") else False
            body = {"type": typ, "role": role}
            if typ in _VALUE:
                body[_VALUE[typ]] = val
            if typ in ("anyone", "domain"):
                body["allowFileDiscovery"] = disc
            wanted[share_sig(role, typ, val, disc)] = body
    return wanted


def _sharing(src, dst, ledger, mapping, user_key, src_user, dst_user, dest_domain, sid, did, old_fp, st):
    wanted = _wanted(src, mapping, src_user, dst_user, dest_domain, sid, st)
    fp = share_fp(wanted)
    have = ledger.drive_shares(user_key, sid)  # local, so failed adds and removals are retried every run
    if fp == old_fp and wanted.keys() == have.keys():
        return
    failed = 0
    for sig in wanted.keys() - have.keys():
        try:
            ledger.add_drive_share(user_key, sid, sig, dst.add_permission(dst_user, did, wanted[sig]))
            st.shares_added += 1
        except ApiError:
            failed += 1
    for sig in have.keys() - wanted.keys():
        try:
            dst.remove_permission(dst_user, did, have[sig])
        except ApiError as e:
            if e.reason not in ("http_404", "http_410"):  # already gone counts as removed
                failed += 1
                continue
        ledger.drop_drive_share(user_key, sid, sig)
        st.shares_removed += 1
    st.shares_failed += failed
    ledger.set_drive(user_key, sid, share_fp=fp)


def _parents_first(folders: list[dict]) -> list[dict]:
    by_id, out, seen = {f["id"]: f for f in folders}, [], set()

    def visit(f):
        if f["id"] in seen:
            return
        seen.add(f["id"])
        parent = by_id.get((f.get("parents") or [None])[0])
        if parent:
            visit(parent)
        out.append(f)

    for f in folders:
        visit(f)
    return out


def _close_gone(dst, ledger, user_key, dst_user, dst_root, rows, live, created, st, allow_trash=False):
    """Trash the copy of every item no longer owned. Runs after the item loop, so files moved out of a gone
    folder on the source are moved first. Trashing a folder trashes its contents, so this user's copies still
    inside a gone folder are first moved to the top (placed=0; re-placed if the folder comes back under
    another owner); a folder whose move-out failed is not trashed this run. If the source lists nothing while
    copies exist, or more than half of this user's copies are gone, nothing is trashed and a person decides;
    allow_trash (that person's go-ahead, for this run only) lifts the hold."""
    done = [sid for sid, row in rows.items() if row["state"] == "done"]
    gone_done = sum(sid not in live for sid in done)
    held = not allow_trash and bool(done) and (not live or gone_done * 2 > len(done))
    ledger.set_meta(user_key, "drive_trash_held", "too_many_gone" if held else "no")
    if held:
        st.trash_held = 1
        return
    gone = {}
    for sid, row in rows.items():
        if sid in live or row["state"] == "trashed":
            continue
        try:
            did = row["dst_id"] if row["state"] in ("done", "failed") else ""
            if not did and row["state"] in ("pending", "failed"):  # a crash after upload leaves a tagged copy
                did = dst.find_by_src(dst_user, sid) or ""
        except ApiError as e:
            ledger.set_drive(user_key, sid, reason=e.reason)
            st.files_failed += 1
            continue
        gone[sid] = did
    gone_dirs = {did: sid for sid, did in gone.items() if did and rows[sid]["kind"] == "folder"}
    blocked = set()

    def block(dir_did):  # a trashed folder takes its contents along, so every gone folder above is held too
        while dir_did in gone_dirs and gone_dirs[dir_did] not in blocked:
            blocked.add(gone_dirs[dir_did])
            dir_did = (created.get(dir_did, {}).get("parents") or [""])[0]

    for csid, crow in ledger.drive_rows(user_key).items():
        cdid = crow["dst_id"]
        old = (created.get(cdid, {}).get("parents") or [""])[0]
        if csid not in live or not cdid or crow["state"] not in ("done", "failed") or old not in gone_dirs:
            continue
        try:
            dst.update_meta(dst_user, cdid, {}, add_parents=dst_root, remove_parents=old)
        except ApiError:
            block(old)
            continue
        created[cdid]["parents"] = [dst_root]
        ledger.set_drive(user_key, csid, placed=0)
        st.files_moved += 1
        st.unplaced += 1
    for sid, did in gone.items():
        try:
            if sid in blocked:
                raise ApiError("contents_not_moved", False)
            if did:
                dst.trash(dst_user, did)
                st.files_trashed += 1
        except ApiError as e:
            if e.reason not in ("http_404", "http_410"):
                ledger.set_drive(user_key, sid, reason=e.reason)  # state kept, so the next run retries
                st.files_failed += 1
                continue
        ledger.set_drive(user_key, sid, dst_id=did, state="trashed")


def copy_drive(src, dst, ledger, mapping, user_key, src_user, dst_user, dest_domain,
               allow_trash: bool = False) -> DriveStats:
    st = DriveStats()
    items = src.list_owned(src_user)
    folders = [i for i in items if i["mimeType"] == FOLDER]
    files = [i for i in items if i["mimeType"] != FOLDER]
    rows = ledger.drive_rows(user_key)
    src_root, dst_root = src.root_id(src_user), dst.root_id(dst_user)
    created = {f["id"]: f for f in dst.list_created(dst_user)}  # current parents and type of every copy

    def target(parent) -> tuple[str, int]:
        if not parent or parent == src_root:
            return dst_root, 1
        found = ledger.dst_folder(parent)
        return (found, 1) if found else (dst_root, 0)

    def move(did, dst_parent):
        """Move a copy to dst_parent from its current destination parent, when they differ."""
        old_parent = (created.get(did, {}).get("parents") or [""])[0]  # "": no parent, so add only
        if old_parent != dst_parent:
            dst.update_meta(dst_user, did, {}, add_parents=dst_parent, remove_parents=old_parent)
            created.setdefault(did, {})["parents"] = [dst_parent]
            st.files_moved += 1

    for item in _parents_first(folders) + files:
        sid, mime = item["id"], item["mimeType"]
        row = rows.get(sid)
        if mime.startswith(GOOGLE) and mime != FOLDER and mime not in EXPORTS:
            ledger.set_drive(user_key, sid, state="unsupported", reason=None)
            if mime == FORM:
                st.forms += 1
            else:
                st.unsupported += 1
            continue
        folder = mime == FOLDER
        office = EXPORTS.get(mime)
        out_mime = office or mime

        def content():
            return src.export(src_user, sid, office) if office else src.download(src_user, sid)

        src_parent = (item.get("parents") or [""])[0]
        dst_parent, placed = target(src_parent)
        try:
            have = bool(row and row["dst_id"] and row["state"] in ("done", "failed"))
            # a copy deleted or trashed on the destination is copied again; both the listing and a direct
            # lookup must miss it, so a short listing alone cannot cause a duplicate
            if have and row["dst_id"] not in created and not dst.find_by_src(dst_user, sid):
                have = False
            if have:
                did, kind, old_fp = row["dst_id"], row["kind"], row["share_fp"]
                if item.get("version") != row["src_version"]:
                    # uploaded files have a checksum; Google files only a modified time
                    changed = (item["md5Checksum"] != row["md5"] if "md5Checksum" in item
                               else item.get("modifiedTime") != row["src_modified"])
                    if changed and not folder:
                        dst.update_content(dst_user, did, content(), out_mime)
                    dst.update_meta(dst_user, did, {k: item[k] for k in ("name", "description", "starred",
                                                                          "modifiedTime") if k in item})
                    st.files_updated += not folder
                move(did, dst_parent)
            else:
                kind, old_fp, did = ("folder" if folder else "file"), "", None
                ledger.set_drive(user_key, sid, dst_id="", kind=kind, state="pending")
                if row:  # a crash mid-copy, or a file restored on the source; old shares were on an old copy
                    for sig in ledger.drive_shares(user_key, sid):
                        ledger.drop_drive_share(user_key, sid, sig)
                    did = dst.find_by_src(dst_user, sid)
                    if did:
                        ledger.set_drive(user_key, sid, dst_id=did)  # so a failed move can't orphan the copy
                        move(did, dst_parent)
                    if did and office and created.get(did, {}).get("mimeType") == office:
                        kind = "office"  # adopted copy whose conversion was refused
                        st.kept_as_office += 1
                if not did:
                    meta = {k: item[k] for k in ("name", "description", "starred", "modifiedTime", "createdTime")
                            if k in item}
                    meta.update(parents=[dst_parent], appProperties={SRC_KEY: sid})
                    if folder:
                        did = dst.create_folder(dst_user, meta)
                    else:
                        did, converted = dst.upload(dst_user, {**meta, "mimeType": mime}, content, out_mime,
                                                    bool(office))
                        if office and not converted:
                            kind = "office"
                            st.kept_as_office += 1
                created.setdefault(did, {"id": did, "parents": [dst_parent]})
                if folder:
                    st.folders += 1
                else:
                    st.files_copied += 1
            ledger.set_drive(user_key, sid, dst_id=did, kind=kind, src_version=item.get("version", ""),
                             src_modified=item.get("modifiedTime", ""), md5=item.get("md5Checksum", ""),
                             src_parent=src_parent, placed=placed, state="done", reason=None)
            st.unplaced += not placed
            _sharing(src, dst, ledger, mapping, user_key, src_user, dst_user, dest_domain, sid, did, old_fp, st)
        except ApiError as e:
            ledger.set_drive(user_key, sid, state="failed", reason=e.reason)
            st.files_failed += 1

    _close_gone(dst, ledger, user_key, dst_user, dst_root, rows, {i["id"] for i in items}, created, st,
                allow_trash)
    final = ledger.drive_rows(user_key).values()
    for name, value in (("drive_unsupported", st.unsupported), ("drive_forms", st.forms),
                        ("drive_kept_as_office", sum(r["kind"] == "office" and r["state"] in ("done", "failed")
                                                     and r["dst_id"] != "" for r in final)),
                        ("drive_unplaced", st.unplaced), ("drive_outside_shares", st.outside_shares),
                        ("drive_shares_failed", st.shares_failed),
                        ("drive_trashed", sum(r["state"] == "trashed" and r["dst_id"] != "" for r in final))):
        ledger.set_meta(user_key, name, str(value))
    return st
