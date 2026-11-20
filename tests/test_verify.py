import html
import json
import re

import pytest
from fakes import make_apis

from movingday import cli, runtime
from movingday.api import ApiError
from movingday.ledger import Ledger

VALIDATION_TESTS = {
    "test_mapping_valid", "test_inventory_present", "test_mail_none_missing", "test_mail_no_duplicates",
    "test_mail_no_foreign_messages", "test_mail_label_counts", "test_calendar_counts",
    "test_calendar_sharing", "test_calendar_events_missing", "test_nothing_left_failed", "test_freeze_held",
    "test_carried_over_notes", "test_contacts_count", "test_contacts_missing", "test_contact_labels",
    "test_drive_files_count", "test_drive_files_missing", "test_drive_folders", "test_drive_checksums",
    "test_drive_sharing", "test_drive_in_step",
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
    monkeypatch.setattr(runtime, "get_apis", lambda config, source_only=False: apis)
    return apis, str(tmp_path / "c.toml"), tmp_path


def run(capfd, *argv):
    code = cli.main(list(argv))
    cap = capfd.readouterr()
    return code, [json.loads(l) for l in cap.out.splitlines()], cap


def report_rows(path):
    """{test id: [cell text]} from pytest-html's embedded data, plus the unescaped page text."""
    text = html.unescape(path.read_text())
    blob = json.loads(html.unescape(re.search(r'data-jsonblob="([^"]*)"', path.read_text()).group(1)))
    strip = lambda c: html.unescape(re.sub(r"<[^>]+>", "", c))
    return {tid: [strip(c) for c in runs[-1]["resultsTableRow"]] for tid, runs in blob["tests"].items()}, text


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


def test_report_shows_condition_expected_and_actual(env, capfd):
    apis, cfg, tmp = env
    run(capfd, "copy", "--batch", "b1", "--config", cfg)
    apis.dst_gmail.messages["a@new.org"].popitem()
    _, out, _ = run(capfd, "verify", "--batch", "b1", "--config", cfg)
    rows, text = report_rows(tmp / "r" / out[0]["report"])
    assert all(f"<th>{h}</th>" in text for h in ("Condition", "Expected", "Actual"))
    assert "Moving Day sign-off report" in text
    result, condition, expected, actual = rows["test_validation.py::test_mail_none_missing[u001]"][:4]
    assert result == "Failed" and condition == "Every source message is present on the destination."
    assert (expected, actual) == ("0 missing", "1 missing")
    assert rows["test_validation.py::test_mail_none_missing[u002]"][1:4] == [
        "Every source message is present on the destination.", "0 missing", "0 missing"]
    counts = rows["test_validation.py::test_calendar_counts[u001]"]
    assert counts[2].splitlines() == ["calendars: 1 (same as source)", "events: 1 (same as source)",
                                      "recurring series: 0 (same as source)", "attendees: 1 (same as source)"]
    assert counts[3].splitlines() == ["calendars: 1", "events: 1", "recurring series: 0", "attendees: 1"]


class _Denied(Exception):
    reason = "http_403"


def test_session_fixture_failure_keeps_condition_on_every_row(env, capfd, monkeypatch):
    _, cfg, tmp = env
    run(capfd, "copy", "--batch", "b1", "--config", cfg)
    monkeypatch.setattr(runtime, "get_apis", lambda c: (_ for _ in ()).throw(_Denied("x")))
    code, out, cap = run(capfd, "verify", "--batch", "b1", "--config", cfg)
    assert code == 1 and len(out) == 1 and cap.err == ""
    rows, _ = report_rows(tmp / "r" / out[0]["report"])
    assert len(rows) == 40 and all(r[1].endswith(".") for r in rows.values())  # every row has its condition
    uses_apis = [t for t in rows if "_mail_" in t or "_calendar_" in t or "_contact" in t or "inventory" in t
                 or "_drive_" in t]
    assert len(uses_apis) == 33 and all(rows[t][3] == "error: http_403" for t in uses_apis)
    assert rows["test_validation.py::test_mail_none_missing[u001]"][2] == "0 missing"  # static expected
    assert rows["test_validation.py::test_calendar_counts[u001]"][2] == "—"  # data-dependent: unknown


def test_freeze_fails_when_no_copy_recorded(env, capfd):
    _, cfg, tmp = env
    code, out, _ = run(capfd, "verify", "--batch", "b2", "--expect-changes", "--config", cfg)
    assert code == 1 and [c for c in out[0]["checks"] if not c["ok"]] == [
        {"name": "test_freeze_held", "user": "u003", "ok": False}]
    rows, _ = report_rows(tmp / "r" / out[0]["report"])
    assert rows["test_validation.py::test_freeze_held[u003]"][3] == "no copy recorded"


def test_nonzero_pytest_exit_without_failed_check_gives_reason(env, capfd, monkeypatch):
    _, cfg, _ = env
    monkeypatch.setattr(pytest, "main", lambda *a, **k: 3)
    code, out, _ = run(capfd, "verify", "--batch", "b1", "--config", cfg)
    assert code == 1 and out[0]["passed"] is False and out[0]["failed_checks"] == 0
    assert out[0]["reason"] == "pytest_exit_3"


def test_plain_skip_is_not_ok_but_xfail_is():
    from types import SimpleNamespace as R
    rec = cli._Recorder()
    rec.pytest_runtest_logreport(R(nodeid="a", failed=False, skipped=True))
    rec.pytest_runtest_logreport(R(nodeid="b", failed=False, skipped=True, wasxfail="changes expected"))
    assert rec.ok == {"a": False, "b": True}


def test_carried_over_notes_shows_counts_for_information(env, capfd):
    apis, cfg, tmp = env
    apis.src_gmail.add_message("a@old.org", b"Subject: s\r\nMessage-ID: <77@x>\r\n\r\nb\r\n", ["INBOX"])  # no Date
    run(capfd, "copy", "--batch", "b1", "--config", cfg)
    led = Ledger(str(tmp / "l.db"))
    led.set_event("u001", "primary", "e9", "", "t", "failed", "unsupported_event_type")
    for name, value in (("outside_shares", "2"), ("acl_failed", "1"), ("shared_calendars_skipped", "3")):
        led.set_meta("u001", name, value)
    led.close()
    code, out, _ = run(capfd, "verify", "--batch", "b1", "--config", cfg)
    assert code == 0 and check(out, "test_carried_over_notes", "u001")[0]["ok"] is True
    rows, _ = report_rows(tmp / "r" / out[0]["report"])
    result, _, expected, actual = rows["test_validation.py::test_carried_over_notes[u001]"][:4]
    assert (result, expected) == ("Passed", "for information")
    assert actual.splitlines() == [
        "threads: 3 on source, 3 on destination", "messages with no Date header: 1",
        "calendar entries of unsupported types not copied: 1", "outside shares: 2", "failed shares: 1",
        "shared calendars skipped: 3", "other contacts not copied: 0",
        "contacts removed on source: 0", "Drive unsupported files not copied: 0", "Drive forms not copied: 0",
        "Drive files kept as Office files: 0", "Drive files unplaced: 0", "Drive outside shares: 0",
        "Drive failed shares: 0", "Drive files moved to destination trash: 0", "Drive trashing held: no",
        "Comments, suggestions and version history are not carried over."]


def test_nothing_left_failed_lists_reasons(env, capfd):
    _, cfg, tmp = env
    run(capfd, "copy", "--batch", "b1", "--config", cfg)
    led = Ledger(str(tmp / "l.db"))
    for mid, reason in (("x1", "http_403"), ("x2", "network_error"), ("x3", "http_403")):
        led.mark_failed("u001", mid, reason)
    led.set_event("u001", "primary", "e9", "", "t", "failed", "unsupported_event_type")
    led.close()
    _, out, _ = run(capfd, "verify", "--batch", "b1", "--config", cfg)
    rows, _ = report_rows(tmp / "r" / out[0]["report"])
    assert rows["test_validation.py::test_nothing_left_failed[u001]"][3] == "3 failed (http_403: 2, network_error: 1)"
    assert rows["test_validation.py::test_nothing_left_failed[u002]"][3] == "0 failed"


def _contact(apis, name, groups=()):
    meta = {"source": {"type": "CONTACT", "id": "x"}}
    return apis.src_people.add_contact("a@old.org", {
        "names": [{"givenName": name, "metadata": meta}],
        "memberships": [{"contactGroupMembership": {"contactGroupResourceName": g}} for g in groups],
        "metadata": {"sources": [{"type": "CONTACT", "id": "x", "etag": "e"}]}})


def test_contact_checks_pass_after_copy_and_show_counts(env, capfd):
    apis, cfg, tmp = env
    g = apis.src_people.add_group("a@old.org", "Team")
    _contact(apis, "one", [g]), _contact(apis, "two")
    run(capfd, "copy", "--batch", "b1", "--config", cfg)
    code, out, _ = run(capfd, "verify", "--batch", "b1", "--config", cfg)
    assert code == 0
    rows, _ = report_rows(tmp / "r" / out[0]["report"])
    count = rows["test_validation.py::test_contacts_count[u001]"]
    assert count[:4] == ["Passed", "The destination has as many contacts as the source.",
                         "contacts: 2 (same as source)", "contacts: 2"]
    assert rows["test_validation.py::test_contacts_missing[u001]"][2:4] == ["0 missing contacts", "0 missing contacts"]
    labels = rows["test_validation.py::test_contact_labels[u001]"]
    assert labels[2:4] == ["labels: 1 (same as source)\n0 labels short of members",
                           "labels: 1\n0 labels short of members"]
    assert rows["test_validation.py::test_contacts_count[u002]"][3] == "contacts: 0"


def test_missing_contact_fails_checks(env, capfd):
    apis, cfg, _ = env
    g = apis.src_people.add_group("a@old.org", "Team")
    _contact(apis, "one", [g]), _contact(apis, "two", [g])
    run(capfd, "copy", "--batch", "b1", "--config", cfg)
    apis.dst_people.contacts["a@new.org"].popitem()
    code, out, _ = run(capfd, "verify", "--batch", "b1", "--config", cfg)
    assert code == 1
    for name in ("test_contacts_count", "test_contacts_missing", "test_contact_labels"):
        assert check(out, name, "u001")[0]["ok"] is False and check(out, name, "u002")[0]["ok"] is True


def test_failed_contact_fails_nothing_left_failed(env, capfd):
    apis, cfg, tmp = env
    _contact(apis, "one")
    apis.dst_people.fail_next("create_contact", ApiError("http_500", False))
    run(capfd, "copy", "--batch", "b1", "--config", cfg)
    code, out, _ = run(capfd, "verify", "--batch", "b1", "--config", cfg)
    rows, _ = report_rows(tmp / "r" / out[0]["report"])
    assert rows["test_validation.py::test_nothing_left_failed[u001]"][3] == "1 failed (http_500: 1)"


def test_carried_over_notes_shows_other_contacts_and_removed_on_source(env, capfd):
    apis, cfg, tmp = env
    res = _contact(apis, "one")
    apis.src_people.set_other_contacts("a@old.org", 9)
    run(capfd, "copy", "--batch", "b1", "--config", cfg)
    del apis.src_people.contacts["a@old.org"][res]
    run(capfd, "copy", "--batch", "b1", "--config", cfg)
    code, out, _ = run(capfd, "verify", "--batch", "b1", "--config", cfg)
    rows, _ = report_rows(tmp / "r" / out[0]["report"])
    lines = rows["test_validation.py::test_carried_over_notes[u001]"][3].splitlines()
    assert "other contacts not copied: 9" in lines and "contacts removed on source: 1" in lines
    assert "other contacts not copied: 0" in rows["test_validation.py::test_carried_over_notes[u002]"][3]


PDF, DOC, FORM = "application/pdf", "application/vnd.google-apps.document", "application/vnd.google-apps.form"


def _drive(apis):
    d = apis.src_drive
    top = d.add_folder("a@old.org")
    pdf = d.add_file("a@old.org", PDF, top, b"pdf")
    d.add_file("a@old.org", DOC, content=b"doc")
    d.add_file("a@old.org", FORM)
    d.share("a@old.org", pdf, {"role": "reader", "type": "user", "emailAddress": "b@old.org"})
    return pdf


def test_report_has_drive_checks_with_expected_and_actual(env, capfd):
    apis, cfg, tmp = env
    _drive(apis)
    run(capfd, "copy", "--batch", "b1", "--config", cfg)
    code, out, _ = run(capfd, "verify", "--batch", "b1", "--config", cfg)
    assert code == 0
    rows, _ = report_rows(tmp / "r" / out[0]["report"])
    row = lambda n, u="u001": rows[f"test_validation.py::{n}[{u}]"]
    assert row("test_drive_files_count")[:4] == [
        "Passed", "The destination has as many files as the source owns (unsupported types excepted).",
        "files: 2 (same as source)", "files: 2"]
    assert row("test_drive_files_missing")[2:4] == ["0 missing files", "0 missing files"]
    assert row("test_drive_folders")[2:4] == ["folders: 1 (same as source)",
                                              "folders: 1\nfiles whose folder is not copied: 0 (for information)"]
    assert row("test_drive_checksums")[2:4] == ["0 checksum mismatches", "0 checksum mismatches"]
    assert row("test_drive_sharing")[2:4] == ["0 missing shares\n0 extra shares"] * 2
    assert row("test_drive_in_step")[2:4] == ["0 changed since copied", "0 changed since copied"]
    notes = row("test_carried_over_notes")[3].splitlines()
    assert "Drive forms not copied: 1" in notes and "Drive unsupported files not copied: 0" in notes
    assert row("test_drive_files_count", "u002")[3] == "files: 0"
    # a destination copy gone and a share removed fail their checks
    led = Ledger(str(tmp / "l.db"))
    pdf_copy = next(r["dst_id"] for r in led.drive_rows("u001").values() if r["md5"])
    led.close()
    apis.dst_drive.files[pdf_copy]["perms"].clear()
    apis.dst_drive.files[pdf_copy]["md5Checksum"] = "bad"
    code, out, _ = run(capfd, "verify", "--batch", "b1", "--config", cfg)
    assert code == 1
    assert check(out, "test_drive_sharing", "u001")[0]["ok"] is False
    assert check(out, "test_drive_checksums", "u001")[0]["ok"] is False
    assert check(out, "test_drive_sharing", "u002")[0]["ok"] is True


def test_drive_in_step_expected_to_differ_with_flag(env, capfd):
    apis, cfg, tmp = env
    pdf = _drive(apis)
    run(capfd, "copy", "--batch", "b1", "--config", cfg)
    apis.src_drive.edit(pdf, content=b"edited after the copy")
    code, out, _ = run(capfd, "verify", "--batch", "b1", "--config", cfg)
    assert code == 1 and check(out, "test_drive_in_step", "u001")[0]["ok"] is False
    rows, _ = report_rows(tmp / "r" / out[0]["report"])
    assert rows["test_validation.py::test_drive_in_step[u001]"][3] == "1 changed since copied"
    code, out, _ = run(capfd, "verify", "--batch", "b1", "--expect-changes", "--config", cfg)
    assert code == 0 and check(out, "test_drive_in_step", "u001")[0]["ok"] is True


def test_verify_passes_after_adopting_an_earlier_transfer(env, capfd):
    apis, cfg, _ = env
    apis.dst_gmail.add_message("a@new.org", raw(0), ["INBOX", "UNREAD"])  # earlier transfer, stale state
    assert run(capfd, "copy", "--batch", "b1", "--allow-existing", "u001", "--config", cfg)[0] == 0
    code, out, _ = run(capfd, "verify", "--batch", "b1", "--config", cfg)
    assert code == 0 and out[0]["passed"] is True


def test_adopted_copies_no_longer_count_as_allowed_extras(env, capfd):
    apis, cfg, _ = env
    apis.dst_gmail.add_message("a@new.org", raw(0), ["INBOX"])
    run(capfd, "copy", "--batch", "b1", "--allow-existing", "u001", "--config", cfg)
    apis.dst_gmail.add_message("a@new.org", raw(99), ["INBOX"])  # not from the source
    code, out, _ = run(capfd, "verify", "--batch", "b1", "--config", cfg)
    assert code == 1
    assert check(out, "test_mail_no_foreign_messages", "u001")[0]["ok"] is False
