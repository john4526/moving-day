import hashlib
import os

import pytest
from fakes import make_apis

from movingday.api import CHUNK, SRC_KEY, ApiError
from movingday.drive_copy import EXPORTS, FOLDER, FORM, copy_drive
from movingday.ledger import Ledger
from movingday.mapping import Mapping, User

U, S, D = "u001", "s@x.org", "d@y.org"
U2, S2, D2 = "u002", "pal@x.org", "pal@y.org"
MAP = Mapping([User(U, S, D, "b"), User(U2, S2, D2, "b")])
DOC = "application/vnd.google-apps.document"
SHEET = "application/vnd.google-apps.spreadsheet"
SLIDES = "application/vnd.google-apps.presentation"
PDF = "application/pdf"
WRITES = {"create_folder", "upload", "update_content", "update_meta", "add_permission", "remove_permission", "trash"}


@pytest.fixture
def env(tmp_path):
    a = make_apis()
    led = Ledger(str(tmp_path / "l.db"))
    yield a, led
    led.close()


def run(env, who=(U, S, D)):
    a, led = env
    return copy_drive(a.src_drive, a.dst_drive, led, MAP, *who, "y.org")


def dst_of(env, sid, user_key=U):
    a, led = env
    return a.dst_drive.files[led.drive_rows(user_key)[sid]["dst_id"]]


def called(fake, *names):
    return [c for c in fake.calls if c[0] in names]


def test_folder_tree_recreated_parents_first(env):
    a, _ = env
    top = a.src_drive.add_folder(S, name="top")
    mid = a.src_drive.add_folder(S, top, name="mid")
    leaf = a.src_drive.add_folder(S, mid, name="leaf")
    f = a.src_drive.add_file(S, PDF, leaf, b"x")
    a.src_drive.files = dict(reversed(a.src_drive.files.items()))  # child first
    st = run(env)
    assert st.folders == 3 and st.files_copied == 1 and st.unplaced == 0
    assert dst_of(env, top)["parents"] == [f"root-{D}"]
    assert dst_of(env, mid)["parents"] == [dst_of(env, top)["id"]]
    assert dst_of(env, leaf)["parents"] == [dst_of(env, mid)["id"]]
    assert dst_of(env, f)["parents"] == [dst_of(env, leaf)["id"]]
    assert dst_of(env, leaf)["mimeType"] == FOLDER


def test_google_files_exported_and_converted(env):
    a, _ = env
    ids = {m: a.src_drive.add_file(S, m, content=b"office bytes") for m in (DOC, SHEET, SLIDES)}
    st = run(env)
    assert st.files_copied == 3 and st.kept_as_office == 0
    exports = {c[1]["file_id"]: c[1]["mime"] for c in called(a.src_drive, "export")}
    assert exports == {sid: EXPORTS[m] for m, sid in ids.items()}
    assert EXPORTS[DOC].endswith("wordprocessingml.document")
    for m, sid in ids.items():
        assert dst_of(env, sid)["mimeType"] == m
    assert all(c[1]["convert"] is True for c in called(a.dst_drive, "upload"))


def test_uploads_byte_identical(env):
    a, _ = env
    data = os.urandom(2 * CHUNK + 5)
    sid = a.src_drive.add_file(S, PDF, content=data)
    run(env)
    d = dst_of(env, sid)
    assert d["content"] == data and d["md5Checksum"] == hashlib.md5(data).hexdigest()
    assert called(a.dst_drive, "upload")[0][1]["convert"] is False


def test_refused_conversion_keeps_office_file(env):
    a, led = env
    sid = a.src_drive.add_file(S, DOC, content=b"d")
    a.dst_drive.refuse_conversion(sid)
    st = run(env)
    assert st.kept_as_office == 1 and st.files_copied == 1 and st.files_failed == 0
    assert dst_of(env, sid)["mimeType"] == EXPORTS[DOC]
    assert led.drive_rows(U)[sid]["state"] == "done"
    run(env)
    assert led.get_meta(U, "drive_kept_as_office") == "1"


def test_forms_and_unsupported_counted_not_failed(env):
    a, led = env
    form = a.src_drive.add_file(S, FORM)
    a.src_drive.add_file(S, "application/vnd.google-apps.drawing")
    a.src_drive.add_file(S, "application/vnd.google-apps.shortcut")
    for _ in range(2):
        st = run(env)
        assert (st.forms, st.unsupported, st.files_failed, st.files_copied) == (1, 2, 0, 0)
    assert not called(a.dst_drive, *WRITES) and not called(a.src_drive, "export", "download")
    assert led.drive_rows(U)[form]["state"] == "unsupported"
    assert (led.get_meta(U, "drive_forms"), led.get_meta(U, "drive_unsupported")) == ("1", "2")


def test_metadata_kept(env):
    a, _ = env
    sid = a.src_drive.add_file(S, PDF, content=b"x", name="n", description="desc", starred=True,
                               createdTime="2020-01-01T00:00:00.000Z", modifiedTime="2021-01-01T00:00:00.000Z")
    run(env)
    meta = called(a.dst_drive, "upload")[0][1]["meta"]
    assert {k: meta[k] for k in ("name", "description", "starred", "modifiedTime", "createdTime")} == {
        "name": "n", "description": "desc", "starred": True, "modifiedTime": "2021-01-01T00:00:00.000Z",
        "createdTime": "2020-01-01T00:00:00.000Z"}
    assert meta["appProperties"] == {SRC_KEY: sid} and meta["parents"] == [f"root-{D}"]


def test_same_name_files_both_copied(env):
    a, _ = env
    one = a.src_drive.add_file(S, PDF, content=b"1", name="same")
    two = a.src_drive.add_file(S, PDF, content=b"2", name="same")
    assert run(env).files_copied == 2
    assert dst_of(env, one)["content"] == b"1" and dst_of(env, two)["content"] == b"2"


def test_empty_files_copied(env):
    a, _ = env
    sid = a.src_drive.add_file(S, "text/plain", content=b"")
    assert run(env).files_copied == 1
    assert dst_of(env, sid)["content"] == b"" and dst_of(env, sid)["md5Checksum"] == hashlib.md5(b"").hexdigest()


def test_sharing_translated_notifications_off(env):
    a, led = env
    sid = a.src_drive.add_file(S, PDF, content=b"x")
    for p in ({"role": "owner", "type": "user", "emailAddress": S},
              {"role": "writer", "type": "user", "emailAddress": S2},
              {"role": "reader", "type": "group", "emailAddress": "team@x.org"},
              {"role": "commenter", "type": "domain", "domain": "x.org", "allowFileDiscovery": True},
              {"role": "reader", "type": "anyone", "allowFileDiscovery": False},
              {"role": "reader", "type": "user", "emailAddress": "out@else.com"}):
        a.src_drive.share(S, sid, p)
    st = run(env)
    got = sorted((p["role"], p["type"], p.get("emailAddress") or p.get("domain"), p.get("allowFileDiscovery"))
                 for p in dst_of(env, sid)["perms"].values())
    assert got == sorted([("writer", "user", D2, None), ("reader", "group", "team@y.org", None),
                          ("commenter", "domain", "y.org", True), ("reader", "anyone", None, False),
                          ("reader", "user", "out@else.com", None)])
    assert (st.shares_added, st.outside_shares, st.shares_failed) == (5, 1, 0)
    assert len(led.drive_shares(U, sid)) == 5
    # sendNotificationEmail=False is set inside Drive.add_permission (tests/test_api.py); nothing else shares
    assert {c[0] for c in a.dst_drive.calls} <= {"root_id", "list_created", "upload", "add_permission"}


def test_share_to_own_destination_skipped(env):
    a, _ = env
    sid = a.src_drive.add_file(S, PDF, content=b"x")
    a.src_drive.share(S, sid, {"role": "writer", "type": "user", "emailAddress": D})
    st = run(env)
    assert st.shares_added == 0 and not called(a.dst_drive, "add_permission")


def test_rerun_copies_nothing(env):
    a, _ = env
    folder = a.src_drive.add_folder(S)
    sid = a.src_drive.add_file(S, DOC, folder, b"d")
    a.src_drive.share(S, sid, {"role": "reader", "type": "user", "emailAddress": S2})
    run(env)
    a.dst_drive.calls.clear()
    st = run(env)
    assert not called(a.dst_drive, *WRITES)
    assert (st.folders, st.files_copied, st.files_updated, st.files_moved, st.shares_added) == (0, 0, 0, 0, 0)


def test_crash_after_upload_no_duplicate(env):
    a, led = env
    sid = a.src_drive.add_file(S, PDF, content=b"x")
    did, _ = a.dst_drive.upload(D, {"appProperties": {SRC_KEY: sid}}, lambda: iter([b"x"]), PDF, False)
    led.set_drive(U, sid, state="pending")
    a.dst_drive.calls.clear()
    run(env)
    assert not called(a.dst_drive, "upload")
    assert led.drive_rows(U)[sid]["dst_id"] == did and led.drive_rows(U)[sid]["state"] == "done"
    assert sum(f["owner"] == D for f in a.dst_drive.files.values()) == 1


def test_topup_content_change_updates_in_place(env):
    a, _ = env
    pdf = a.src_drive.add_file(S, PDF, content=b"v1")
    doc = a.src_drive.add_file(S, DOC, content=b"d1")
    run(env)
    before = dst_of(env, pdf)["id"], dst_of(env, doc)["id"]
    a.src_drive.edit(pdf, content=b"v2")
    a.src_drive.edit(doc, content=b"d2")
    st = run(env)
    assert st.files_updated == 2 and st.files_copied == 0
    assert (dst_of(env, pdf)["id"], dst_of(env, doc)["id"]) == before
    assert dst_of(env, pdf)["content"] == b"v2" and dst_of(env, doc)["content"] == b"d2"


def test_topup_rename_updates_meta(env):
    a, _ = env
    sid = a.src_drive.add_file(S, PDF, content=b"x", name="old")
    run(env)
    a.src_drive.edit(sid, name="new")
    run(env)
    assert dst_of(env, sid)["name"] == "new"
    assert not called(a.dst_drive, "update_content")


def test_topup_move_between_folders(env):
    a, _ = env
    f1, f2 = a.src_drive.add_folder(S), a.src_drive.add_folder(S)
    sid = a.src_drive.add_file(S, PDF, f1, b"x")
    run(env)
    a.src_drive.edit(sid, parent=f2)
    st = run(env)
    assert st.files_moved == 1
    assert dst_of(env, sid)["parents"] == [dst_of(env, f2)["id"]]


def test_unplaced_file_placed_on_rerun(env):
    a, led = env
    theirs = a.src_drive.add_folder(S2)
    sid = a.src_drive.add_file(S, PDF, theirs, b"x")
    st = run(env)
    assert st.unplaced == 1 and dst_of(env, sid)["parents"] == [f"root-{D}"]
    assert led.drive_rows(U)[sid]["placed"] == 0 and led.get_meta(U, "drive_unplaced") == "1"
    run(env, (U2, S2, D2))
    st = run(env)
    assert st.unplaced == 0 and st.files_moved == 1
    assert dst_of(env, sid)["parents"] == [dst_of(env, theirs, U2)["id"]]
    assert led.drive_rows(U)[sid]["placed"] == 1


def test_topup_share_added_and_removed(env):
    a, led = env
    sid = a.src_drive.add_file(S, PDF, content=b"x")
    pal = a.src_drive.share(S, sid, {"role": "writer", "type": "user", "emailAddress": S2})
    run(env)
    did = dst_of(env, sid)["id"]
    own = a.dst_drive.share(D, did, {"role": "reader", "type": "user", "emailAddress": "kept@y.org"})
    del a.src_drive.files[sid]["perms"][pal]
    a.src_drive.share(S, sid, {"role": "reader", "type": "domain", "domain": "x.org"})
    st = run(env)
    assert (st.shares_added, st.shares_removed, st.shares_failed) == (1, 1, 0)
    perms = a.dst_drive.files[did]["perms"]
    assert own in perms
    assert sorted(p.get("emailAddress") or p.get("domain") for p in perms.values()) == ["kept@y.org", "y.org"]
    assert len(led.drive_shares(U, sid)) == 1


def test_source_deleted_goes_to_destination_trash(env):
    a, led = env
    sid = a.src_drive.add_file(S, PDF, content=b"x")
    run(env)
    a.src_drive.files[sid]["trashed"] = True
    st = run(env)
    assert st.files_trashed == 1 and dst_of(env, sid)["trashed"] is True
    assert led.drive_rows(U)[sid]["state"] == "trashed" and led.get_meta(U, "drive_trashed") == "1"
    assert called(a.dst_drive, "trash")[0][1]["user"] == D


def test_owner_change_trashes_then_other_user_copies(env):
    a, led = env
    sid = a.src_drive.add_file(S, PDF, content=b"x")
    run(env)
    old = dst_of(env, sid)["id"]
    a.src_drive.set_owner(sid, S2)
    assert run(env).files_trashed == 1 and a.dst_drive.files[old]["trashed"] is True
    assert run(env, (U2, S2, D2)).files_copied == 1
    new = dst_of(env, sid, U2)
    assert new["id"] != old and new["owner"] == D2 and not new["trashed"]


def test_export_too_large_recorded_failed(env):
    a, led = env
    sid = a.src_drive.add_file(S, DOC, content=b"d")
    ok = a.src_drive.add_file(S, PDF, content=b"x")
    a.src_drive.fail_export(sid, "export_too_large")
    st = run(env)
    assert st.files_failed == 1 and st.files_copied == 1
    row = led.drive_rows(U)[sid]
    assert (row["state"], row["reason"]) == ("failed", "export_too_large")
    assert led.drive_rows(U)[ok]["state"] == "done"


def test_source_only_read(env):
    a, _ = env
    folder = a.src_drive.add_folder(S)
    sid = a.src_drive.add_file(S, PDF, folder, b"x")
    a.src_drive.add_file(S, DOC, folder, b"d")
    a.src_drive.share(S, sid, {"role": "reader", "type": "user", "emailAddress": S2})
    run(env)
    a.src_drive.edit(sid, content=b"y", name="z")
    run(env)
    a.src_drive.files[sid]["trashed"] = True
    run(env)
    assert {c[0] for c in a.src_drive.calls} <= {"root_id", "list_owned", "download", "export", "list_permissions"}


def test_ledger_holds_no_names(env):
    a, led = env
    folder = a.src_drive.add_folder(S, name="Secret Folder Name")
    sid = a.src_drive.add_file(S, PDF, folder, b"x", name="Hidden File Name", description="Private Description")
    a.src_drive.add_file(S, DOC, folder, b"d", name="Doc Title Here")
    a.src_drive.share(S, sid, {"role": "reader", "type": "user", "emailAddress": S2})
    run(env)
    dump = "\n".join(led.db.iterdump())
    assert "drive_items" in dump
    for secret in ("Secret Folder Name", "Hidden File Name", "Private Description", "Doc Title Here", S2, D2):
        assert secret not in dump


def test_restored_on_source_copied_again_with_sharing(env):
    a, led = env
    sid = a.src_drive.add_file(S, PDF, content=b"x")
    a.src_drive.share(S, sid, {"role": "reader", "type": "user", "emailAddress": S2})
    run(env)
    old = dst_of(env, sid)["id"]
    a.src_drive.files[sid]["trashed"] = True
    run(env)
    a.src_drive.files[sid]["trashed"] = False
    st = run(env)
    new = dst_of(env, sid)
    assert st.files_copied == 1 and new["id"] != old and not new["trashed"]
    assert [p["emailAddress"] for p in new["perms"].values()] == [D2]
    assert list(led.drive_shares(U, sid).values()) == list(new["perms"])


def test_folders_given_away_keep_child_copies_live(env):
    a, led = env
    f = a.src_drive.add_folder(S)
    g = a.src_drive.add_folder(S, f)
    sid = a.src_drive.add_file(S, PDF, g, b"x")
    run(env)
    a.src_drive.set_owner(f, S2)
    a.src_drive.set_owner(g, S2)
    st = run(env)
    assert st.files_trashed == 2 and dst_of(env, f)["trashed"] and dst_of(env, g)["trashed"]
    assert not dst_of(env, sid)["trashed"] and dst_of(env, sid)["parents"] == [f"root-{D}"]
    assert led.drive_rows(U)[sid]["placed"] == 0 and st.unplaced == 1
    run(env, (U2, S2, D2))
    st = run(env)
    assert st.files_moved == 1 and not dst_of(env, sid)["trashed"]
    assert dst_of(env, sid)["parents"] == [dst_of(env, g, U2)["id"]]


def test_move_takes_old_parent_from_destination(env):
    a, led = env
    f1, f2 = a.src_drive.add_folder(S), a.src_drive.add_folder(S)
    sid = a.src_drive.add_file(S, PDF, f1, b"x")
    run(env)
    a.src_drive.set_owner(f1, "out@else.com")
    led.set_drive(U, f1, state="trashed")  # the old folder's row no longer resolves
    a.src_drive.edit(sid, parent=f2)
    run(env)
    assert dst_of(env, sid)["parents"] == [dst_of(env, f2)["id"]]


def test_gone_pending_row_orphan_trashed(env):
    a, led = env
    sid = a.src_drive.add_file(S, PDF, content=b"x")
    did, _ = a.dst_drive.upload(D, {"appProperties": {SRC_KEY: sid}}, lambda: iter([b"x"]), PDF, False)
    led.set_drive(U, sid, state="pending")
    a.src_drive.files[sid]["trashed"] = True
    st = run(env)
    assert st.files_trashed == 1 and a.dst_drive.files[did]["trashed"]
    row = led.drive_rows(U)[sid]
    assert (row["state"], row["dst_id"]) == ("trashed", did) and led.get_meta(U, "drive_trashed") == "1"


def test_adopted_refused_conversion_counted_as_office(env):
    a, led = env
    sid = a.src_drive.add_file(S, DOC, content=b"d")
    a.dst_drive.refuse_conversion(sid)
    a.dst_drive.upload(D, {"appProperties": {SRC_KEY: sid}, "mimeType": DOC}, lambda: iter([b"d"]),
                       EXPORTS[DOC], True)
    led.set_drive(U, sid, state="pending")
    run(env)
    assert led.drive_rows(U)[sid]["kind"] == "office" and led.get_meta(U, "drive_kept_as_office") == "1"
    a.src_drive.edit(sid, content=b"d2")
    a.dst_drive.fail_next("update_content", ApiError("http_500", False))
    run(env)
    assert led.drive_rows(U)[sid]["state"] == "failed" and led.get_meta(U, "drive_kept_as_office") == "1"


def test_gone_folder_not_trashed_when_move_out_fails(env):
    a, led = env
    f = a.src_drive.add_folder(S)
    sid = a.src_drive.add_file(S, PDF, f, b"x")
    run(env)
    a.src_drive.set_owner(f, S2)
    a.dst_drive.fail_next("update_meta", ApiError("http_500", False))
    st = run(env)
    assert st.files_failed == 1 and not dst_of(env, f)["trashed"] and not dst_of(env, sid)["trashed"]
    assert (led.drive_rows(U)[f]["state"], led.drive_rows(U)[f]["reason"]) == ("done", "contents_not_moved")
    assert run(env).files_trashed == 1 and not dst_of(env, sid)["trashed"]
