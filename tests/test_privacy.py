import html

from test_verify import env, report_rows, run  # noqa: F401  (env is a fixture)

MARKER = "zebra7731"
RAW = (b"From: ZEBRA7731 Person <zebra7731@corr.example>\r\nSubject: ZEBRA7731\r\nMessage-ID: <z@x>\r\n"
       b"Date: Mon, 1 Jan 2026 00:00:00 +0000\r\n\r\nZEBRA7731 body\r\n")


def _run_all(env, capfd):
    apis, cfg, tmp = env
    apis.src_gmail.add_message("a@old.org", RAW, ["INBOX"])
    apis.src_cal.add_event("a@old.org", "a@old.org", {
        "summary": "ZEBRA7731", "description": "ZEBRA7731",
        "attendees": [{"email": "guest@corr.example", "displayName": "ZEBRA7731"}]})
    meta = {"source": {"type": "CONTACT", "id": "x"}}
    apis.src_people.add_contact("a@old.org", {
        "names": [{"givenName": "ZEBRA7731", "metadata": meta}],
        "emailAddresses": [{"value": "zebra7731@corr.example", "metadata": meta}],
        "phoneNumbers": [{"value": "+1 555 ZEBRA7731", "metadata": meta}],
        "biographies": [{"value": "notes ZEBRA7731", "metadata": meta}],
        "metadata": {"sources": [{"type": "CONTACT", "id": "x", "etag": "e"}]}})
    folder = apis.src_drive.add_folder("a@old.org", name="ZEBRA7731 folder")
    apis.src_drive.add_file("a@old.org", "application/pdf", folder, b"ZEBRA7731 content", name="ZEBRA7731.pdf",
                            description="ZEBRA7731 description")
    apis.src_drive.add_file("a@old.org", "application/vnd.google-apps.document", content=b"ZEBRA7731 doc",
                            name="ZEBRA7731 doc", description="ZEBRA7731")
    out, err, report = "", "", None
    for argv in (["check"], ["copy", "--batch", "b1"], ["status"], ["verify", "--batch", "b1"]):
        code, lines, cap = run(capfd, *argv, "--config", cfg)
        assert argv != ["check"] or lines[0]["ok"] is True  # the scan really ran over the marker files
        out, err = out + cap.out, err + cap.err
        report = lines[0].get("report", report)
    return out, err, (tmp / "l.db").read_bytes(), html.unescape((tmp / "r" / report).read_text())


def test_marker_never_leaks(env, capfd):
    out, err, ledger, report = _run_all(env, capfd)
    assert MARKER not in out.lower() and MARKER not in err.lower()
    assert MARKER.encode() not in ledger.lower()
    assert MARKER not in report.lower()


def test_contacts_were_copied_in_the_privacy_run(env, capfd):
    _run_all(env, capfd)
    assert len(env[0].dst_people.contacts["a@new.org"]) == 1  # so the marker test above covered a real copy


def test_drive_was_copied_in_the_privacy_run(env, capfd):
    _run_all(env, capfd)
    names = {f["name"] for f in env[0].dst_drive.files.values() if f["owner"] == "a@new.org"}
    assert names == {"ZEBRA7731 folder", "ZEBRA7731.pdf", "ZEBRA7731 doc"}  # so the marker test covered Drive


def test_report_shows_user_addresses_but_no_correspondents(env, capfd):
    *_, report = _run_all(env, capfd)
    assert "a@old.org" in report and "a@new.org" in report and "b@new.org" in report
    assert "corr.example" not in report


class _Denied(Exception):
    reason = "http_403"


def test_api_error_text_never_reaches_report(env, capfd):
    apis, cfg, tmp = env
    run(capfd, "copy", "--batch", "b1", "--config", cfg)
    apis.dst_gmail.fail_next("list_messages", _Denied("ZEBRA7731 zebra7731@corr.example"))
    code, out, cap = run(capfd, "verify", "--batch", "b1", "--config", cfg)
    path = tmp / "r" / out[0]["report"]
    rows, text = report_rows(path)
    assert code == 1 and MARKER not in (text + cap.out + cap.err).lower() and "corr.example" not in text
    row = rows["test_validation.py::test_mail_none_missing[u001]"]
    assert row[0] == "Error" and row[4].endswith("::setup") and row[3] == "error: http_403"


def test_captured_output_and_logs_never_reach_report(env, capfd, monkeypatch):
    import logging
    import sys
    apis, cfg, tmp = env
    run(capfd, "copy", "--batch", "b1", "--config", cfg)
    real = apis.dst_gmail.list_messages

    def noisy(user):
        sys.stderr.write("ZEBRA7731 stderr\n")
        sys.stdout.write("ZEBRA7731 stdout\n")
        logging.getLogger("googleapiclient.http").warning("Invalid JSON content from response: %s", "ZEBRA7731")
        return real(user)
    monkeypatch.setattr(apis.dst_gmail, "list_messages", noisy)
    apis.dst_gmail.messages["a@new.org"].popitem()  # a failing row too, whose log pytest-html would show
    code, out, cap = run(capfd, "verify", "--batch", "b1", "--config", cfg)
    text = html.unescape((tmp / "r" / out[0]["report"]).read_text())
    assert code == 1 and len(out) == 1 and cap.err == "" and len(cap.out.splitlines()) == 1
    assert MARKER not in (text + cap.out).lower()
