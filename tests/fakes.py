"""In-memory stand-ins for movingday.api.Gmail/Calendar/Directory/People."""
import copy
import datetime
import functools
import inspect
import itertools
from email.parser import BytesHeaderParser

from movingday.api import ApiError, RawMessage
from movingday.runtime import Apis

_ids = itertools.count(1)  # shared by every fake, so source and destination never issue the same id
SYSTEM_LABELS = ["INBOX", "SENT", "DRAFT", "SPAM", "TRASH", "UNREAD", "STARRED", "IMPORTANT", "CHAT"]


def api(method):
    """Record the call in self.calls (args by parameter name), then raise any injected failure."""
    sig = inspect.signature(method)

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        bound = sig.bind(self, *args, **kwargs).arguments
        self.calls.append((method.__name__, {k: v for k, v in bound.items() if k != "self"}))
        pending = self._fail.get(method.__name__)
        if pending:
            raise pending.pop(0)
        return method(self, *args, **kwargs)

    return wrapper


class _Fake:
    def __init__(self):
        self.calls = []
        self._fail = {}

    def fail_next(self, method, error, times=1):
        """error: any BaseException instance or class."""
        self._fail.setdefault(method, []).extend([error] * times)

    def _id(self, prefix):
        return f"{prefix}{next(_ids)}"


class FakeGmail(_Fake):
    def __init__(self):
        super().__init__()
        self.messages = {}  # user -> {id: {"raw", "labels", "thread"}} in insertion order
        self.labels = {}  # user -> {id: name} (user labels only)
        self.history = 0

    def add_message(self, user, raw, label_ids, thread_id=None) -> str:
        msg_id = self._id("m")
        self.messages.setdefault(user, {})[msg_id] = {
            "raw": raw, "labels": list(label_ids), "thread": thread_id or self._id("t")
        }
        self.history += 1
        return msg_id

    def add_label(self, user, name) -> str:
        i = self._id("Label_")
        self.labels.setdefault(user, {})[i] = name
        return i

    def _get(self, user, msg_id):
        m = self.messages.get(user, {}).get(msg_id)
        if m is None:
            raise ApiError("http_404", False)
        return m

    @api
    def profile(self, user) -> dict:
        msgs = self.messages.get(user, {}).values()
        return {"messagesTotal": len(msgs), "threadsTotal": len({m["thread"] for m in msgs}),
                "historyId": str(self.history)}

    @api
    def list_labels(self, user) -> list[dict]:
        msgs = self.messages.get(user, {}).values()

        def lab(i, name, typ):
            return {"id": i, "name": name, "type": typ, "messagesTotal": sum(i in m["labels"] for m in msgs)}

        return [lab(i, i, "system") for i in SYSTEM_LABELS] + [
            lab(i, n, "user") for i, n in self.labels.get(user, {}).items()
        ]

    @api
    def create_label(self, user, name) -> str:
        labels = self.labels.setdefault(user, {})
        for i, n in labels.items():
            if n.lower() == name.lower():
                return i
        return self.add_label(user, name)

    @api
    def list_messages(self, user) -> list[tuple[str, str]]:
        msgs = self.messages.get(user, {}).items()
        return [(i, m["thread"]) for i, m in reversed(msgs) if not {"SPAM", "TRASH"} & set(m["labels"])]

    @api
    def get_raw(self, user, msg_id) -> RawMessage:
        m = self._get(user, msg_id)
        return RawMessage(m["raw"], list(m["labels"]), m["thread"])

    @api
    def get_header_and_labels(self, user, msg_id) -> tuple[str | None, list[str]]:
        m = self._get(user, msg_id)
        return BytesHeaderParser().parsebytes(m["raw"]).get("Message-ID"), list(m["labels"])

    @api
    def insert(self, user, raw, label_ids, thread_id) -> tuple[str, str]:
        if any(i not in SYSTEM_LABELS and i not in self.labels.get(user, {}) for i in label_ids):
            raise ApiError("http_400", False)
        msg_id = self.add_message(user, raw, label_ids, thread_id)
        return msg_id, self.messages[user][msg_id]["thread"]


class FakeCalendar(_Fake):
    def __init__(self):
        super().__init__()
        self.cals = {}  # user -> {cal_id: {"meta": dict, "events": {id: event}, "acl": [rule]}}

    def add_calendar(self, user, cal_id, summary="", primary=False, access_role="owner", time_zone="UTC"):
        meta = {"id": cal_id, "summary": summary, "timeZone": time_zone, "primary": primary,
                "accessRole": access_role}
        self.cals.setdefault(user, {})[cal_id] = {"meta": meta, "events": {}, "acl": []}

    def add_event(self, user, cal_id, event: dict) -> str:
        ev = copy.deepcopy(event)
        ev.setdefault("id", self._id("ev"))
        ev.setdefault("updated", self._stamp())
        self._cal(user, cal_id)["events"][ev["id"]] = ev
        return ev["id"]

    def add_acl(self, user, cal_id, rule):
        self._cal(user, cal_id)["acl"].append(copy.deepcopy(rule))

    def _stamp(self):
        """A fresh, increasing RFC 3339 'updated' value, as Google sets on every write."""
        t = datetime.datetime(2026, 1, 1) + datetime.timedelta(seconds=int(self._id("")))
        return t.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    def _cal(self, user, cal_id):
        if cal_id == "primary":
            cal_id = next((i for i, c in self.cals.get(user, {}).items() if c["meta"]["primary"]), cal_id)
        c = self.cals.get(user, {}).get(cal_id)
        if c is None:
            raise ApiError("http_404", False)
        return c

    def _event(self, user, cal_id, event_id):
        events = self._cal(user, cal_id)["events"]
        if event_id not in events:
            raise ApiError("http_404", False)
        return events

    @api
    def list_calendars(self, user) -> list[dict]:
        return [dict(c["meta"]) for c in self.cals.get(user, {}).values()]

    @api
    def create_calendar(self, user, summary, time_zone) -> str:
        cal_id = self._id("cal")
        self.add_calendar(user, cal_id, summary=summary, time_zone=time_zone)
        return cal_id

    @api
    def list_events(self, user, cal_id) -> list[dict]:
        return [copy.deepcopy(e) for e in self._cal(user, cal_id)["events"].values()
                if e.get("status") != "cancelled" or e.get("recurringEventId")]

    @api
    def import_event(self, user, cal_id, body, *, send_updates) -> str:
        body = {k: v for k, v in body.items() if k not in ("id", "updated")}
        return self.add_event(user, cal_id, body)

    @api
    def update_event(self, user, cal_id, event_id, body, *, send_updates) -> None:
        events = self._event(user, cal_id, event_id)
        events[event_id] = {**copy.deepcopy(body), "id": event_id, "updated": self._stamp()}

    @api
    def delete_event(self, user, cal_id, event_id, *, send_updates) -> None:
        del self._event(user, cal_id, event_id)[event_id]

    @api
    def list_acl(self, user, cal_id) -> list[dict]:
        return copy.deepcopy(self._cal(user, cal_id)["acl"])

    @api
    def insert_acl(self, user, cal_id, rule, *, send_notifications) -> None:
        self.add_acl(user, cal_id, rule)


class FakeDirectory(_Fake):
    def __init__(self, users=None, groups=None, aliases=None):
        super().__init__()
        self.users = set(users or ())
        self.groups = dict(groups or {})
        self.aliases = dict(aliases or {})

    @api
    def list_users(self) -> set[str]:
        return set(self.users)

    @api
    def list_groups(self) -> dict[str, set[str]]:
        return {g: set(m) for g, m in self.groups.items()}

    @api
    def list_aliases(self) -> dict[str, str]:
        return dict(self.aliases)


SINGLETON_FIELDS = ("names", "biographies", "birthdays", "genders")
SYSTEM_GROUPS = ["myContacts", "starred", "friends", "family", "coworkers", "chatBuddies", "all", "blocked"]


class FakePeople(_Fake):
    def __init__(self):
        super().__init__()
        self.contacts = {}  # user -> {resourceName: person incl. resourceName, etag}
        self.groups = {}  # user -> {resourceName: name} (user groups only)
        self.other = {}  # user -> count of other contacts

    def add_contact(self, user, person) -> str:
        res = self._id("people/c")
        self.contacts.setdefault(user, {})[res] = {**copy.deepcopy(person), "resourceName": res,
                                                   "etag": self._id("etag")}
        return res

    def add_group(self, user, name) -> str:
        res = self._id("contactGroups/")
        self.groups.setdefault(user, {})[res] = name
        return res

    def set_other_contacts(self, user, n):
        self.other[user] = n

    def _contact(self, user, resource):
        c = self.contacts.get(user, {}).get(resource)
        if c is None:
            raise ApiError("http_404", False)
        return c

    def _check(self, user, body):
        """As the real API: one value per singleton field, and memberships of groups the account has."""
        if any(len(body.get(f, [])) > 1 for f in SINGLETON_FIELDS):
            raise ApiError("http_400", False)
        known = {"contactGroups/" + g for g in SYSTEM_GROUPS} | set(self.groups.get(user, {}))
        if any(m["contactGroupMembership"]["contactGroupResourceName"] not in known
               for m in body.get("memberships", [])):
            raise ApiError("http_400", False)

    @api
    def list_contacts(self, user) -> list[dict]:
        return [copy.deepcopy(c) for c in self.contacts.get(user, {}).values()]

    @api
    def count_other_contacts(self, user) -> int:
        return self.other.get(user, 0)

    @api
    def list_groups(self, user) -> list[dict]:
        def count(res):
            return sum(any(m.get("contactGroupMembership", {}).get("contactGroupResourceName") == res
                           for m in c.get("memberships", [])) for c in self.contacts.get(user, {}).values())

        return [{"resourceName": "contactGroups/" + g, "name": g, "groupType": "SYSTEM_CONTACT_GROUP",
                 "memberCount": count("contactGroups/" + g)} for g in SYSTEM_GROUPS] + [
            {"resourceName": r, "name": n, "groupType": "USER_CONTACT_GROUP", "memberCount": count(r)}
            for r, n in self.groups.get(user, {}).items()]

    @api
    def create_group(self, user, name) -> str:
        for r, n in self.groups.get(user, {}).items():
            if n.lower() == name.lower():
                return r
        return self.add_group(user, name)

    @api
    def create_contact(self, user, body) -> tuple[str, str]:
        self._check(user, body)
        res = self.add_contact(user, body)
        return res, self.contacts[user][res]["etag"]

    @api
    def update_contact(self, user, resource, body) -> str:
        """The real API answers a missing metadata.sources with 400, and a stale metadata.sources etag with
        400 failedPrecondition."""
        c = self._contact(user, resource)
        sources = body.get("metadata", {}).get("sources")
        if not sources or sources[0]["etag"] != c["etag"]:
            raise ApiError("http_400", False)
        self._check(user, body)
        new = self._id("etag")
        fields = {k: v for k, v in copy.deepcopy(body).items() if k not in ("etag", "metadata")}
        self.contacts[user][resource] = {**fields, "resourceName": resource, "etag": new}
        return new

    @api
    def get_update_meta(self, user, resource) -> dict:
        etag = self._contact(user, resource)["etag"]
        return {"etag": etag, "metadata": {"sources": [{"type": "CONTACT", "id": resource[7:], "etag": etag}]}}


def make_apis() -> Apis:
    return Apis(src_gmail=FakeGmail(), dst_gmail=FakeGmail(), src_cal=FakeCalendar(), dst_cal=FakeCalendar(),
                dst_dir=FakeDirectory(), src_people=FakePeople(), dst_people=FakePeople())
