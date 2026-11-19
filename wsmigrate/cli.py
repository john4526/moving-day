"""check, copy, status. Everything the agent sees goes through safeout.emit."""
import argparse
import fcntl
import os
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

from wsmigrate import calendar_copy, mail, runtime
from wsmigrate.config import load_config
from wsmigrate.ledger import Ledger
from wsmigrate.mapping import MappingError, load_mapping
from wsmigrate.safeout import emit, reason_for


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        emit({"ok": False, "reason": "bad_arguments"})
        sys.exit(2)


def _parser():
    p = _Parser(prog="wsmigrate")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("check", "copy", "status"):
        s = sub.add_parser(name)
        s.add_argument("--config", default=os.environ.get("WSMIGRATE_CONFIG"))
        if name != "check":
            s.add_argument("--batch", required=name == "copy")
        if name == "copy":
            s.add_argument("--mail", action="store_true")
            s.add_argument("--calendar", action="store_true")
            s.add_argument("--allow-existing", nargs="+", default=[], metavar="KEY")
    return p


def _check(config, users):
    apis = runtime.get_apis(config)
    out = []
    for u in users:
        e = {"user": u.key}
        try:
            e["src_messages"] = apis.src_gmail.profile(u.source)["messagesTotal"]
            e["dst_existing"] = apis.dst_gmail.profile(u.destination)["messagesTotal"]
            e["ok"] = True
        except Exception as exc:
            e.update(ok=False, reason=reason_for(exc))
        out.append(e)
    bad = any(not e["ok"] for e in out)
    emit({"command": "check", "ok": not bad, "users": out})
    return 1 if bad else 0


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
                    apis.src_cal, apis.dst_cal, ledger, mapping, u.key, u.source, u.destination)))
            e["ok"] = not (e.get("failed") or e.get("events_failed"))
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


def main(argv: list[str] | None = None) -> int:
    warnings.simplefilter("ignore")
    try:
        a = _parser().parse_args(argv)
    except SystemExit as e:  # --help exits 0; bad arguments were already emitted
        return e.code if isinstance(e.code, int) else 2
    name = a.command
    try:
        if not a.config:
            emit({"command": name, "ok": False, "reason": "no_config"})
            return 2
        config = load_config(a.config)
        try:
            mapping = load_mapping(config.mapping_file)
        except MappingError as e:
            emit({"command": name, "ok": False, "reason": e.reason, "row": e.row})
            return 2
        users = mapping.batch(a.batch) if getattr(a, "batch", None) else mapping.users
        if name == "check":
            return _check(config, users)
        if name == "status":
            return _status(config, users)
        return _copy(config, mapping, a)
    except Exception as exc:
        emit({"command": name, "ok": False, "reason": reason_for(exc)})
        return 1
