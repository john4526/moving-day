import csv
import re
from dataclasses import dataclass

COLUMNS = ("key", "source", "destination", "batch")


@dataclass(frozen=True)
class User:
    key: str
    source: str
    destination: str
    batch: str


class MappingError(Exception):
    def __init__(self, reason: str, row: int):
        super().__init__(f"{reason} (row {row})")
        self.reason = reason
        self.row = row


class Mapping:
    def __init__(self, users: list[User]):
        self.users = users
        self._by_key = {u.key: u for u in users}
        self._by_source = {u.source: u for u in users}

    def batch(self, name: str) -> list[User]:
        return [u for u in self.users if u.batch == name]

    def by_key(self, key: str) -> User:
        return self._by_key[key]

    def dest_for(self, source_address: str) -> str | None:
        u = self._by_source.get(source_address.strip().lower())
        return u.destination if u else None


def _bad_address(a: str) -> bool:
    return not a or a.count("@") != 1 or a.startswith("@") or a.endswith("@")


def load_mapping(path: str) -> Mapping:
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        header = [(h or "").strip() for h in reader.fieldnames or []]
        if any(c not in header for c in COLUMNS):
            raise MappingError("missing_column", 1)
        reader.fieldnames = header
        users, keys, sources, dests = [], set(), set(), set()
        for row, r in enumerate(reader, start=2):
            c = {k: (r.get(k) or "").strip() for k in COLUMNS}
            c["source"], c["destination"] = c["source"].lower(), c["destination"].lower()
            if not re.fullmatch(r"u\d{3}", c["key"]):
                raise MappingError("bad_key", row)
            if _bad_address(c["source"]) or _bad_address(c["destination"]):
                raise MappingError("bad_address", row)
            if c["key"] in keys:
                raise MappingError("duplicate_key", row)
            if c["source"] in sources:
                raise MappingError("duplicate_source", row)
            if c["destination"] in dests:
                raise MappingError("duplicate_destination", row)
            if c["source"] == c["destination"]:
                raise MappingError("same_source_and_destination", row)
            if not c["batch"]:
                raise MappingError("empty_batch", row)
            keys.add(c["key"]); sources.add(c["source"]); dests.add(c["destination"])
            users.append(User(**c))
    return Mapping(users)
