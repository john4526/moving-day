import fcntl
import json
import os

import pytest
from fakes import make_apis

from movingday.api import ApiError
from movingday import cli, mail, runtime
from movingday.drive_copy import FORM

REAL_GET_APIS = runtime.get_apis
PDF = "application/pdf"
DRIVE0 = {"drive_files": 0, "drive_folders": 0, "drive_bytes": 0, "drive_unsupported": 0, "drive_forms": 0,
          "drive_shared_from_outside": 0, "drive_in_others_folders": 0}

RAW = b"Subject: s\r\nMessage-ID: <a@x>\r\nDate: Mon, 1 Jan 2026 00:00:00 +0000\r\n\r\nb\r\n"


@pytest.fixture
def env(tmp_path, monkeypatch):
    (tmp_path / "m.csv").write_text(
        "key,source,destination,batch\n"
        "u001,a@old.org,a@new.org,b1\n"
        "u002,b@old.org,b@new.org,b1\n"
        "u003,c@old.org,c@new.org,b2\n")
    (tmp_path / "c.toml").write_text(
        f'source_key_file="s.json"\ndest_key_file="d.json"\nmapping_file="{tmp_path}/m.csv"\n'
        f'inventory_file="i.json"\nledger_path="{tmp_path}/l.db"\nreport_dir="r"\n'
        'dest_admin="adm@new.org"\ndest_domain="t.new.org"\nmax_existing=1\n')
    apis = make_apis()
    for u in ("a@old.org", "b@old.org", "c@old.org"):
        for _ in range(2):
            apis.src_gmail.add_message(u, RAW + bytes([len(apis.src_gmail.messages.get(u, {}))]), ["INBOX"])
    apis.get_apis_calls = []

    def get_apis(config, source_only=False):
        apis.get_apis_calls.append(source_only)
        return apis
    monkeypatch.setattr(runtime, "get_apis", get_apis)
    return apis, str(tmp_path / "c.toml"), tmp_path


def run(capsys, *argv):
    code = cli.main(list(argv))
    cap = capsys.readouterr()
    return code, [json.loads(l) for l in cap.out.splitlines()], cap


def test_check_reports_sizes_per_user_key(env, capsys):
    _, cfg, _ = env
    code, out, _ = run(capsys, "check", "--config", cfg)
    assert code == 0 and out[0]["ok"] is True
    assert out[0]["users"][0] == {"user": "u001", "src_messages": 2, "own_calendars": 0, "delegated_calendars": 0,
                                  "team_calendars": 0, "contacts": 0, "other_contacts": 0, "dst_existing": 0,
                                  **DRIVE0, "ok": True}
    assert [u["user"] for u in out[0]["users"]] == ["u001", "u002", "u003"]


def _scan_calendars(apis):
    G1, G2 = "team@group.calendar.google.com", "solo@group.calendar.google.com"
    cal = apis.src_cal
    cal.add_calendar("a@old.org", "a@old.org", primary=True)
    cal.add_calendar("a@old.org", G1)                                        # co-managed with b
    cal.add_calendar("a@old.org", G2)                                        # a's own, nobody else
    cal.add_calendar("a@old.org", "boss@old.org")                            # delegated
    cal.add_calendar("a@old.org", "room@resource.calendar.google.com")       # resource: neither
    cal.add_calendar("a@old.org", "viewer@group.calendar.google.com", access_role="reader")
    cal.add_calendar("b@old.org", "b@old.org", primary=True)
    cal.add_calendar("b@old.org", G1)
    cal.add_calendar("b@old.org", G2, access_role="writer")                  # not owner: not b's


def test_check_classifies_calendars_and_summarises(env, capsys):
    apis, cfg, _ = env
    _scan_calendars(apis)
    code, out, cap = run(capsys, "check", "--config", cfg)
    u = {x["user"]: x for x in out[0]["users"]}
    assert code == 0
    assert (u["u001"]["own_calendars"], u["u001"]["delegated_calendars"], u["u001"]["team_calendars"]) == (3, 1, 1)
    assert (u["u002"]["own_calendars"], u["u002"]["delegated_calendars"], u["u002"]["team_calendars"]) == (2, 0, 1)
    assert (u["u003"]["own_calendars"], u["u003"]["delegated_calendars"], u["u003"]["team_calendars"]) == (0, 0, 0)
    assert out[0]["summary"] == {"users": 3, "total_messages": 6, "largest_mailbox": 2,
                                 "accounts_with_delegated": 1, "accounts_with_team_calendars": 2, "total_contacts": 0,
                                 "total_drive_bytes": 0, "largest_drive_bytes": 0, "failed": 0}
    assert "@" not in cap.out


def test_check_source_only_never_touches_destination(env, capsys):
    apis, cfg, _ = env
    code, out, _ = run(capsys, "check", "--source-only", "--config", cfg)
    assert code == 0 and apis.get_apis_calls == [True]
    assert out[0]["users"][0] == {"user": "u001", "src_messages": 2, "own_calendars": 0,
                                  "delegated_calendars": 0, "team_calendars": 0, "contacts": 0,
                                  "other_contacts": 0, **DRIVE0, "ok": True}
    assert apis.dst_gmail.calls == [] and apis.dst_cal.calls == [] and apis.dst_people.calls == []
    assert {c[0] for c in apis.src_gmail.calls + apis.src_cal.calls} == {"profile", "list_calendars"}
    assert {c[0] for c in apis.src_people.calls} == {"list_contacts", "count_other_contacts"}


def test_check_counts_contacts_and_other_contacts(env, capsys):
    apis, cfg, _ = env
    for n in range(3):
        apis.src_people.add_contact("a@old.org", {"names": [{"givenName": f"n{n}"}]})
    apis.src_people.add_contact("b@old.org", {"names": [{"givenName": "x"}]})
    apis.src_people.set_other_contacts("a@old.org", 7)
    code, out, cap = run(capsys, "check", "--source-only", "--config", cfg)
    u = {x["user"]: x for x in out[0]["users"]}
    assert code == 0 and (u["u001"]["contacts"], u["u001"]["other_contacts"]) == (3, 7)
    assert (u["u002"]["contacts"], u["u002"]["other_contacts"]) == (1, 0)
    assert out[0]["summary"]["total_contacts"] == 4


def test_check_contacts_failure_marks_user_failed(env, capsys):
    apis, cfg, _ = env
    apis.src_people.fail_next("list_contacts", ApiError("http_403", False))
    code, out, _ = run(capsys, "check", "--source-only", "--config", cfg)
    assert code == 1 and out[0]["users"][0] == {"user": "u001", "ok": False, "reason": "http_403"}


def test_check_failures_count_in_summary(env, capsys):
    apis, cfg, _ = env
    apis.src_cal.fail_next("list_calendars", ApiError("http_500", False))
    code, out, _ = run(capsys, "check", "--source-only", "--config", cfg)
    assert code == 1 and out[0]["summary"]["failed"] == 1 and out[0]["summary"]["users"] == 3
    assert out[0]["users"][0]["ok"] is False and "own_calendars" not in out[0]["users"][0]
    assert out[0]["summary"]["total_messages"] == 4


def test_check_does_not_create_or_write_the_ledger(env, capsys):
    _, cfg, tmp = env
    run(capsys, "check", "--config", cfg)
    assert not (tmp / "l.db").exists()
    run(capsys, "copy", "--batch", "b1", "--mail", "--config", cfg)
    before = (tmp / "l.db").read_bytes()
    run(capsys, "check", "--config", cfg)
    assert (tmp / "l.db").read_bytes() == before


def test_check_profile_failure_continues(env, capsys):
    apis, cfg, _ = env
    from movingday.api import ApiError
    apis.src_gmail.fail_next("profile", ApiError("http_500", False))
    code, out, _ = run(capsys, "check", "--config", cfg)
    assert code == 1 and out[0]["ok"] is False
    assert out[0]["users"][0]["ok"] is False and "reason" in out[0]["users"][0]
    assert out[0]["users"][1]["ok"] is True


def test_check_bad_mapping_exits_2_with_reason_and_row(env, capsys):
    _, cfg, tmp = env
    (tmp / "m.csv").write_text("key,source,destination,batch\nxx,a@o,b@n,b1\n")
    code, out, _ = run(capsys, "check", "--config", cfg)
    assert code == 2
    assert out[0] == {"command": "check", "ok": False, "reason": "bad_key", "row": 2}


def test_copy_batch_copies_only_that_batch(env, capsys):
    apis, cfg, _ = env
    code, out, _ = run(capsys, "copy", "--batch", "b1", "--config", cfg)
    assert code == 0 and out[0]["ok"] is True and isinstance(out[0]["seconds"], int)
    assert {u["user"] for u in out[0]["users"]} == {"u001", "u002"}
    assert out[0]["users"][0]["copied"] == 2
    assert len(apis.dst_gmail.messages["a@new.org"]) == 2
    assert "c@new.org" not in apis.dst_gmail.messages


def test_copy_mail_only_skips_calendars(env, capsys):
    apis, cfg, _ = env
    code, out, _ = run(capsys, "copy", "--batch", "b2", "--mail", "--config", cfg)
    assert code == 0 and "calendars" not in out[0]["users"][0]
    assert not [c for c in apis.src_cal.calls if c[0] == "list_calendars"]


def _contact(apis, user="a@old.org", name="n"):
    apis.src_people.add_contact(user, {"names": [{"givenName": name, "metadata": {"source": {"type": "CONTACT"}}}]})


def test_copy_default_includes_contacts_and_merges_stats(env, capsys):
    apis, cfg, _ = env
    _contact(apis)
    code, out, _ = run(capsys, "copy", "--batch", "b1", "--config", cfg)
    u = out[0]["users"][0]
    assert code == 0 and u["copied"] == 2 and u["contacts_found"] == 1 and u["contacts_copied"] == 1
    assert (u["contacts_updated"], u["contacts_failed"], u["labels_created"], u["removed_on_source"]) == (0, 0, 0, 0)
    assert len(apis.dst_people.contacts["a@new.org"]) == 1


def test_copy_contacts_only_skips_mail_and_calendars(env, capsys):
    apis, cfg, _ = env
    _contact(apis)
    code, out, _ = run(capsys, "copy", "--batch", "b1", "--contacts", "--config", cfg)
    assert code == 0 and "copied" not in out[0]["users"][0] and out[0]["users"][0]["contacts_copied"] == 1
    assert not [c for c in apis.src_cal.calls if c[0] == "list_calendars"]
    assert apis.dst_gmail.messages == {} or not apis.dst_gmail.messages.get("a@new.org")


def test_copy_mail_flag_alone_skips_contacts(env, capsys):
    apis, cfg, _ = env
    code, out, _ = run(capsys, "copy", "--batch", "b2", "--mail", "--config", cfg)
    assert code == 0 and "contacts_copied" not in out[0]["users"][0] and apis.src_people.calls == []


def test_failed_contact_makes_copy_not_ok(env, capsys):
    apis, cfg, _ = env
    _contact(apis)
    apis.dst_people.fail_next("create_contact", ApiError("http_500", False))
    code, out, _ = run(capsys, "copy", "--batch", "b1", "--contacts", "--config", cfg)
    u = out[0]["users"][0]
    assert code == 1 and u["ok"] is False and u["contacts_failed"] == 1
    code, out, _ = run(capsys, "copy", "--batch", "b1", "--contacts", "--allow-existing", "u001", "--config", cfg)
    assert code == 0 and out[0]["users"][0]["contacts_copied"] == 1  # the failed row is retried


def test_copy_refuses_when_destination_not_empty(env, capsys):
    apis, cfg, _ = env
    for _ in range(3):
        apis.dst_gmail.add_message("b@new.org", RAW, ["INBOX"])
    code, out, _ = run(capsys, "copy", "--batch", "b1", "--config", cfg)
    assert code == 2 and out[0]["reason"] == "destination_not_empty" and out[0]["ok"] is False
    assert [u["user"] for u in out[0]["users"]] == ["u002"]
    assert "a@new.org" not in apis.dst_gmail.messages  # nothing copied for anyone


def test_allow_existing_overrides_for_named_user(env, capsys):
    apis, cfg, _ = env
    for _ in range(3):
        apis.dst_gmail.add_message("b@new.org", RAW, ["INBOX"])
    code, out, _ = run(capsys, "copy", "--batch", "b1", "--allow-existing", "u002", "--config", cfg)
    assert code == 0 and len(apis.dst_gmail.messages["a@new.org"]) == 2


def test_second_copy_refused_while_first_holds_lock(env, capsys):
    apis, cfg, tmp = env
    with open(tmp / "l.db.lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        code, out, _ = run(capsys, "copy", "--batch", "b1", "--config", cfg)
    assert code == 2 and out[0]["reason"] == "already_running"
    assert "a@new.org" not in apis.dst_gmail.messages


def test_status_reads_ledger_without_calling_google(env, capsys):
    apis, cfg, _ = env
    run(capsys, "copy", "--batch", "b1", "--config", cfg)
    for fake in (apis.src_gmail, apis.dst_gmail, apis.src_cal, apis.dst_cal, apis.dst_dir):
        fake.calls.clear()
    code, out, _ = run(capsys, "status", "--batch", "b1", "--config", cfg)
    assert code == 0
    assert out[0]["users"][0] == {"user": "u001", "copied": 2, "failed": 0, "pending": 0, "failures": {}}
    assert not any(f.calls for f in (apis.src_gmail, apis.dst_gmail, apis.src_cal, apis.dst_cal, apis.dst_dir))


def test_unexpected_error_emits_reason_only(env, capsys, monkeypatch):
    apis, cfg, _ = env

    def boom(*a, **k):
        raise RuntimeError("SECRET")
    monkeypatch.setattr(mail, "copy_mail", boom)
    code, out, cap = run(capsys, "copy", "--batch", "b1", "--config", cfg)
    assert code == 1 and "SECRET" not in cap.out + cap.err
    assert out[0]["users"][0] == {"user": "u001", "ok": False, "reason": "internal_RuntimeError"}


def test_one_user_failing_leaves_others_ok(env, capsys, monkeypatch):
    apis, cfg, _ = env
    real = mail.copy_mail

    def sometimes(src, dst, ledger, key, su, du):
        if key == "u001":
            raise RuntimeError("SECRET")
        return real(src, dst, ledger, key, su, du)
    monkeypatch.setattr(mail, "copy_mail", sometimes)
    code, out, cap = run(capsys, "copy", "--batch", "b1", "--config", cfg)
    assert code == 1 and "SECRET" not in cap.out + cap.err
    by = {u["user"]: u for u in out[0]["users"]}
    assert by["u001"]["ok"] is False and by["u002"]["ok"] is True


def test_unsupported_event_type_is_not_a_failure(env, capsys):
    apis, cfg, _ = env
    apis.src_cal.add_calendar("c@old.org", "c@old.org", primary=True)
    apis.dst_cal.add_calendar("c@new.org", "c@new.org", primary=True)
    apis.src_cal.add_event("c@old.org", "c@old.org", {"summary": "x", "eventType": "focusTime"})
    apis.dst_cal.fail_next("import_event", ApiError("http_400", False))
    code, out, _ = run(capsys, "copy", "--batch", "b2", "--config", cfg)
    assert code == 0 and out[0]["users"][0]["ok"] is True and out[0]["users"][0]["events_failed"] == 1


def test_real_mail_failure_is_not_ok(env, capsys):
    apis, cfg, _ = env
    apis.dst_gmail.fail_next("insert", ApiError("http_500", False))
    code, out, _ = run(capsys, "copy", "--batch", "b2", "--config", cfg)
    assert code == 1 and out[0]["users"][0]["ok"] is False and out[0]["users"][0]["failed"] == 1


def test_rerun_of_same_batch_is_not_refused(env, capsys):
    apis, cfg, tmp = env  # max_existing=1, each user copies 2 messages
    assert run(capsys, "copy", "--batch", "b1", "--config", cfg)[0] == 0
    code, out, _ = run(capsys, "copy", "--batch", "b1", "--config", cfg)
    assert code == 0 and out[0]["ok"] is True


def test_keyboard_interrupt_emits_interrupted(env, capsys, monkeypatch):
    _, cfg, _ = env
    monkeypatch.setattr(runtime, "get_apis", lambda c, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    code, out, cap = run(capsys, "check", "--config", cfg)
    assert code == 1 and out[0]["reason"] == "interrupted" and cap.err == ""


def test_top_level_error_emits_reason(env, capsys, monkeypatch):
    _, cfg, _ = env
    monkeypatch.setattr(runtime, "get_apis", lambda c, **k: (_ for _ in ()).throw(ValueError("SECRET")))
    code, out, cap = run(capsys, "check", "--config", cfg)
    assert code == 1 and "SECRET" not in cap.out + cap.err
    assert out[0] == {"command": "check", "ok": False, "reason": "internal_ValueError"}


def test_no_config_and_bad_arguments(env, capsys, monkeypatch):
    monkeypatch.delenv("MOVINGDAY_CONFIG", raising=False)
    code, out, _ = run(capsys, "check")
    assert code == 2 and out[0]["reason"] == "no_config"
    for argv in (["copy", "--bogus"], ["--help"], ["copy", "-h"]):
        code, out, cap = run(capsys, *argv)
        assert code == 2 and out == [{"ok": False, "reason": "bad_arguments"}] and cap.err == ""


def test_config_from_environment(env, capsys, monkeypatch):
    _, cfg, _ = env
    monkeypatch.setenv("MOVINGDAY_CONFIG", cfg)
    code, out, _ = run(capsys, "status")
    assert code == 0 and len(out[0]["users"]) == 3


def test_output_never_contains_addresses(env, capsys):
    _, cfg, _ = env
    for argv in (["check"], ["copy", "--batch", "b1"], ["status"]):
        code, out, cap = run(capsys, *argv, "--config", cfg)
        assert "@" not in cap.out and cap.err == ""


@pytest.mark.parametrize("name", ["c.toml", "m.csv"])
def test_untrusted_config_or_mapping_exits_2(env, capsys, name):
    _, cfg, tmp = env
    os.chmod(tmp / name, 0o666)
    code, out, _ = run(capsys, "status", "--config", cfg)
    assert code == 2 and out == [{"command": "status", "ok": False, "reason": "untrusted_file"}]


def test_copy_drive_flag_only_drive(env, capsys):
    apis, cfg, _ = env
    apis.src_drive.add_file("a@old.org", PDF, content=b"x")
    apis.src_drive.add_file("a@old.org", FORM)
    _contact(apis)
    code, out, cap = run(capsys, "copy", "--batch", "b1", "--drive", "--config", cfg)
    u = out[0]["users"][0]
    assert code == 0 and u["ok"] is True and u["drive_files_copied"] == 1 and u["drive_forms"] == 1
    assert u["drive_files_updated"] == 0 and "copied" not in u and "contacts_copied" not in u
    assert apis.src_cal.calls == [] and apis.src_people.calls == []
    assert not apis.dst_gmail.messages.get("a@new.org") and "@" not in cap.out


def test_copy_no_flags_runs_all_four_parts(env, capsys):
    apis, cfg, _ = env
    apis.src_drive.add_file("a@old.org", PDF, content=b"x")
    _contact(apis)
    code, out, _ = run(capsys, "copy", "--batch", "b1", "--config", cfg)
    u = out[0]["users"][0]
    assert code == 0 and u["copied"] == 2 and "calendars" in u and u["contacts_copied"] == 1
    assert u["drive_files_copied"] == 1


def test_failed_drive_file_makes_copy_not_ok(env, capsys):
    apis, cfg, _ = env
    apis.src_drive.add_file("a@old.org", PDF, content=b"x")
    apis.dst_drive.fail_next("upload", ApiError("http_500", False))
    code, out, _ = run(capsys, "copy", "--batch", "b1", "--drive", "--config", cfg)
    assert code == 1 and out[0]["users"][0]["ok"] is False and out[0]["users"][0]["drive_files_failed"] == 1


def test_drive_trash_held_makes_copy_not_ok(env, capsys):
    apis, cfg, _ = env
    sid = apis.src_drive.add_file("a@old.org", PDF, content=b"x")
    run(capsys, "copy", "--batch", "b1", "--drive", "--config", cfg)
    apis.src_drive.files[sid]["trashed"] = True
    code, out, _ = run(capsys, "copy", "--batch", "b1", "--drive", "--config", cfg)
    u = out[0]["users"][0]
    assert code == 1 and u["ok"] is False and u["reason"] == "too_many_gone" and u["drive_trash_held"] == 1


def test_check_reports_drive_counts_and_summary(env, capsys):
    apis, cfg, _ = env
    d = apis.src_drive
    top = d.add_folder("a@old.org")
    d.add_file("a@old.org", PDF, top, b"12345")
    d.add_file("a@old.org", PDF, content=b"123")
    d.add_file("a@old.org", FORM)
    d.add_file("a@old.org", "application/vnd.google-apps.drawing")
    theirs = d.add_folder("b@old.org")
    d.add_file("a@old.org", PDF, theirs, b"1")                           # in someone else's folder
    for owner in ("b@old.org", "boss@parent.org", "boss@parent.org"):    # opened by a, owned by others
        d.share(owner, d.add_file(owner, PDF), {"role": "reader", "type": "user", "emailAddress": "a@old.org"})
    code, out, cap = run(capsys, "check", "--config", cfg)
    u = {x["user"]: x for x in out[0]["users"]}
    assert code == 0
    assert {k: v for k, v in u["u001"].items() if k.startswith("drive_")} == {
        "drive_files": 5, "drive_folders": 1, "drive_bytes": 9, "drive_unsupported": 1, "drive_forms": 1,
        "drive_shared_from_outside": 2, "drive_in_others_folders": 1}
    assert (u["u002"]["drive_folders"], u["u002"]["drive_files"], u["u002"]["drive_bytes"]) == (1, 1, 0)
    assert (out[0]["summary"]["total_drive_bytes"], out[0]["summary"]["largest_drive_bytes"]) == (9, 9)
    assert "@" not in cap.out


def test_check_source_only_never_builds_dst_drive(env, capsys, monkeypatch):
    apis, cfg, tmp = env
    apis.src_drive.add_file("a@old.org", PDF, content=b"x")
    code, out, _ = run(capsys, "check", "--source-only", "--config", cfg)
    assert code == 0 and apis.get_apis_calls == [True] and apis.dst_drive.calls == []
    assert out[0]["users"][0]["drive_files"] == 1
    assert {c[0] for c in apis.src_drive.calls} <= {"root_id", "list_owned", "list_shared_owners"}
    built = []
    for name in ("Gmail", "Calendar", "People", "Drive", "Directory"):
        monkeypatch.setattr(runtime, name, lambda key, *a, _n=name: built.append((_n, key)))
    from movingday.config import load_config
    got = REAL_GET_APIS(load_config(cfg), source_only=True)
    assert got.dst_drive is None and ("Drive", "s.json") in built and all(k == "s.json" for _, k in built)
