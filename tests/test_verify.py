import json

import pytest
from fakes import make_apis

from wsmigrate import cli, runtime

VALIDATION_TESTS = {
    "test_mapping_valid", "test_inventory_present", "test_mail_none_missing", "test_mail_no_duplicates",
    "test_mail_no_foreign_messages", "test_mail_label_counts", "test_calendar_counts",
    "test_calendar_sharing", "test_calendar_events_missing", "test_nothing_left_failed", "test_freeze_held",
}


def raw(n, extra=b""):
    return (extra + b"Subject: s\r\nMessage-ID: <%d@x>\r\nDate: Mon, 1 Jan 2026 00:00:00 +0000\r\n\r\nb\r\n" % n)


@pytest.fixture
def env(tmp_path, monkeypatch):
    (tmp_path / "m.csv").write_text(
        "key,source,destination,batch\n"
        "u001,a@old.org,a@new.org,b1\n"
        "u002,b@old.org,b@new.org,b1\n"
        "u003,c@old.org,c@new.org,b2\n")
    (tmp_path / "i.csv").write_text("type,address,parent\nuser,a@old.org,\n")
    (tmp_path / "c.toml").write_text(
        f'source_key_file="s.json"\ndest_key_file="d.json"\nmapping_file="{tmp_path}/m.csv"\n'
        f'inventory_file="{tmp_path}/i.csv"\nledger_path="{tmp_path}/l.db"\nreport_dir="{tmp_path}/r"\n'
        'dest_admin="adm@new.org"\ndest_domain="t.new.org"\nmax_existing=1\n')
    apis = make_apis()
    apis.dst_dir.users = {"a@t.new.org"}
    for n, u in enumerate(("a", "b")):
        apis.src_gmail.add_message(f"{u}@old.org", raw(2 * n), ["INBOX"])
        apis.src_gmail.add_message(f"{u}@old.org", raw(2 * n + 1), ["INBOX"])
        apis.src_cal.add_calendar(f"{u}@old.org", f"{u}@old.org", primary=True)
        apis.dst_cal.add_calendar(f"{u}@new.org", f"{u}@new.org", primary=True)
        apis.src_cal.add_event(f"{u}@old.org", f"{u}@old.org",
                               {"summary": "e", "attendees": [{"email": "x@else.org"}]})
    monkeypatch.setattr(runtime, "get_apis", lambda config: apis)
    return apis, str(tmp_path / "c.toml"), tmp_path


def run(capfd, *argv):
    code = cli.main(list(argv))
    cap = capfd.readouterr()
    return code, [json.loads(l) for l in cap.out.splitlines()], cap


def check(out, name, user=None):
    return [c for c in out[0]["checks"] if c["name"] == name and c.get("user") == user]


def test_verify_passes_after_clean_copy(env, capfd):
    _, cfg, tmp = env
    assert run(capfd, "copy", "--batch", "b1", "--config", cfg)[0] == 0
    code, out, cap = run(capfd, "verify", "--batch", "b1", "--config", cfg)
    assert code == 0 and len(out) == 1 and cap.err == ""  # nothing but the one JSON line
    assert out[0]["command"] == "verify" and out[0]["batch"] == "b1"
    assert out[0]["passed"] is True and out[0]["failed_checks"] == 0
    assert (tmp / "r" / out[0]["report"]).is_file() and "/" not in out[0]["report"]
    # isolated: only the validation suite ran, never anything under tests/
    assert {c["name"] for c in out[0]["checks"]} == VALIDATION_TESTS
    assert check(out, "test_mapping_valid") == [{"name": "test_mapping_valid", "ok": True}]
    assert check(out, "test_mail_none_missing", "u002") == [
        {"name": "test_mail_none_missing", "user": "u002", "ok": True}]


def test_verify_fails_and_names_check_when_message_missing(env, capfd):
    apis, cfg, _ = env
    run(capfd, "copy", "--batch", "b1", "--config", cfg)
    apis.dst_gmail.messages["a@new.org"].popitem()
    code, out, cap = run(capfd, "verify", "--batch", "b1", "--config", cfg)
    assert code == 1 and len(out) == 1 and cap.err == ""
    assert out[0]["passed"] is False and out[0]["failed_checks"] >= 1
    assert check(out, "test_mail_none_missing", "u001")[0]["ok"] is False
    assert check(out, "test_mail_none_missing", "u002")[0]["ok"] is True


def test_calendar_events_missing_check_fails_when_event_gone(env, capfd):
    apis, cfg, _ = env
    run(capfd, "copy", "--batch", "b1", "--config", cfg)
    apis.dst_cal.cals["a@new.org"]["a@new.org"]["events"].clear()
    code, out, _ = run(capfd, "verify", "--batch", "b1", "--config", cfg)
    assert code == 1 and check(out, "test_calendar_events_missing", "u001")[0]["ok"] is False


def test_user_with_empty_mailbox_and_calendar_passes(env, capfd):
    _, cfg, _ = env
    assert run(capfd, "copy", "--batch", "b2", "--config", cfg)[0] == 0
    code, out, _ = run(capfd, "verify", "--batch", "b2", "--config", cfg)
    assert code == 0 and out[0]["failed_checks"] == 0
    assert {c.get("user") for c in out[0]["checks"]} == {None, "u003"}


def test_expect_changes_does_not_fail_freeze_check(env, capfd):
    apis, cfg, _ = env
    run(capfd, "copy", "--batch", "b1", "--config", cfg)
    apis.src_gmail.add_message("a@old.org", raw(99), ["INBOX"])  # mail arrives during the copy window
    run(capfd, "copy", "--batch", "b1", "--config", cfg)
    code, out, _ = run(capfd, "verify", "--batch", "b1", "--config", cfg)
    assert code == 1 and check(out, "test_freeze_held", "u001")[0]["ok"] is False
    code, out, _ = run(capfd, "verify", "--batch", "b1", "--expect-changes", "--config", cfg)
    assert code == 0 and check(out, "test_freeze_held", "u001")[0]["ok"] is True


def test_inventory_error_fails_check_without_crashing(env, capfd):
    _, cfg, tmp = env
    run(capfd, "copy", "--batch", "b1", "--config", cfg)
    (tmp / "i.csv").write_text("nope\n")
    code, out, cap = run(capfd, "verify", "--batch", "b1", "--config", cfg)
    assert code == 1 and len(out) == 1 and cap.err == ""
    assert check(out, "test_inventory_present")[0]["ok"] is False


def test_unknown_batch(env, capfd):
    _, cfg, _ = env
    for argv in (["--batch", "nope"], ["--batch", ""]):
        code, out, _ = run(capfd, "verify", *argv, "--config", cfg)
        assert code == 2 and out == [{"command": "verify", "ok": False, "reason": "unknown_batch"}]
