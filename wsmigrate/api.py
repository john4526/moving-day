"""The only code that talks to Google. Errors surface as ApiError carrying a status-derived reason only.

Real wrappers are first exercised in the rehearsal; see docs/rehearsal-checklist.md.
"""
import base64
import json
import threading
import time
from dataclasses import dataclass

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaInMemoryUpload


class ApiError(Exception):
    def __init__(self, reason: str, retryable: bool):
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable


def with_retry(fn, *, sleep=time.sleep, tries: int = 6):
    """Call fn; on a retryable ApiError wait 1, 2, 4... s and retry, up to `tries` retries."""
    for attempt in range(tries + 1):
        try:
            return fn()
        except ApiError as e:
            if not e.retryable or attempt == tries:
                raise
            sleep(2**attempt)


@dataclass
class RawMessage:
    raw: bytes
    label_ids: list[str]
    thread_id: str


_RATE_REASONS = {"rateLimitExceeded", "userRateLimitExceeded"}


def _to_api_error(e: HttpError) -> ApiError:
    status = int(e.resp.status)
    if status == 429:
        return ApiError("rate_limited", True)
    if status == 403:
        try:
            errors = json.loads(e.content)["error"]["errors"]
            if any(x.get("reason") in _RATE_REASONS for x in errors):
                return ApiError("rate_limited", True)
        except Exception:
            pass
    return ApiError(f"http_{status}", status >= 500)


def _execute(make_request):
    # `from None` keeps response bodies and URLs out of any traceback.
    try:
        return make_request().execute()
    except HttpError as e:
        raise _to_api_error(e) from None
    except (TimeoutError, ConnectionError):  # socket.timeout is TimeoutError
        raise ApiError("network_error", True) from None


class _Google:
    _api = _version = ""

    def __init__(self, key_file: str, scopes: list[str]):
        self._creds = service_account.Credentials.from_service_account_file(key_file, scopes=scopes)
        self._services = {}

    def _svc(self, user):
        key = (threading.get_ident(), user)
        if key not in self._services:
            self._services[key] = build(
                self._api, self._version, credentials=self._creds.with_subject(user), cache_discovery=False
            )
        return self._services[key]

    def _call(self, make_request):
        return with_retry(lambda: _execute(make_request))

    def _all(self, make_request, key):
        """make_request(page_token) -> request; returns every item across pages."""
        items, token = [], None
        while True:
            resp = self._call(lambda: make_request(token))
            items += resp.get(key, [])
            token = resp.get("nextPageToken")
            if not token:
                return items


class Gmail(_Google):
    _api, _version = "gmail", "v1"

    def _m(self, user):
        return self._svc(user).users()

    def profile(self, user) -> dict:
        p = self._call(lambda: self._m(user).getProfile(userId="me"))
        return {"messagesTotal": p["messagesTotal"], "threadsTotal": p["threadsTotal"], "historyId": p["historyId"]}

    def _label_stubs(self, user):
        return self._call(lambda: self._m(user).labels().list(userId="me")).get("labels", [])

    def list_labels(self, user) -> list[dict]:
        out = []
        for stub in self._label_stubs(user):  # list omits counts; get has them
            lab = self._call(lambda: self._m(user).labels().get(userId="me", id=stub["id"]))
            out.append({"id": lab["id"], "name": lab["name"], "type": lab["type"],
                        "messagesTotal": lab.get("messagesTotal", 0)})
        return out

    def create_label(self, user, name) -> str:
        try:
            return self._call(lambda: self._m(user).labels().create(userId="me", body={"name": name}))["id"]
        except ApiError as e:
            if e.reason != "http_409":
                raise
        for lab in self._label_stubs(user):
            if lab["name"].lower() == name.lower():
                return lab["id"]
        raise ApiError("http_409", False)

    def list_messages(self, user) -> list[tuple[str, str]]:
        msgs = self._all(
            lambda tok: self._m(user).messages().list(
                userId="me", includeSpamTrash=False, maxResults=500, pageToken=tok
            ),
            "messages",
        )
        return [(m["id"], m["threadId"]) for m in msgs]

    def get_raw(self, user, msg_id) -> RawMessage:
        m = self._call(lambda: self._m(user).messages().get(userId="me", id=msg_id, format="raw"))
        return RawMessage(base64.urlsafe_b64decode(m["raw"]), m.get("labelIds", []), m["threadId"])

    def get_header_and_labels(self, user, msg_id) -> tuple[str | None, list[str]]:
        m = self._call(
            lambda: self._m(user).messages().get(
                userId="me", id=msg_id, format="metadata", metadataHeaders=["Message-ID"]
            )
        )
        value = next(
            (h["value"] for h in m.get("payload", {}).get("headers", []) if h["name"].lower() == "message-id"),
            None,
        )
        return value, m.get("labelIds", [])

    def insert(self, user, raw, label_ids, thread_id) -> tuple[str, str]:
        body = {"labelIds": label_ids}
        if thread_id is not None:
            body["threadId"] = thread_id
        m = self._call(
            lambda: self._m(user).messages().insert(
                userId="me",
                body=body,
                media_body=MediaInMemoryUpload(raw, mimetype="message/rfc822", resumable=True),
                internalDateSource="dateHeader",
            )
        )
        return m["id"], m["threadId"]


def _require_none(send_updates):
    if send_updates != "none":
        raise ValueError("send_updates must be 'none'")


class Calendar(_Google):
    _api, _version = "calendar", "v3"

    def list_calendars(self, user) -> list[dict]:
        cals = self._all(lambda tok: self._svc(user).calendarList().list(pageToken=tok), "items")
        return [{"id": c["id"], "summary": c.get("summary", ""), "timeZone": c.get("timeZone"),
                 "primary": c.get("primary", False), "accessRole": c.get("accessRole")} for c in cals]

    def create_calendar(self, user, summary, time_zone) -> str:
        body = {"summary": summary, "timeZone": time_zone}
        return self._call(lambda: self._svc(user).calendars().insert(body=body))["id"]

    def list_events(self, user, cal_id) -> list[dict]:
        return self._all(
            lambda tok: self._svc(user).events().list(
                calendarId=cal_id, showDeleted=False, singleEvents=False, maxResults=2500, pageToken=tok
            ),
            "items",
        )

    def import_event(self, user, cal_id, body, *, send_updates: str) -> str:
        _require_none(send_updates)
        # events.import takes no sendUpdates parameter (the client rejects it); import never notifies.
        return self._call(lambda: self._svc(user).events().import_(calendarId=cal_id, body=body))["id"]

    def update_event(self, user, cal_id, event_id, body, *, send_updates: str) -> None:
        _require_none(send_updates)
        self._call(lambda: self._svc(user).events().update(
            calendarId=cal_id, eventId=event_id, body=body, sendUpdates=send_updates))

    def delete_event(self, user, cal_id, event_id, *, send_updates: str) -> None:
        _require_none(send_updates)
        self._call(lambda: self._svc(user).events().delete(
            calendarId=cal_id, eventId=event_id, sendUpdates=send_updates))

    def list_acl(self, user, cal_id) -> list[dict]:
        rules = self._all(lambda tok: self._svc(user).acl().list(calendarId=cal_id, pageToken=tok), "items")
        return [{"role": r["role"], "scope": r["scope"]} for r in rules]

    def insert_acl(self, user, cal_id, rule, *, send_notifications: bool) -> None:
        if send_notifications is not False:
            raise ValueError("send_notifications must be False")
        self._call(lambda: self._svc(user).acl().insert(
            calendarId=cal_id, body=rule, sendNotifications=send_notifications))


class Directory(_Google):
    _api, _version = "admin", "directory_v1"
    SCOPES = [
        "https://www.googleapis.com/auth/admin.directory.user.readonly",
        "https://www.googleapis.com/auth/admin.directory.group.readonly",
    ]

    def __init__(self, key_file: str, admin: str):
        super().__init__(key_file, self.SCOPES)
        self._admin = admin

    def _users(self):
        return self._all(
            lambda tok: self._svc(self._admin).users().list(customer="my_customer", maxResults=500, pageToken=tok),
            "users",
        )

    def list_users(self) -> set[str]:
        return {u["primaryEmail"].lower() for u in self._users()}

    def list_groups(self) -> dict[str, set[str]]:
        d = self._svc(self._admin)
        groups = self._all(lambda tok: d.groups().list(customer="my_customer", pageToken=tok), "groups")
        return {
            g["email"].lower(): {
                m["email"].lower()
                for m in self._all(lambda tok: d.members().list(groupKey=g["id"], pageToken=tok), "members")
                if "email" in m
            }
            for g in groups
        }

    def list_aliases(self) -> dict[str, str]:
        return {a.lower(): u["primaryEmail"].lower() for u in self._users() for a in u.get("aliases", [])}
