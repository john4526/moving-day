import re
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "movingday"
SOURCES = {p: p.read_text().splitlines() for p in PKG.rglob("*.py")}


def _owner(path, n):
    before = SOURCES[path][:n]
    cls = [l for l in before if l.startswith("class ")][-1].split("(")[0].split()[1]
    fn = [l for l in before if l.lstrip().startswith("def ")][-1].strip().split("(")[0].split()[1]
    return path.name, cls, fn


def test_no_delete_trash_or_modify_in_package():
    hits = [(_owner(p, n), l.strip()) for p, lines in SOURCES.items() for n, l in enumerate(lines)
            if re.search(r"\.(delete|trash|untrash|modify|batchDelete|batchModify)\(", l)]
    assert sorted(hits) == [
        (("api.py", "Calendar", "delete_event"), hits[0][1]),
        (("api.py", "Drive", "remove_permission"), hits[1][1]),
    ]
    assert ".events().delete(" in hits[0][1] and ".permissions().delete(" in hits[1][1]


def test_trashed_true_only_in_drive_trash():
    hits = [_owner(p, n) for p, lines in SOURCES.items() for n, l in enumerate(lines) if '"trashed": True' in l]
    assert hits == [("api.py", "Drive", "trash")]


def test_no_print_or_logging_in_package():
    assert SOURCES  # the scan found the package
    for p, lines in SOURCES.items():
        for l in lines:
            assert not re.search(r"\bprint\(", l), p.name
            assert not re.search(r"^\s*(import|from) logging\b", l), p.name
