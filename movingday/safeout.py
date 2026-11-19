"""The only way anything is printed: allow-listed keys, safe-pattern values."""
import json
import re
import sys

ALLOWED_KEYS = frozenset(
    "command batch user ok reason row found copied skipped failed remaining pending "
    "src_messages dst_existing calendars events_copied events_updated events_removed "
    "events_failed acl_copied outside_shares no_date_header threads_src threads_dst "
    "users own_calendars delegated_calendars team_calendars summary total_messages largest_mailbox "
    "accounts_with_delegated accounts_with_team_calendars passed failed_checks checks name failures report seconds".split()
)
_SAFE = re.compile(r"^[A-Za-z0-9_./-]{1,120}$")


class UnsafeOutput(Exception):
    pass


def _check(v, in_failures=False):
    if v is None or isinstance(v, (bool, int)):
        return
    if isinstance(v, str):
        if _SAFE.match(v):
            return
    elif isinstance(v, list):
        if all(isinstance(i, dict) for i in v):
            for i in v:
                _check(i)
            return
    elif isinstance(v, dict):
        if in_failures:
            if all(isinstance(k, str) and _SAFE.match(k) and type(x) is int for k, x in v.items()):
                return
        elif all(k in ALLOWED_KEYS for k in v):
            for k, x in v.items():
                _check(x, k == "failures")
            return
    raise UnsafeOutput("unsafe output")


def emit(obj: dict, stream=None) -> None:
    _check(obj)
    line = json.dumps(obj) + "\n"  # fully built before writing, so rejects write nothing
    (stream or sys.stdout).write(line)


def reason_for(exc: BaseException) -> str:
    r = getattr(exc, "reason", None)
    return r if isinstance(r, str) else "internal_" + type(exc).__name__
