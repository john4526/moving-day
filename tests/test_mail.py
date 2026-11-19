import pytest
from fakes import make_apis

from wsmigrate.api import ApiError
from wsmigrate.ledger import Ledger
from wsmigrate.mail import MailStats, copy_mail, msgid_hash

U = "u001"


def raw(n, mid=True, date=True):
    h = f"Subject: s{n}\r\n"
    if mid:
        h += f"Message-ID: <id{n}@x>\r\n"
    if date:
        h += "Date: Mon, 1 Jan 2026 00:00:00 +0000\r\n"
    return (h + f"\r\nbody{n}\r\n").encode()


@pytest.fixture
def env(tmp_path):
    apis = make_apis()
    led = Ledger(str(tmp_path / "l.db"))
    yield apis, led
    led.close()


def run(env):
    a, led = env
    return copy_mail(a.src_gmail, a.dst_gmail, led, U, "s@x", "d@x")


def dst_msgs(env):
    return env[0].dst_gmail.messages.get("d@x", {})


def test_copies_all_messages_with_labels_and_state(env):
    a, led = env
    lid = a.src_gmail.add_label("s@x", "Donors/2025")
    for n in range(3):
        a.src_gmail.add_message("s@x", raw(n), ["INBOX", "UNREAD", "STARRED", lid])
    st = run(env)
    assert st == MailStats(found=3, copied=3, skipped=0, failed=0)
    names = a.dst_gmail.labels["d@x"]
    assert len(dst_msgs(env)) == 3
    for m in dst_msgs(env).values():
        assert {"INBOX", "UNREAD", "STARRED"} <= set(m["labels"])
        assert [names[i] for i in m["labels"] if i in names] == ["Donors/2025"]
    assert led.get_meta(U, "history_start") is not None
    assert led.get_meta(U, "history_end") is not None
    assert led.get_meta(U, "preexisting") == "0"


def test_rerun_creates_no_duplicates(env):
    a, _ = env
    for n in range(3):
        a.src_gmail.add_message("s@x", raw(n), ["INBOX"])
    run(env)
    st = run(env)
    assert len(dst_msgs(env)) == 3
    assert st.copied == 0 and st.found == 3


def test_interrupted_run_resumes(env):
    a, led = env
    for n in range(3):
        a.src_gmail.add_message("s@x", raw(n), ["INBOX"])
    # let the first insert succeed, interrupt the second
    real = a.dst_gmail.insert
    state = {"n": 0}

    def flaky(user, r, labels, thread):
        state["n"] += 1
        if state["n"] == 2:
            raise KeyboardInterrupt
        return real(user, r, labels, thread)

    a.dst_gmail.insert = flaky
    with pytest.raises(KeyboardInterrupt):
        run(env)
    assert len(led.pending(U)) == 1
    a.dst_gmail.insert = real
    run(env)
    assert len(dst_msgs(env)) == 3
    assert led.counts(U) == {"done": 3, "failed": 0, "pending": 0}


def test_interrupt_after_insert_does_not_duplicate(env):
    a, led = env
    for n in range(2):
        a.src_gmail.add_message("s@x", raw(n), ["INBOX"])
    real = a.dst_gmail.insert
    state = {"n": 0}

    def crash_after(user, r, labels, thread):
        state["n"] += 1
        out = real(user, r, labels, thread)
        if state["n"] == 1:
            raise KeyboardInterrupt  # inserted but never marked done
        return out

    a.dst_gmail.insert = crash_after
    with pytest.raises(KeyboardInterrupt):
        run(env)
    a.dst_gmail.insert = real
    run(env)
    assert len(dst_msgs(env)) == 2
    assert led.counts(U) == {"done": 2, "failed": 0, "pending": 0}


def test_pending_row_already_on_destination_is_not_reinserted(env):
    a, led = env
    r = raw(1)
    mid = a.src_gmail.add_message("s@x", r, ["INBOX"], "t1")
    a.dst_gmail.add_message("d@x", r, ["INBOX"])
    led.mark_pending(U, mid, "t1", msgid_hash(r))
    run(env)
    assert led.dst_thread(U, "t1")
    assert len(dst_msgs(env)) == 1
    assert led.counts(U)["done"] == 1


def test_threads_stay_grouped(env):
    a, _ = env
    a.src_gmail.add_message("s@x", raw(1), ["INBOX"], "T")
    a.src_gmail.add_message("s@x", raw(2), ["INBOX"], "T")
    run(env)
    assert len({m["thread"] for m in dst_msgs(env).values()}) == 1


def test_spam_trash_draft_chat_skipped(env):
    a, _ = env
    a.src_gmail.add_message("s@x", raw(1), ["INBOX"])
    for lab in ("SPAM", "TRASH", "DRAFT", "CHAT"):
        a.src_gmail.add_message("s@x", raw(9), [lab])
    st = run(env)
    assert st.copied == 1 and len(dst_msgs(env)) == 1
    assert st.skipped == 2  # list_messages already hides SPAM/TRASH in the fake


def test_permanent_error_recorded_and_run_continues(env):
    a, led = env
    for n in range(3):
        a.src_gmail.add_message("s@x", raw(n), ["INBOX"])
    a.dst_gmail.fail_next("insert", ApiError("http_400", retryable=False))
    st = run(env)
    assert (st.copied, st.failed) == (2, 1)
    assert led.failures(U) == {"http_400": 1}


def test_source_is_only_read(env):
    a, _ = env
    a.src_gmail.add_message("s@x", raw(1), ["INBOX"])
    run(env)
    assert {c[0] for c in a.src_gmail.calls} <= {"profile", "list_labels", "list_messages", "get_raw"}


def test_message_without_message_id_copied_once(env):
    a, led = env
    a.src_gmail.add_message("s@x", raw(1, mid=False), ["INBOX"])
    run(env)
    run(env)
    assert len(dst_msgs(env)) == 1


def test_no_message_id_pending_is_dropped_and_retried(env):
    a, led = env
    r = raw(1, mid=False)
    mid = a.src_gmail.add_message("s@x", r, ["INBOX"])
    assert msgid_hash(r).startswith("raw:")
    led.mark_pending(U, mid, "t", msgid_hash(r))
    run(env)
    assert len(dst_msgs(env)) == 1 and led.counts(U)["pending"] == 0


def test_two_messages_sharing_message_id_both_copied(env):
    a, _ = env
    a.src_gmail.add_message("s@x", raw(1), ["INBOX"])
    a.src_gmail.add_message("s@x", raw(1), ["SENT"])
    run(env)
    run(env)
    assert len(dst_msgs(env)) == 2


def test_label_differing_only_by_case_reuses_existing(env):
    a, _ = env
    existing = a.dst_gmail.add_label("d@x", "donors")
    lid = a.src_gmail.add_label("s@x", "Donors")
    a.src_gmail.add_message("s@x", raw(1), [lid])
    run(env)
    assert list(a.dst_gmail.labels["d@x"]) == [existing]
    assert next(iter(dst_msgs(env).values()))["labels"] == [existing]
    assert not [c for c in a.dst_gmail.calls if c[0] == "create_label"]


def test_empty_mailbox(env):
    assert run(env) == MailStats(0, 0, 0, 0)


def test_no_date_header_counted_cumulatively(env):
    a, led = env
    a.src_gmail.add_message("s@x", raw(1, date=False), ["INBOX"])
    run(env)
    a.src_gmail.add_message("s@x", raw(2, date=False), ["INBOX"])
    run(env)
    assert led.get_meta(U, "no_date_header") == "2"


def test_meta_set_once_and_end_overwritten(env):
    a, led = env
    a.dst_gmail.add_message("d@x", raw(7), ["INBOX"])
    a.src_gmail.add_message("s@x", raw(1), ["INBOX"])
    run(env)
    start = led.get_meta(U, "history_start")
    a.src_gmail.add_message("s@x", raw(2), ["INBOX"])
    run(env)
    assert led.get_meta(U, "preexisting") == "1"
    assert led.get_meta(U, "history_start") == start
    assert led.get_meta(U, "history_end") != start


def test_no_date_header_survives_interrupt(env):
    a, led = env
    a.src_gmail.add_message("s@x", raw(1, date=False), ["INBOX"])
    a.src_gmail.add_message("s@x", raw(2), ["INBOX"])
    real = a.dst_gmail.insert
    state = {"n": 0}

    def flaky(user, r, labels, thread):
        state["n"] += 1
        if state["n"] == 2:
            raise KeyboardInterrupt
        return real(user, r, labels, thread)

    a.dst_gmail.insert = flaky
    with pytest.raises(KeyboardInterrupt):
        run(env)
    a.dst_gmail.insert = real
    run(env)
    assert led.get_meta(U, "no_date_header") == "1"
