"""The only code that talks to Google. Errors surface as ApiError carrying a status-derived reason only.

Real wrappers are first exercised in the rehearsal; see docs/rehearsal-checklist.md.
The People (contacts) wrapper is first exercised in the rehearsal too.
"""
import base64
import http.client
import io
import json
import ssl
import threading
import time
from dataclasses import dataclass

import httplib2
from google.auth.exceptions import TransportError
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import HttpRequest, MediaInMemoryUpload, MediaIoBaseDownload


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
            if any(x.get("reason") == "exportSizeLimitExceeded" for x in errors):
                return ApiError("export_size_limit", False)
        except Exception:
            pass
    return ApiError(f"http_{status}", status >= 500)


def _execute(make_request):
    # `from None` keeps response bodies and URLs out of any traceback.
    try:
        return make_request().execute()
    except HttpError as e:
        raise _to_api_error(e) from None
    # socket.timeout is TimeoutError
    except (TimeoutError, ConnectionError, http.client.HTTPException, httplib2.ServerNotFoundError, ssl.SSLError,
            TransportError):
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
        cals = self._all(lambda tok: self._svc(user).calendarList().list(showHidden=True, pageToken=tok), "items")
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


PERSON_FIELDS = ("names", "emailAddresses", "phoneNumbers", "addresses", "organizations", "biographies",
                 "birthdays", "events", "urls", "relations", "nicknames", "memberships")


class People(_Google):
    """Label membership is written through the `memberships` field of create/update; there is no
    contactGroups.members call. Nothing here deletes."""
    _api, _version = "people", "v1"

    def list_contacts(self, user) -> list[dict]:
        fields = ",".join(PERSON_FIELDS + ("metadata",))
        return self._all(lambda tok: self._svc(user).people().connections().list(
            resourceName="people/me", personFields=fields, sources=["READ_SOURCE_TYPE_CONTACT"], pageSize=1000,
            pageToken=tok), "connections")

    def count_other_contacts(self, user) -> int:
        return len(self._all(lambda tok: self._svc(user).otherContacts().list(
            readMask="metadata", pageSize=1000, pageToken=tok), "otherContacts"))

    def list_groups(self, user) -> list[dict]:
        groups = self._all(lambda tok: self._svc(user).contactGroups().list(pageSize=1000, pageToken=tok),
                           "contactGroups")
        return [{"resourceName": g["resourceName"], "name": g.get("name", ""), "groupType": g.get("groupType"),
                 "memberCount": g.get("memberCount", 0)} for g in groups]

    def create_group(self, user, name) -> str:
        try:
            return self._call(lambda: self._svc(user).contactGroups().create(
                body={"contactGroup": {"name": name}}))["resourceName"]
        except ApiError as e:
            if e.reason != "http_409":
                raise
        for g in self.list_groups(user):
            if g["groupType"] == "USER_CONTACT_GROUP" and g["name"].lower() == name.lower():
                return g["resourceName"]
        raise ApiError("http_409", False)

    def create_contact(self, user, body) -> tuple[str, str]:
        # ponytail: with_retry can repeat a POST whose response was lost, creating a duplicate contact;
        # compare reports it as extra.
        p = self._call(lambda: self._svc(user).people().createContact(body=body, personFields="metadata"))
        return p["resourceName"], p["etag"]

    def update_contact(self, user, resource, body) -> str:
        """body must carry what get_update_meta returns (etag and metadata.sources)."""
        return self._call(lambda: self._svc(user).people().updateContact(
            resourceName=resource, updatePersonFields=",".join(PERSON_FIELDS), personFields="metadata",
            body=body))["etag"]

    def get_update_meta(self, user, resource) -> dict:
        """{"etag", "metadata": {"sources": [CONTACT source]}}: updateContact answers 400 without
        metadata.sources, and 400 failedPrecondition when that source's etag is stale."""
        p = self._call(lambda: self._svc(user).people().get(resourceName=resource, personFields="metadata"))
        src = next((x for x in p.get("metadata", {}).get("sources", []) if x.get("type") == "CONTACT"), None)
        if src is None:
            raise ApiError("no_contact_source", False)
        return {"etag": p["etag"], "metadata": {"sources": [{k: src.get(k) for k in ("type", "id", "etag")}]}}


CHUNK = 8 * 1024 * 1024  # a multiple of 256 KiB, as resumable uploads require
SRC_KEY = "movingday_src"
FOLDER = "application/vnd.google-apps.folder"
_ITEM = "id,mimeType,parents,version,modifiedTime,createdTime,md5Checksum,size,name,description,starred"


class _Step:
    """Lets _call/_execute drive any callable that raises HttpError, e.g. a download's next_chunk."""

    def __init__(self, fn):
        self.execute = fn


def _slices(data: bytes):
    for i in range(0, len(data), CHUNK):
        yield data[i:i + CHUNK]


class Drive(_Google):
    """File content is passed through as chunks of at most CHUNK bytes, never joined and never on disk.
    Nothing here deletes a file; remove_permission and trash are the only removals."""
    _api, _version = "drive", "v3"

    def _f(self, user):
        return self._svc(user).files()

    def _raw(self, user, url, method="GET", body=None, headers=None, ok=(200,)):
        """One authorised HTTP request; returns (response, content). Used where googleapiclient cannot
        stream (export links, uploads of unknown length)."""
        def once():
            try:
                resp, content = self._svc(user)._http.request(url, method, body=body, headers=headers or {})
            except (TimeoutError, ConnectionError, http.client.HTTPException, httplib2.ServerNotFoundError,
                    ssl.SSLError, TransportError):
                raise ApiError("network_error", True) from None
            if resp.status not in ok:
                raise _to_api_error(HttpError(resp, content))
            return resp, content

        return with_retry(once)

    def root_id(self, user) -> str:
        return self._call(lambda: self._f(user).get(fileId="root", fields="id"))["id"]

    def list_owned(self, user) -> list[dict]:
        return self._all(lambda tok: self._f(user).list(
            q="'me' in owners and trashed=false", fields=f"nextPageToken,files({_ITEM})", pageSize=1000,
            pageToken=tok), "files")

    def list_shared_owners(self, user) -> list[str]:
        files = self._all(lambda tok: self._f(user).list(
            q="not 'me' in owners and trashed=false", fields="nextPageToken,files(owners(emailAddress))",
            pageSize=1000, pageToken=tok), "files")
        return [o["emailAddress"] for f in files for o in f.get("owners", [])[:1] if "emailAddress" in o]

    def list_permissions(self, user, file_id) -> list[dict]:
        return self._all(lambda tok: self._svc(user).permissions().list(
            fileId=file_id, fields="nextPageToken,permissions(id,role,type,emailAddress,domain,allowFileDiscovery)",
            pageSize=100, pageToken=tok), "permissions")

    def _stream(self, req):
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, req, chunksize=CHUNK)
        done = False
        while not done:
            _, done = self._call(lambda: _Step(dl.next_chunk))
            data = buf.getvalue()
            buf.seek(0)
            buf.truncate()
            if data:
                yield data

    def download(self, user, file_id):
        yield from self._stream(self._f(user).get_media(fileId=file_id))

    def export(self, user, file_id, mime):
        try:
            data = self._call(lambda: self._f(user).export(fileId=file_id, mimeType=mime))
        except ApiError as e:  # the normal export is capped by Google (10 MB), so it is held whole
            if e.reason != "export_size_limit":
                raise
        else:
            yield from _slices(data)
            return
        # ponytail: the link is streamed in CHUNK ranges; Range support on export links is confirmed in rehearsal.
        try:
            link = self._call(lambda: self._f(user).get(fileId=file_id, fields="exportLinks")
                              ).get("exportLinks", {}).get(mime)
            if not link:
                raise ApiError("export_too_large", False)
            yield from self._stream(HttpRequest(self._svc(user)._http, lambda resp, content: content, link))
        except ApiError:
            raise ApiError("export_too_large", False) from None

    def create_folder(self, user, meta) -> str:
        body = {**meta, "mimeType": FOLDER}
        return self._call(lambda: self._f(user).create(body=body, fields="id"))["id"]

    def _session(self, user, method, url, meta, mime) -> str:
        resp, _ = self._raw(user, url + "&fields=id", method, json.dumps(meta),
                            {"Content-Type": "application/json; charset=UTF-8", "X-Upload-Content-Type": mime})
        return resp["location"]

    def _send(self, user, loc, chunks) -> str:
        """Resumable upload of unknown length: one chunk of lookahead tells which one is last."""
        it = iter(chunks)
        cur, start = next(it, b""), 0
        while True:
            nxt = next(it, None)
            end = start + len(cur)
            rng = (f"bytes {start}-{end - 1}/" if cur else "bytes */") + (str(end) if nxt is None else "*")
            _, content = self._raw(user, loc, "PUT", cur, {"Content-Range": rng, "Content-Length": str(len(cur))},
                                   ok=(200, 201) if nxt is None else (308,))
            if nxt is None:
                return json.loads(content)["id"]
            cur, start = nxt, end

    def upload(self, user, meta, chunks, mime, convert) -> tuple[str, bool]:
        """chunks is a factory returning a fresh iterator, so a refused conversion can be retried unconverted."""
        url = "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable"
        if convert:
            try:
                return self._send(user, self._session(user, "POST", url, meta, mime), chunks()), True
            except ApiError as e:  # refusal: 400, or 403 for a reason other than rate limiting (those retried)
                if e.retryable or e.reason not in ("http_400", "http_403"):
                    raise
        loc = self._session(user, "POST", url, {**meta, "mimeType": mime}, mime)
        return self._send(user, loc, chunks()), False

    def update_content(self, user, file_id, chunks, mime) -> None:
        url = f"https://www.googleapis.com/upload/drive/v3/files/{file_id}?uploadType=resumable"
        self._send(user, self._session(user, "PATCH", url, {}, mime), chunks)

    def update_meta(self, user, file_id, meta, add_parents="", remove_parents="") -> None:
        if "trashed" in meta:
            raise ValueError("use trash()")
        self._call(lambda: self._f(user).update(fileId=file_id, body=meta, addParents=add_parents or None,
                                                removeParents=remove_parents or None, fields="id"))

    def find_by_src(self, user, src_id) -> str | None:
        files = self._call(lambda: self._f(user).list(
            q=f"appProperties has {{ key='{SRC_KEY}' and value='{src_id}' }} and trashed=false",
            fields="files(id)", pageSize=1)).get("files", [])
        return files[0]["id"] if files else None

    def list_created(self, user) -> list[dict]:
        return self._all(lambda tok: self._f(user).list(
            q="trashed=false", fields="nextPageToken,files(id,mimeType,parents,md5Checksum,appProperties)",
            pageSize=1000, pageToken=tok), "files")

    def add_permission(self, user, file_id, perm) -> str:
        return self._call(lambda: self._svc(user).permissions().create(
            fileId=file_id, body=perm, sendNotificationEmail=False, fields="id"))["id"]

    def remove_permission(self, user, file_id, perm_id) -> None:
        self._call(lambda: self._svc(user).permissions().delete(fileId=file_id, permissionId=perm_id))

    def trash(self, user, file_id) -> None:
        self._call(lambda: self._f(user).update(fileId=file_id, body={"trashed": True}, fields="id"))


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
