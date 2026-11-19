from test_verify import env, run  # noqa: F401  (env is a fixture)

MARKER = "zebra7731"
RAW = (b"From: ZEBRA7731 Person <zebra7731@corr.example>\r\nSubject: ZEBRA7731\r\nMessage-ID: <z@x>\r\n"
       b"Date: Mon, 1 Jan 2026 00:00:00 +0000\r\n\r\nZEBRA7731 body\r\n")


def _run_all(env, capfd):
    apis, cfg, tmp = env
    apis.src_gmail.add_message("a@old.org", RAW, ["INBOX"])
    apis.src_cal.add_event("a@old.org", "a@old.org", {
        "summary": "ZEBRA7731", "description": "ZEBRA7731",
        "attendees": [{"email": "guest@corr.example", "displayName": "ZEBRA7731"}]})
    out, err, report = "", "", None
    for argv in (["check"], ["copy", "--batch", "b1"], ["status"], ["verify", "--batch", "b1"]):
        code, lines, cap = run(capfd, *argv, "--config", cfg)
        out, err = out + cap.out, err + cap.err
        report = lines[0].get("report", report)
    return out, err, (tmp / "l.db").read_bytes(), (tmp / "r" / report).read_text()


def test_marker_never_leaks(env, capfd):
    out, err, ledger, report = _run_all(env, capfd)
    assert MARKER not in out.lower() and MARKER not in err.lower()
    assert MARKER.encode() not in ledger.lower()
    assert MARKER not in report.lower()


def test_report_shows_user_addresses_but_no_correspondents(env, capfd):
    *_, report = _run_all(env, capfd)
    assert "a@old.org" in report and "a@new.org" in report and "b@new.org" in report
    assert "corr.example" not in report
