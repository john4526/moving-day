"""check, copy, status. Everything the agent sees goes through safeout.emit."""
import argparse
import fcntl
import os
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

import pytest

from movingday import calendar_copy, mail, runtime, validation
from movingday.config import UntrustedFile, load_config
from movingday.ledger import Ledger
from movingday.mapping import MappingError, load_mapping
from movingday.safeout import emit, reason_for


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        emit({"ok": False, "reason": "bad_arguments"})
        sys.exit(2)


def _parser():
    p = _Parser(prog="movingday", add_help=False)
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("check", "copy", "status", "verify"):
        s = sub.add_parser(name, add_help=False)
        s.add_argument("--config", default=os.environ.get("MOVINGDAY_CONFIG"))
        if name == "check":
            s.add_argument("--source-only", action="store_true")
        else:
            s.add_argument("--batch", required=name in ("copy", "verify"))
        if name == "verify":
            s.add_argument("--expect-changes", action="store_true")
        if name == "copy":
            s.add_argument("--mail", action="store_true")
            s.add_argument("--calendar", action="store_true")
            s.add_argument("--allow-existing", nargs="+", default=[], metavar="KEY")
    return p


def _scan_user(apis, u, source_only):
    """Read-only: profile and calendar list. Returns the entry and the group calendars this user owns."""
    e = {"user": u.key, "src_messages": apis.src_gmail.profile(u.source)["messagesTotal"]}
    own = delegated = 0
    groups = set()
    for cal in apis.src_cal.list_calendars(u.source):
        if cal["primary"]:
            own += 1
        elif cal["accessRole"] == "owner":
            if cal["id"].endswith(calendar_copy.GROUP_SUFFIX):
                own += 1
                groups.add(cal["id"])
            elif not cal["id"].endswith(calendar_copy.RESOURCE_SUFFIX):
                delegated += 1
    e.update(own_calendars=own, delegated_calendars=delegated)
    if not source_only:
        e["dst_existing"] = apis.dst_gmail.profile(u.destination)["messagesTotal"]
    return e, groups


def _check(config, users, source_only=False):
    apis = runtime.get_apis(config, source_only=source_only)
    out, owned = [], {}
    for u in users:
        try:
            e, owned[u.key] = _scan_user(apis, u, source_only)
            e["ok"] = True
        except Exception as exc:
            e = {"user": u.key, "ok": False, "reason": reason_for(exc)}
        out.append(e)
    for e in out:
        if e["ok"]:
            e["team_calendars"] = sum(
                any(g in other for k, other in owned.items() if k != e["user"]) for g in owned[e["user"]])
    good = [e for e in out if e["ok"]]
    summary = {"users": len(users), "total_messages": sum(e["src_messages"] for e in good),
               "largest_mailbox": max((e["src_messages"] for e in good), default=0),
               "accounts_with_delegated": sum(e["delegated_calendars"] > 0 for e in good),
               "accounts_with_team_calendars": sum(e["team_calendars"] > 0 for e in good),
               "failed": len(out) - len(good)}
    emit({"command": "check", "ok": not summary["failed"], "users": out, "summary": summary})
    return 1 if summary["failed"] else 0


def _copy_user(config, u, do_mail, do_cal, mapping):
    try:
        ledger = Ledger(config.ledger_path)
        try:
            apis = runtime.get_apis(config)
            e = {"user": u.key}
            if do_mail:
                e.update(asdict(mail.copy_mail(
                    apis.src_gmail, apis.dst_gmail, ledger, u.key, u.source, u.destination)))
            if do_cal:
                e.update(asdict(calendar_copy.copy_calendars(
                    apis.src_cal, apis.dst_cal, ledger, mapping, u.key, u.source, u.destination,
                    config.dest_domain)))
            # unsupported event types are retried every run; they are not failures (R5)
            e["ok"] = set(ledger.failures(u.key)) <= {"unsupported_event_type"}
            return e
        finally:
            ledger.close()
    except Exception as exc:
        return {"user": u.key, "ok": False, "reason": reason_for(exc)}


def _copy(config, mapping, a):
    users = mapping.batch(a.batch)
    if not users:
        emit({"command": "copy", "ok": False, "reason": "unknown_batch"})
        return 2
    with open(config.ledger_path + ".lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            emit({"command": "copy", "ok": False, "reason": "already_running"})
            return 2
        apis, ledger, refused = runtime.get_apis(config), Ledger(config.ledger_path), []
        try:
            for u in users:
                total = apis.dst_gmail.profile(u.destination)["messagesTotal"]
                if u.key not in a.allow_existing and total > config.max_existing + ledger.counts(u.key)["done"]:
                    refused.append({"user": u.key, "ok": False, "reason": "destination_not_empty"})
        finally:
            ledger.close()
        if refused:
            emit({"command": "copy", "ok": False, "reason": "destination_not_empty", "users": refused})
            return 2
        both = not (a.mail or a.calendar)
        t0 = time.monotonic()
        with ThreadPoolExecutor(max_workers=config.parallel_users) as pool:
            results = list(pool.map(
                lambda u: _copy_user(config, u, a.mail or both, a.calendar or both, mapping), users))
        bad = any(not r["ok"] for r in results)
        emit({"command": "copy", "ok": not bad, "batch": a.batch, "users": results,
              "seconds": round(time.monotonic() - t0)})
        return 1 if bad else 0


def _status(config, users):
    ledger = Ledger(config.ledger_path)
    try:
        out = []
        for u in users:
            c = ledger.counts(u.key)
            out.append({"user": u.key, "copied": c["done"], "failed": c["failed"],
                        "pending": c["pending"], "failures": ledger.failures(u.key)})
    finally:
        ledger.close()
    emit({"command": "status", "ok": True, "users": out})
    return 0


class _Recorder:
    """pytest plugin: one check per test, ok unless any phase failed or was skipped (an xfail counts as ok)."""

    def __init__(self):
        self.ok = {}

    def pytest_runtest_logreport(self, report):
        bad = report.failed or (report.skipped and not hasattr(report, "wasxfail"))
        self.ok[report.nodeid] = self.ok.get(report.nodeid, True) and not bad


def _verify(config, a):
    here = os.path.dirname(validation.__file__)
    name = f"{a.batch}-{time.strftime('%Y%m%d-%H%M%S')}.html"
    rec = _Recorder()
    args = [here, "-c", os.devnull, "--rootdir", here, "--batch", a.batch, "--config", a.config,
            "--html", os.path.abspath(os.path.join(config.report_dir, name)), "--self-contained-html",
            "--tb=short", "--show-capture=no", "-s", "-p", "no:cacheprovider", "-p", "no:logging",
            "-p", "no:faulthandler"] + (["--expect-changes"] if a.expect_changes else [])
    # pytest writes at the fd level, so silence fds 1 and 2, not just sys.stdout/stderr
    sys.stdout.flush(); sys.stderr.flush()
    saved, streams = (os.dup(1), os.dup(2)), (sys.stdout, sys.stderr)
    with open(os.devnull, "w") as null:
        os.dup2(null.fileno(), 1); os.dup2(null.fileno(), 2)
        sys.stdout = sys.stderr = null
        try:
            ret = pytest.main(args, plugins=[rec])
        finally:
            sys.stdout, sys.stderr = streams
            for fd, copy in zip((1, 2), saved):
                os.dup2(copy, fd); os.close(copy)
    checks = []
    for nodeid, ok in rec.ok.items():
        test, _, user = nodeid.split("::")[-1].rstrip("]").partition("[")
        checks.append({"name": test, **({"user": user} if user else {}), "ok": ok})
    failed = sum(not c["ok"] for c in checks)
    passed = ret == 0 and failed == 0
    out = {"command": "verify", "batch": a.batch, "passed": passed, "failed_checks": failed,
           "checks": checks, "report": name}
    if ret != 0 and not failed:
        out["reason"] = f"pytest_exit_{int(ret)}"
    emit(out)
    return 0 if passed else 1


def main(argv: list[str] | None = None) -> int:
    warnings.simplefilter("ignore")
    try:
        a = _parser().parse_args(argv)
    except SystemExit:  # bad arguments were already emitted
        return 2
    name = a.command
    try:
        if not a.config:
            emit({"command": name, "ok": False, "reason": "no_config"})
            return 2
        try:
            config = load_config(a.config)
            mapping = load_mapping(config.mapping_file)
        except MappingError as e:
            emit({"command": name, "ok": False, "reason": e.reason, "row": e.row})
            return 2
        except UntrustedFile as e:
            emit({"command": name, "ok": False, "reason": e.reason})
            return 2
        if name == "verify":
            if not mapping.batch(a.batch):
                emit({"command": name, "ok": False, "reason": "unknown_batch"})
                return 2
            return _verify(config, a)
        users = mapping.batch(a.batch) if getattr(a, "batch", None) else mapping.users
        if name == "check":
            return _check(config, users, a.source_only)
        if name == "status":
            return _status(config, users)
        return _copy(config, mapping, a)
    except KeyboardInterrupt:
        emit({"command": name, "ok": False, "reason": "interrupted"})
        return 1
    except Exception as exc:
        emit({"command": name, "ok": False, "reason": reason_for(exc)})
        return 1
